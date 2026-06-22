"""
飞书流式卡片增强插件

将 LLM 流式输出渲染为持续更新的飞书卡片
"""
import asyncio
import importlib.util
import inspect
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
            min_interval=self.config.get("update_interval", 0.2)
        )

        # 并发控制：per-session 锁
        self.session_locks: Dict[str, asyncio.Lock] = {}
        self._locks_manager_lock = asyncio.Lock()

        # 调试模式
        self.debug_mode = self.config.get("debug_mode", False)

        # 安装 Monkey Patch
        if self.config.get("enabled", True):
            self._install_patch()
        else:
            logger.info("[飞书流式卡片] 插件已禁用")

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

    async def _handle_streaming(self, event, generator):
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

            # 发送初始卡片
            feishu_message_id = await self._send_initial_card(event, session)
            session.feishu_message_id = feishu_message_id

            # 流式更新
            await self._stream_updates(event, session, generator)

            # 完成标记
            session.status = "completed"
            await self._update_card(event, session, force=True)

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 会话完成: {session_key}")

            return session.answer_text

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

    async def _send_initial_card(self, event, session: CardSession) -> str:
        """
        发送初始卡片

        Args:
            event: LarkMessageEvent 实例
            session: 卡片会话

        Returns:
            飞书消息 ID
        """
        card_json = render_card(
            session,
            show_thinking=self.config.get("show_thinking", True),
            show_tools=self.config.get("show_tools", True),
            show_footer=self.config.get("show_footer", True)
        )

        # 调用飞书 API
        lark_client = event.bot
        chat_id = session.chat_id

        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )

            original_message_id = getattr(event.message_obj, 'message_id', None)

            if original_message_id and original_message_id != 'unknown':
                request = ReplyMessageRequest.builder() \
                    .message_id(original_message_id) \
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(card_json)
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
                        .content(card_json)
                        .uuid(str(uuid.uuid4()))
                        .build()
                    ).build()
                response = await self._call_lark_message_api(
                    lark_client.im.v1.message,
                    "create",
                    request,
                )

            if response.code != 0:
                raise Exception(f"Lark API error: {response.code} - {response.msg}")

            message_id = response.data.message_id

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 发送卡片成功: {message_id}")

            return message_id

        except Exception as e:
            logger.error(f"[飞书流式卡片] 发送卡片失败: {e}", exc_info=True)
            raise

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

                if response.code != 0:
                    logger.error(f"[飞书流式卡片] 更新卡片失败: {response.code} - {response.msg}")

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

            # 兼容不同的 token 字段名
            session.input_tokens = (
                usage.get("input_tokens") or
                usage.get("prompt_tokens") or
                0
            )
            session.output_tokens = (
                usage.get("output_tokens") or
                usage.get("completion_tokens") or
                0
            )
            session.model = getattr(resp, 'model', None) or "Unknown"

            if self.debug_mode:
                logger.debug(
                    f"[飞书流式卡片] 提取统计: {session.model}, "
                    f"↑{session.input_tokens} ↓{session.output_tokens}"
                )

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
