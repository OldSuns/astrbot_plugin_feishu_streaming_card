"""
飞书流式卡片增强插件

将 LLM 流式输出渲染为持续更新的飞书卡片
"""
import asyncio
import importlib.util
import inspect
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api import logger

try:
    # AstrBot 正常按插件包加载时走这里，模块名形如：
    # astrbot_plugin_feishu_streaming_card.core.patch
    # 避免污染/复用全局顶层 core.patch，防止热重载时继续执行旧模块。
    from .core import (
        CardSession,
        ToolCall,
        SessionManager,
        render_card,
        StreamingTextNormalizer,
        StreamingPatch,
        RateLimiter,
    )
except ImportError:
    # 兼容 AstrBot 或本地测试通过文件方式加载 main.py 的场景。
    # 不再把插件目录插入 sys.path 后 `from core import ...`，因为那会把
    # 子包注册成全局 `core`，多个插件/热重载时容易命中旧的 core.patch。
    _plugin_dir = Path(__file__).resolve().parent
    _plugin_pkg_name = _plugin_dir.name
    _core_name = f"{_plugin_pkg_name}.core"
    _core_init = _plugin_dir / "core" / "__init__.py"
    _core_spec = importlib.util.spec_from_file_location(
        _core_name,
        _core_init,
        submodule_search_locations=[str(_plugin_dir / "core")],
    )
    if _core_spec is None or _core_spec.loader is None:
        raise
    _core_module = importlib.util.module_from_spec(_core_spec)
    sys.modules[_core_name] = _core_module
    _core_spec.loader.exec_module(_core_module)

    CardSession = _core_module.CardSession
    ToolCall = _core_module.ToolCall
    SessionManager = _core_module.SessionManager
    render_card = _core_module.render_card
    StreamingTextNormalizer = _core_module.StreamingTextNormalizer
    StreamingPatch = _core_module.StreamingPatch
    RateLimiter = _core_module.RateLimiter


@register(
    "astrbot_plugin_feishu_streaming_card",
    "AstrBot Contributors",
    "将 LLM 流式输出渲染为持续更新的飞书卡片",
    "v0.1.0"
)
class FeishuStreamingCardPlugin(Star):
    """飞书流式卡片插件"""

    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        self.config = config or {}

        # 初始化组件
        self.session_manager = SessionManager(
            max_sessions=self.config.get("max_sessions", 100),
            ttl=self.config.get("session_ttl", 3600)
        )
        self.rate_limiter = RateLimiter(
            min_interval=self.config.get("update_interval", 0.05)
        )

        # 并发控制：per-session 锁
        self.session_locks: Dict[str, asyncio.Lock] = {}
        self._locks_manager_lock = asyncio.Lock()

        # 调试模式
        self.debug_mode = self.config.get("debug_mode", False)

        # 安装/卸载 Monkey Patch
        if self.config.get("uninstall_patch", False):
            if StreamingPatch.uninstall():
                logger.info("[飞书流式卡片] 已按配置卸载 Patch")
            else:
                logger.warning("[飞书流式卡片] Patch 未安装或卸载失败")
        elif self.config.get("enabled", True):
            self._install_patch()
        else:
            StreamingPatch.uninstall()
            logger.info("[飞书流式卡片] 插件已禁用")

    async def terminate(self):
        """插件卸载/停用时恢复原生 send_streaming。"""
        StreamingPatch.uninstall()

    @staticmethod
    async def _maybe_await(value):
        """兼容 lark-oapi 同步/异步 API 返回值。"""
        if inspect.isawaitable(value):
            return await value
        return value

    async def _call_lark_message_api(self, message_api, method_name: str, request):
        """调用 lark-oapi message API，优先使用异步方法。"""
        async_method = getattr(message_api, f"a{method_name}", None)
        if async_method is not None:
            return await self._maybe_await(async_method(request))

        method = getattr(message_api, method_name)
        return await self._maybe_await(method(request))

    async def _call_lark_api(self, api_obj, method_name: str, request):
        """调用 lark-oapi API，优先使用异步 a* 方法。"""
        async_method = getattr(api_obj, f"a{method_name}", None)
        if async_method is not None:
            return await self._maybe_await(async_method(request))

        method = getattr(api_obj, method_name)
        return await self._maybe_await(method(request))

    @staticmethod
    def _response_success(response) -> bool:
        success = getattr(response, 'success', None)
        if callable(success):
            return bool(success())
        return getattr(response, 'code', 0) == 0

    @staticmethod
    def _response_error(response) -> str:
        return f"{getattr(response, 'code', 'unknown')} - {getattr(response, 'msg', '')}"

    @staticmethod
    def _chunk_to_text(chunk) -> str:
        """从 AstrBot send_streaming chunk 中提取纯文本。

        AstrBot Lark 的 send_streaming generator 产出通常是 MessageChain，
        不是字符串。这里只抽取 Plain.text；工具边界等非文本 chunk 返回空串。
        """
        if chunk is None:
            return ""
        if isinstance(chunk, str):
            return chunk

        chain = getattr(chunk, 'chain', None)
        if isinstance(chain, list):
            parts = []
            for comp in chain:
                text = getattr(comp, 'text', None)
                if text:
                    parts.append(str(text))
            return ''.join(parts)

        text = getattr(chunk, 'text', None)
        if text:
            return str(text)

        return ""

    def _build_streaming_card(self, session: CardSession) -> dict:
        """构建 CardKit streaming 初始卡片。"""
        status_info = {
            "thinking": ("思考中...", "indigo"),
            "completed": ("✓ 完成", "green"),
            "failed": ("✗ 处理失败", "red"),
        }.get(session.status, ("思考中...", "indigo"))

        return {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "update_multi": True,
                "summary": {"content": status_info[0]},
                "streaming_config": {
                    "print_frequency_ms": {
                        "default": int(
                            self.config.get("streaming_print_frequency_ms", 20)
                        )
                    },
                    "print_step": {
                        "default": int(self.config.get("streaming_print_step", 24))
                    },
                    "print_strategy": "fast",
                },
            },
            "header": {
                "template": status_info[1],
                "title": {"tag": "plain_text", "content": "AstrBot"},
                "subtitle": {"tag": "plain_text", "content": status_info[0]},
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": session.answer_text or "",
                        "element_id": "markdown_1",
                    }
                ]
            },
        }

    @staticmethod
    def _apply_card_header(card: dict, session: CardSession) -> dict:
        """给原生 CardKit 初始卡片注入插件标题区域。"""
        status_info = {
            "thinking": ("思考中...", "indigo"),
            "completed": ("✓ 完成", "green"),
            "failed": ("✗ 处理失败", "red"),
        }.get(session.status, ("思考中...", "indigo"))

        card.setdefault("config", {})
        card["config"].setdefault("summary", {})["content"] = status_info[0]
        card["header"] = {
            "template": status_info[1],
            "title": {"tag": "plain_text", "content": "AstrBot"},
            "subtitle": {"tag": "plain_text", "content": status_info[0]},
        }
        return card

    def _mutate_card_json_text(self, text: str, session: CardSession) -> str:
        """如果字符串是飞书卡片 JSON，则注入标题后返回。"""
        try:
            data = json.loads(text)
        except Exception:
            return text

        if not isinstance(data, dict):
            return text
        if "body" not in data and "schema" not in data and "config" not in data:
            return text

        self._apply_card_header(data, session)
        return json.dumps(data, ensure_ascii=False)

    def _mutate_card_request_object(self, obj, session: CardSession, seen=None) -> bool:
        """递归修改 lark-oapi request 中的 card_json data/content 字段。"""
        if obj is None:
            return False
        if seen is None:
            seen = set()

        obj_id = id(obj)
        if obj_id in seen:
            return False
        seen.add(obj_id)

        changed = False
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if isinstance(value, str):
                    new_value = self._mutate_card_json_text(value, session)
                    if new_value != value:
                        obj[key] = new_value
                        changed = True
                elif self._mutate_card_request_object(value, session, seen):
                    changed = True
            return changed

        if isinstance(obj, list):
            for i, value in enumerate(list(obj)):
                if isinstance(value, str):
                    new_value = self._mutate_card_json_text(value, session)
                    if new_value != value:
                        obj[i] = new_value
                        changed = True
                elif self._mutate_card_request_object(value, session, seen):
                    changed = True
            return changed

        if isinstance(obj, (str, bytes, int, float, bool)):
            return False

        attrs = getattr(obj, '__dict__', None)
        if not isinstance(attrs, dict):
            return False

        for key, value in list(attrs.items()):
            if isinstance(value, str):
                new_value = self._mutate_card_json_text(value, session)
                if new_value != value:
                    try:
                        setattr(obj, key, new_value)
                        changed = True
                    except Exception:
                        pass
            elif self._mutate_card_request_object(value, session, seen):
                changed = True

        return changed

    def _install_patch(self):
        """安装 Monkey Patch"""
        force = self.config.get("force_patch", False)

        if force:
            logger.warning("[飞书流式卡片] 强制安装模式已启用")

        success = StreamingPatch.install(self._handle_streaming)

        if not success and force:
            logger.warning("[飞书流式卡片] 强制安装失败，可能存在兼容性问题")

    async def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """
        获取会话级别的锁

        Args:
            session_key: 会话键

        Returns:
            该会话的异步锁
        """
        async with self._locks_manager_lock:
            if session_key not in self.session_locks:
                self.session_locks[session_key] = asyncio.Lock()
                if self.debug_mode:
                    logger.debug(f"[飞书流式卡片] 创建会话锁: {session_key}")
            return self.session_locks[session_key]

    def _cleanup_session_lock(self, session_key: str):
        """
        清理会话锁

        Args:
            session_key: 会话键
        """
        if session_key in self.session_locks:
            del self.session_locks[session_key]
            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 清理会话锁: {session_key}")

    async def _handle_streaming(self, event, generator, *args, **kwargs):
        """
        核心流式处理逻辑

        Args:
            event: LarkMessageEvent 实例
            generator: 流式输出的异步生成器

        Returns:
            流式输出的最终文本
        """
        # 检查是否是飞书平台
        if not self._is_lark_event(event):
            # 非飞书平台，使用原生流式
            if self.debug_mode:
                logger.debug("[飞书流式卡片] 非飞书平台，跳过")
            return await self._fallback_streaming(event, generator)

        success = False
        try:
            # 创建会话
            session_key = self._make_session_key(event)
            session = self.session_manager.get_or_create(
                session_key,
                conversation_id=event.session_id,
                message_id=getattr(event.message_obj, 'message_id', 'unknown'),
                chat_id=getattr(event.message_obj, 'chat_id', 'unknown')
            )

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 创建会话: {session_key}")

            # 正文流式输出完全交给 AstrBot/Lark 原生实现，插件只旁路捕获文本。
            result = await self._native_streaming_with_capture(
                event,
                generator,
                session,
                *args,
                **kwargs,
            )

            # 完成标记
            session.status = "completed"
            await self._update_card(event, session, force=True)
            success = True

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 会话完成: {session_key}")

            return result if result is not None else session.answer_text

        except Exception as e:
            logger.error(f"[飞书流式卡片] 处理失败: {e}", exc_info=True)

            # 标记失败状态
            if 'session' in locals() and session:
                session.status = "failed"
                try:
                    await self._update_card(event, session, force=True)
                except Exception:
                    pass

            # 降级到原生流式
            return await self._fallback_streaming(event, generator)
        finally:
            if 'session_key' in locals():
                if not success:
                    self.session_manager.remove(session_key)
                self._cleanup_session_lock(session_key)

    async def _stream_updates(self, event, session: CardSession, generator):
        """
        流式更新卡片

        Args:
            event: LarkMessageEvent 实例
            session: 卡片会话
            generator: 流式输出的异步生成器
        """
        normalizer = StreamingTextNormalizer()

        async for chunk in generator:
            if not chunk:
                continue

            chunk_text = self._chunk_to_text(chunk)
            if not chunk_text:
                continue

            # 累积文本
            session.answer_text = normalizer.feed(chunk_text)

            await self._update_card(event, session)

        # 最终归一化
        session.answer_text = normalizer.finalize()

    def _get_original_send_streaming(self, event):
        """获取被 patch 前的飞书原生 send_streaming。"""
        original = getattr(StreamingPatch, '_original_send_streaming', None)
        if original is not None:
            return original

        current = getattr(type(event), 'send_streaming', None)
        extractor = getattr(StreamingPatch, '_extract_original_send_streaming', None)
        if current is not None and extractor is not None:
            return extractor(current)
        return None

    @staticmethod
    def _capture_lark_ids_from_response(response) -> dict[str, str]:
        """从 lark-oapi response/data 中提取 message_id/card_id。"""
        captured: dict[str, str] = {}
        for obj in (response, getattr(response, 'data', None)):
            if obj is None:
                continue
            message_id = getattr(obj, 'message_id', None)
            card_id = getattr(obj, 'card_id', None)
            if message_id:
                captured['message_id'] = message_id
            if card_id:
                captured['card_id'] = card_id
        return captured

    def _wrap_native_lark_methods(self, event, session: CardSession):
        """临时包装原生流式内部方法，仅用于捕获最终 message_id/card_id。"""
        captured: dict[str, str] = {}
        restore: list[tuple[Any, str, Any]] = []

        def remember(values: dict[str, str]) -> None:
            if values.get('message_id'):
                captured['message_id'] = values['message_id']
                session.feishu_message_id = values['message_id']
            if values.get('card_id'):
                captured['card_id'] = values['card_id']
                session.card_id = values['card_id']

        def make_wrapper(name: str, original):
            async def wrapper(*args, **kwargs):
                if name in ('_create_streaming_card', 'card_create'):
                    for arg in args:
                        self._mutate_card_request_object(arg, session)
                    for value in kwargs.values():
                        self._mutate_card_request_object(value, session)

                if name in (
                    '_send_card_message',
                    '_send_message',
                    'message_create',
                    'message_reply',
                ):
                    for arg in args:
                        if isinstance(arg, str) and arg.startswith('card'):
                            remember({'card_id': arg})

                result = original(*args, **kwargs)
                result = await self._maybe_await(result)

                values = self._capture_lark_ids_from_response(result)
                if isinstance(result, str):
                    if name in ('_create_streaming_card', 'card_create'):
                        values['card_id'] = result
                    elif name in (
                        '_send_card_message',
                        '_send_message',
                        'message_create',
                        'message_reply',
                    ):
                        values['message_id'] = result
                remember(values)
                return result

            return wrapper

        for name in (
            '_create_streaming_card',
            '_send_card_message',
            '_send_message',
            '_close_streaming_mode',
        ):
            original = getattr(event, name, None)
            if callable(original):
                restore.append((event, name, original))
                setattr(event, name, make_wrapper(name, original))

        # 部分 AstrBot 版本不经过 event._create_streaming_card，而是直接调用
        # lark_client.cardkit.v1.card.create/acreate 和 im.v1.message.*。
        # 因此这里再临时包装底层 API：开始时注入 header，结束时才能捕获
        # message_id 进行最终状态/footer patch。
        lark_client = getattr(event, 'bot', None)

        card_api = getattr(
            getattr(getattr(lark_client, 'cardkit', None), 'v1', None),
            'card',
            None,
        )
        if card_api is not None:
            for method_name in ('create', 'acreate'):
                original = getattr(card_api, method_name, None)
                if callable(original):
                    restore.append((card_api, method_name, original))
                    setattr(card_api, method_name, make_wrapper('card_create', original))

        message_api = getattr(
            getattr(getattr(lark_client, 'im', None), 'v1', None),
            'message',
            None,
        )
        if message_api is not None:
            for method_name, wrapper_name in (
                ('create', 'message_create'),
                ('acreate', 'message_create'),
                ('reply', 'message_reply'),
                ('areply', 'message_reply'),
            ):
                original = getattr(message_api, method_name, None)
                if callable(original):
                    restore.append((message_api, method_name, original))
                    setattr(message_api, method_name, make_wrapper(wrapper_name, original))

        return captured, restore

    async def _native_streaming_with_capture(
        self,
        event,
        generator,
        session: CardSession,
        *args,
        **kwargs,
    ):
        """调用飞书原生流式输出，同时旁路捕获文本用于最终状态卡片。"""
        original_send_streaming = self._get_original_send_streaming(event)
        if original_send_streaming is None:
            await self._stream_updates(event, session, generator)
            return session.answer_text

        normalizer = StreamingTextNormalizer()

        async def capture_generator():
            async for chunk in generator:
                text = self._chunk_to_text(chunk)
                if text:
                    session.answer_text = normalizer.feed(text)
                yield chunk
            session.answer_text = normalizer.finalize()

        _captured, restore = self._wrap_native_lark_methods(event, session)
        try:
            result = await original_send_streaming(
                event,
                capture_generator(),
                *args,
                **kwargs,
            )
        finally:
            for target, name, original in restore:
                setattr(target, name, original)

        if not session.answer_text and isinstance(result, str):
            session.answer_text = result

        return result

    async def _create_streaming_card(self, event, session: CardSession) -> str:
        """创建 CardKit 流式卡片实体。"""
        from lark_oapi.api.cardkit.v1 import CreateCardRequest, CreateCardRequestBody

        lark_client = event.bot
        if getattr(lark_client, 'cardkit', None) is None:
            raise RuntimeError("Lark API client cardkit module is not initialized")

        request = CreateCardRequest.builder().request_body(
            CreateCardRequestBody.builder()
            .type("card_json")
            .data(json.dumps(self._build_streaming_card(session), ensure_ascii=False))
            .build()
        ).build()

        response = await self._call_lark_api(lark_client.cardkit.v1.card, "create", request)
        if not self._response_success(response):
            raise Exception(f"Lark CardKit create error: {self._response_error(response)}")
        if response.data is None or not getattr(response.data, 'card_id', None):
            raise Exception("Lark CardKit create succeeded but card_id is missing")

        return response.data.card_id

    async def _send_initial_card(self, event, session: CardSession) -> str:
        """
        发送初始卡片

        Args:
            event: LarkMessageEvent 实例
            session: 卡片会话

        Returns:
            飞书消息 ID
        """
        # 调用飞书 API
        lark_client = event.bot
        chat_id = session.chat_id

        try:
            card_id = await self._create_streaming_card(event, session)
            session.card_id = card_id

            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )

            original_message_id = getattr(event.message_obj, 'message_id', None)
            card_content = json.dumps(
                {"type": "card", "data": {"card_id": card_id}},
                ensure_ascii=False,
            )

            if original_message_id and original_message_id != 'unknown':
                request = ReplyMessageRequest.builder() \
                    .message_id(original_message_id) \
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(card_content)
                        .uuid(str(uuid.uuid4()))
                        .reply_in_thread(False)
                        .build()
                    ).build()
                response = await self._call_lark_message_api(
                    lark_client.im.v1.message,
                    "reply",
                    request,
                )
            else:
                request = CreateMessageRequest.builder() \
                    .receive_id_type("chat_id") \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("interactive")
                        .content(card_content)
                        .uuid(str(uuid.uuid4()))
                        .build()
                    ).build()
                response = await self._call_lark_message_api(
                    lark_client.im.v1.message,
                    "create",
                    request,
                )

            if not self._response_success(response):
                raise Exception(f"Lark API error: {self._response_error(response)}")

            message_id = response.data.message_id

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 发送卡片成功: {message_id}")

            return message_id

        except Exception as e:
            logger.error(f"[飞书流式卡片] 发送卡片失败: {e}", exc_info=True)
            raise

    async def _update_streaming_text(self, event, session: CardSession) -> bool:
        """使用 CardKit streaming 接口更新正文文本。"""
        if not session.card_id:
            return False

        from lark_oapi.api.cardkit.v1 import (
            ContentCardElementRequest,
            ContentCardElementRequestBody,
        )

        lark_client = event.bot
        if getattr(lark_client, 'cardkit', None) is None:
            return False

        session.card_sequence += 1
        request = ContentCardElementRequest.builder() \
            .card_id(session.card_id) \
            .element_id("markdown_1") \
            .request_body(
                ContentCardElementRequestBody.builder()
                .content(session.answer_text or "")
                .sequence(session.card_sequence)
                .uuid(str(uuid.uuid4()))
                .build()
            ).build()

        response = await self._call_lark_api(
            lark_client.cardkit.v1.card_element,
            "content",
            request,
        )
        if not self._response_success(response):
            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 流式文本更新失败: {self._response_error(response)}")
            return False
        return True

    async def _close_streaming_card(self, event, session: CardSession):
        """关闭 CardKit streaming 模式。"""
        if not session.card_id:
            return

        from lark_oapi.api.cardkit.v1 import SettingsCardRequest, SettingsCardRequestBody

        lark_client = event.bot
        if getattr(lark_client, 'cardkit', None) is None:
            return

        session.card_sequence += 1
        request = SettingsCardRequest.builder() \
            .card_id(session.card_id) \
            .request_body(
                SettingsCardRequestBody.builder()
                .settings(json.dumps({"config": {"streaming_mode": False}}, ensure_ascii=False))
                .sequence(session.card_sequence)
                .uuid(str(uuid.uuid4()))
                .build()
            ).build()

        response = await self._call_lark_api(lark_client.cardkit.v1.card, "settings", request)
        if not self._response_success(response):
            logger.error(f"[飞书流式卡片] 关闭流式模式失败: {self._response_error(response)}")

    async def _update_card(self, event, session: CardSession, force: bool = False):
        """
        更新卡片（带并发控制）

        Args:
            event: LarkMessageEvent 实例
            session: 卡片会话
            force: 是否强制更新（忽略节流）
        """
        if not session.feishu_message_id:
            return

        session_key = self._make_session_key(event)
        lock = await self._get_session_lock(session_key)

        # 使用 per-session 锁保护 PATCH 操作
        async with lock:
            # 检查节流（除非强制更新）
            if not force:
                should_update = await self.rate_limiter.should_update(
                    session.feishu_message_id
                )
                if not should_update:
                    return

            if not session.is_terminal:
                return

            card_json = render_card(
                session,
                show_thinking=self.config.get("show_thinking", True),
                show_tools=self.config.get("show_tools", True),
                show_footer=self.config.get("show_footer", True)
            )

            # 调用飞书 API
            lark_client = event.bot

            try:
                import lark_oapi as lark
                from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

                request = PatchMessageRequest.builder() \
                    .message_id(session.feishu_message_id) \
                    .request_body(
                        PatchMessageRequestBody.builder()
                        .content(card_json)
                        .build()
                    ).build()

                response = await self._call_lark_message_api(
                    lark_client.im.v1.message,
                    "patch",
                    request,
                )

                if not self._response_success(response):
                    logger.error(f"[飞书流式卡片] 更新卡片失败: {self._response_error(response)}")

                elif self.debug_mode:
                    logger.debug(f"[飞书流式卡片] 更新卡片成功: {session.feishu_message_id}")

            except Exception as e:
                logger.error(f"[飞书流式卡片] 更新卡片异常: {e}", exc_info=True)

    async def _fallback_streaming(self, event, generator):
        """
        降级到原生流式

        Args:
            event: 事件对象
            generator: 流式输出的异步生成器

        Returns:
            流式输出的最终文本
        """
        if self.debug_mode:
            logger.debug("[飞书流式卡片] 降级到原生流式")

        original_send_streaming = getattr(StreamingPatch, '_original_send_streaming', None)
        if original_send_streaming is not None:
            return await original_send_streaming(event, generator)

        result = []
        async for chunk in generator:
            text = self._chunk_to_text(chunk)
            if text:
                result.append(text)

        return ''.join(result)

    @staticmethod
    def _is_lark_event(event) -> bool:
        """判断是否是飞书事件"""
        try:
            from astrbot.core.platform.sources.lark.lark_event import LarkMessageEvent
            return isinstance(event, LarkMessageEvent)
        except Exception:
            platform_meta = getattr(event, 'platform_meta', None)
            platform_name = getattr(platform_meta, 'name', '') or ''
            return platform_name == 'lark'

    @staticmethod
    def _make_session_key(event) -> str:
        """生成会话键"""
        message_id = getattr(event.message_obj, 'message_id', 'unknown')
        return f"{event.session_id}:{message_id}"

    # LLM 生命周期钩子
    @filter.on_llm_response()
    async def extract_llm_stats(self, event: AstrMessageEvent, resp):
        """提取 LLM 统计信息"""
        session_key = self._make_session_key(event)
        session = self.session_manager.get(session_key)

        if session:
            usage = getattr(resp, 'usage', {}) or {}

            def _usage_get(*names):
                for name in names:
                    if isinstance(usage, dict):
                        value = usage.get(name)
                    else:
                        value = getattr(usage, name, None)
                    if value:
                        return value
                return 0

            # 兼容不同的 token 字段名
            session.input_tokens = _usage_get(
                "input_tokens", "prompt_tokens", "input", "prompt"
            )
            session.output_tokens = _usage_get(
                "output_tokens", "completion_tokens", "output", "completion"
            )
            session.model = (
                getattr(resp, 'model', None)
                or getattr(resp, 'model_name', None)
                or getattr(resp, 'llm_model', None)
                or getattr(resp, 'provider_model', None)
                or session.model
                or "Unknown"
            )

            if self.debug_mode:
                logger.debug(
                    f"[飞书流式卡片] 提取统计: {session.model}, "
                    f"↑{session.input_tokens} ↓{session.output_tokens}"
                )

            if session.is_terminal and session.feishu_message_id:
                try:
                    await self._update_card(event, session, force=True)
                except Exception as e:
                    logger.error(f"[飞书流式卡片] 刷新统计信息失败: {e}")

    @filter.on_using_llm_tool()
    async def track_tool_call(self, event: AstrMessageEvent, tool_name: str, args: dict):
        """跟踪工具调用"""
        if not self._is_lark_event(event):
            return

        session_key = self._make_session_key(event)
        session = self.session_manager.get(session_key)

        if session:
            tool_call = ToolCall(name=tool_name, args=args, status="running")
            session.tools.append(tool_call)

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 工具调用: {tool_name}")

            # 立即更新卡片显示工具调用
            try:
                await self._update_card(event, session, force=True)
            except Exception as e:
                logger.error(f"[飞书流式卡片] 更新工具状态失败: {e}")

    @filter.on_llm_tool_respond()
    async def update_tool_result(self, event: AstrMessageEvent, result):
        """更新工具结果"""
        if not self._is_lark_event(event):
            return

        session_key = self._make_session_key(event)
        session = self.session_manager.get(session_key)

        if session and session.tools:
            # 更新最后一个工具的状态
            last_tool = session.tools[-1]
            last_tool.status = "completed"
            last_tool.result = str(result)[:100]  # 截断长结果

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 工具完成: {last_tool.name}")
