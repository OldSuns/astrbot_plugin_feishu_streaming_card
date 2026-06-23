"""
飞书流式卡片增强插件

将 LLM 流式输出渲染为持续更新的飞书卡片
"""
import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api import logger


def _optional_hook(name: str):
    """兼容不同 AstrBot 版本的可选生命周期钩子。"""
    factory = getattr(filter, name, None)
    if callable(factory):
        return factory()

    def decorator(func):
        return func

    return decorator


class _NoopRateLimiter:
    """兼容旧测试/外部访问；主链路不再使用节流。"""

    async def should_update(self, key: str) -> bool:
        return True

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
        apply_stats,
        chunk_to_text,
        current_provider_model,
        extract_stats_payload,
        normalize_tool_args,
        provider_model_name,
        tool_name,
        install_lifecycle_hook_passthrough,
        uninstall_lifecycle_hook_passthrough,
        wrap_native_lark_methods,
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
    apply_stats = _core_module.apply_stats
    chunk_to_text = _core_module.chunk_to_text
    current_provider_model = _core_module.current_provider_model
    extract_stats_payload = _core_module.extract_stats_payload
    normalize_tool_args = _core_module.normalize_tool_args
    provider_model_name = _core_module.provider_model_name
    tool_name = _core_module.tool_name
    install_lifecycle_hook_passthrough = _core_module.install_lifecycle_hook_passthrough
    uninstall_lifecycle_hook_passthrough = _core_module.uninstall_lifecycle_hook_passthrough
    wrap_native_lark_methods = _core_module.wrap_native_lark_methods


@register(
    "astrbot_plugin_feishu_streaming_card",
    "AstrBot Contributors",
    "将 LLM 流式输出渲染为持续更新的飞书卡片",
    "v0.2.0"
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
        self.pending_tools: Dict[str, list[ToolCall]] = {}
        self.pending_stats: Dict[str, Dict[str, Any]] = {}
        self.rate_limiter = _NoopRateLimiter()

        # 并发控制：per-session 锁
        self.session_locks: Dict[str, asyncio.Lock] = {}
        self._locks_manager_lock = asyncio.Lock()

        # 调试模式
        self.debug_mode = self.config.get("debug_mode", False)
        self._release_reserved_metadata()

        # 安装/卸载 Monkey Patch
        if self.config.get("uninstall_patch", False):
            uninstall_lifecycle_hook_passthrough()
            if StreamingPatch.uninstall():
                logger.info("[飞书流式卡片] 已按配置卸载 Patch")
            else:
                logger.warning("[飞书流式卡片] Patch 未安装或卸载失败")
        elif self.config.get("enabled", True):
            self._install_patch()
        else:
            uninstall_lifecycle_hook_passthrough()
            StreamingPatch.uninstall()
            logger.info("[飞书流式卡片] 插件已禁用")

    async def initialize(self):
        """AstrBot 完成 metadata 装载后安装生命周期 hook 透传。"""
        self._release_reserved_metadata()
        installed = False
        if self.config.get("enabled", True) and not self.config.get("uninstall_patch", False):
            installed = install_lifecycle_hook_passthrough(self)
        if self.debug_mode:
            logger.debug(f"[飞书流式卡片] 生命周期 hook 透传: {installed}")

    async def terminate(self):
        """插件卸载/停用时恢复原生 send_streaming。"""
        uninstall_lifecycle_hook_passthrough()
        StreamingPatch.uninstall()

    def _release_reserved_metadata(self):
        """确保插件不是 AstrBot 保留插件，避免影响删除/卸载。"""
        hits = []
        star_map = None
        try:
            from astrbot.core.star.star import star_map
        except Exception:
            module = sys.modules.get("astrbot.core.star.star")
            star_map = getattr(module, "star_map", None)
        if not isinstance(star_map, dict):
            return hits

        module_names = {
            self.__class__.__module__,
            __name__,
            "astrbot_plugin_feishu_streaming_card.main",
        }
        for module_name in module_names:
            meta = star_map.get(module_name)
            if meta is not None:
                try:
                    meta.reserved = False
                    hits.append(module_name)
                except Exception:
                    pass

        for module_name, meta in list(star_map.items()):
            try:
                if (
                    getattr(meta, "star_cls", None) is self
                    or getattr(meta, "star_cls_type", None) is self.__class__
                ):
                    meta.reserved = False
                    if module_name not in hits:
                        hits.append(module_name)
            except Exception:
                continue
        return hits

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

    def _install_patch(self):
        """安装 Monkey Patch"""
        success = StreamingPatch.install(self._handle_streaming)

        if not success:
            logger.warning("[飞书流式卡片] Monkey Patch 未安装，可能存在插件冲突或环境不兼容")

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
            session_id = self._event_session_id(event)
            session = self.session_manager.get_or_create(
                session_key,
                unified_msg_origin=self._event_origin(event),
                conversation_id=session_id,
                message_id=getattr(event.message_obj, 'message_id', 'unknown'),
                chat_id=getattr(event.message_obj, 'chat_id', 'unknown')
            )
            self._apply_pending_session_data(event, session)

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

            # send_streaming 是副作用接口：正文已经由飞书原生流式发送。
            # 如果原生返回 None，不能把旁路捕获的 session.answer_text 返回给上层，
            # 否则 AstrBot 可能把它当作普通结果再次发送，造成二次输出/二次链路。
            return result

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

            chunk_text = chunk_to_text(chunk)
            if not chunk_text:
                continue

            # 累积文本
            session.answer_text = normalizer.feed(chunk_text)

            # 兼容旧单元测试保留本方法，但主链路已不再实时更新卡片。
            if hasattr(self, 'rate_limiter'):
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
                text = chunk_to_text(chunk)
                if text:
                    session.answer_text = normalizer.feed(text)
                yield chunk
            session.answer_text = normalizer.finalize()

        _captured, restore = wrap_native_lark_methods(event, session, self._maybe_await)
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

        self._apply_pending_session_data(event, session)

        return result

    async def _update_card(self, event, session: CardSession, force: bool = False):
        """
        更新卡片（带并发控制）

        Args:
            event: LarkMessageEvent 实例
            session: 卡片会话
            force: 是否强制更新（忽略节流）
        """
        if not session.feishu_message_id or not session.card_id:
            return

        session_key = self._make_session_key(event)
        lock = await self._get_session_lock(session_key)

        # 使用 per-session 锁保护 PATCH 操作
        async with lock:
            if not session.is_terminal and not session.has_tools:
                return

            card_json = render_card(
                session,
                show_thinking=self.config.get("show_thinking", True),
                show_tools=self.config.get("show_tools", True),
                show_footer=self.config.get("show_footer", True),
                footer_style=self.config.get("footer_style", "compact"),
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
            text = chunk_to_text(chunk)
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
        message_id = getattr(getattr(event, 'message_obj', None), 'message_id', 'unknown')
        origin = FeishuStreamingCardPlugin._event_origin(event)
        return f"{origin}:{message_id}"

    @staticmethod
    def _pending_key(event) -> str:
        keys = FeishuStreamingCardPlugin._event_keys(event)
        return keys[0] if keys else ""

    @staticmethod
    def _event_origin(event) -> str:
        for attr in ("unified_msg_origin", "session"):
            try:
                value = getattr(event, attr, None)
            except Exception:
                value = None
            if value:
                return str(value)
        return FeishuStreamingCardPlugin._event_session_id(event)

    @staticmethod
    def _event_session_id(event) -> str:
        try:
            value = getattr(event, 'session_id', None)
        except Exception:
            value = None
        if value:
            return str(value)

        origin = ""
        try:
            origin = str(getattr(event, "unified_msg_origin", "") or "")
        except Exception:
            origin = ""
        if origin and ":" in origin:
            return origin.rsplit(":", 1)[-1]
        return origin

    @staticmethod
    def _event_keys(event) -> list[str]:
        keys = []
        for value in (
            FeishuStreamingCardPlugin._event_origin(event),
            FeishuStreamingCardPlugin._event_session_id(event),
        ):
            if value and value not in keys:
                keys.append(value)
        return keys

    def _find_session_for_event(self, event) -> Optional[CardSession]:
        """生命周期钩子里的 message_id 经常和 send_streaming 不一致，按会话兜底匹配。"""
        try:
            exact = self.session_manager.get(self._make_session_key(event))
            if exact:
                return exact
        except Exception:
            pass

        origin = self._event_origin(event)
        if origin:
            candidates = [
                s for s in self.session_manager.sessions.values()
                if getattr(s, "unified_msg_origin", "") == origin
            ]
            if candidates:
                return max(candidates, key=lambda s: s.start_time)

        session_id = self._event_session_id(event)
        if not session_id:
            return None
        candidates = [
            s for s in self.session_manager.sessions.values()
            if s.conversation_id == session_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.start_time)

    def _apply_pending_session_data(self, event, session: CardSession):
        for key in self._event_keys(event):
            if key in self.pending_tools:
                session.tools.extend(self.pending_tools.pop(key))
            if key in self.pending_stats:
                apply_stats(session, self.pending_stats.pop(key))

    def _remember_stats(self, event, stats: Dict[str, Any]):
        session = self._find_session_for_event(event)
        if session:
            apply_stats(session, stats)
            return session

        key = self._pending_key(event)
        existing = self.pending_stats.get(key, {})
        merged = {**existing}
        for k, v in stats.items():
            if v not in (None, "", 0):
                merged[k] = v
            elif k not in merged:
                merged[k] = v
        self.pending_stats[key] = merged
        return None

    def _merge_response_tool_calls(self, event, resp, session: Optional[CardSession] = None):
        names = getattr(resp, 'tools_call_name', None) or []
        args_list = getattr(resp, 'tools_call_args', None) or []
        if not names:
            return

        if session is None:
            session = self._find_session_for_event(event)

        calls = []
        for i, name in enumerate(names):
            args = args_list[i] if i < len(args_list) else {}
            calls.append(
                ToolCall(
                    name=str(name),
                    args=normalize_tool_args(args),
                    status="completed",
                )
            )

        if session:
            existing = {(tool.name, json.dumps(tool.args, sort_keys=True, ensure_ascii=False)) for tool in session.tools}
            for call in calls:
                key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False))
                if key not in existing:
                    session.tools.append(call)
        else:
            self.pending_tools.setdefault(self._pending_key(event), []).extend(calls)

    @_optional_hook("on_llm_request")
    async def capture_llm_request(self, event: AstrMessageEvent, req):
        """LLMResponse 本身不一定带模型名，优先从 ProviderRequest 记录。"""
        model = getattr(req, 'model', None)
        if not model:
            provider = getattr(req, 'provider', None) or getattr(req, 'provider_meta', None)
            model = provider_model_name(provider)

        if not model:
            model = await current_provider_model(self.context, event, self._maybe_await)

        if model:
            self._remember_stats(event, {'model': str(model)})

    # LLM 生命周期钩子
    @filter.on_llm_response()
    async def extract_llm_stats(self, event: AstrMessageEvent, resp):
        """提取 LLM 统计信息"""
        stats = extract_stats_payload(resp)
        if not stats.get('model'):
            model = await current_provider_model(self.context, event, self._maybe_await)
            if model:
                stats['model'] = model

        session = self._remember_stats(event, stats)

        if session:
            self._merge_response_tool_calls(event, resp, session)

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
        else:
            self._merge_response_tool_calls(event, resp, None)

    @_optional_hook("on_agent_done")
    async def capture_agent_done(self, event: AstrMessageEvent, run_context, resp):
        """Agent 完成后补齐最终统计，并刷新终态卡片。"""
        stats = extract_stats_payload(resp)
        if not stats.get('model'):
            model = await current_provider_model(self.context, event, self._maybe_await)
            if model:
                stats['model'] = model

        session = self._remember_stats(event, stats)
        if session:
            self._merge_response_tool_calls(event, resp, session)
            if session.is_terminal and session.feishu_message_id:
                try:
                    await self._update_card(event, session, force=True)
                except Exception as e:
                    logger.error(f"[飞书流式卡片] 刷新 Agent 统计失败: {e}")
        else:
            self._merge_response_tool_calls(event, resp, None)

    @filter.on_using_llm_tool()
    async def track_tool_call(self, event: AstrMessageEvent, tool, tool_args: dict | None = None):
        """跟踪工具调用"""
        session = self._find_session_for_event(event)
        tool_name_value = tool_name(tool)
        tool_call = ToolCall(
            name=tool_name_value,
            args=normalize_tool_args(tool_args),
            status="running",
        )

        if session:
            session.tools.append(tool_call)

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 工具调用: {tool_name_value}")

            # 立即更新卡片显示工具调用
            try:
                await self._update_card(event, session, force=True)
            except Exception as e:
                logger.error(f"[飞书流式卡片] 更新工具状态失败: {e}")
        else:
            self.pending_tools.setdefault(self._pending_key(event), []).append(tool_call)

    @filter.on_llm_tool_respond()
    async def update_tool_result(
        self,
        event: AstrMessageEvent,
        tool,
        tool_args: dict | None = None,
        tool_result=None,
    ):
        """更新工具结果"""
        session = self._find_session_for_event(event)
        result_preview = str(tool_result)[:100]
        tool_name_value = tool_name(tool)

        if session and session.tools:
            # 更新最后一个工具的状态
            last_tool = session.tools[-1]
            last_tool.status = "completed"
            last_tool.result = result_preview  # 截断长结果

            if self.debug_mode:
                logger.debug(f"[飞书流式卡片] 工具完成: {last_tool.name}")

            if session.feishu_message_id:
                try:
                    await self._update_card(event, session, force=True)
                except Exception as e:
                    logger.error(f"[飞书流式卡片] 刷新工具状态失败: {e}")
        else:
            pending = self.pending_tools.setdefault(self._pending_key(event), [])
            if pending:
                pending[-1].status = "completed"
                pending[-1].result = result_preview
            else:
                pending.append(
                    ToolCall(
                        name=tool_name_value,
                        args=normalize_tool_args(tool_args),
                        status="completed",
                        result=result_preview,
                    )
                )
