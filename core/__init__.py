"""
飞书流式卡片插件核心模块
"""
from .session import CardSession, ToolCall, SessionManager
from .render import render_card
from .normalizer import StreamingTextNormalizer
from .patch import StreamingPatch
from .lark_card import (
    THINKING_PANEL_ELEMENT_ID,
    capture_lark_ids_from_response,
    chunk_to_reasoning_text,
    chunk_to_text,
    is_reasoning_chunk,
    mutate_card_request_object,
    thinking_panel_element,
    request_references_session_card,
)
from .observability import (
    apply_stats,
    current_provider_model,
    extract_stats_payload,
    normalize_tool_args,
    provider_model_name,
    tool_name,
)
from .native_lark import wrap_native_lark_methods
from .reasoning_stream_patch import (
    install_reasoning_stream_patch,
    uninstall_reasoning_stream_patch,
)
from .lifecycle_hooks import (
    install_lifecycle_hook_passthrough,
    uninstall_lifecycle_hook_passthrough,
)

__all__ = [
    "CardSession",
    "ToolCall",
    "SessionManager",
    "render_card",
    "StreamingTextNormalizer",
    "StreamingPatch",
    "THINKING_PANEL_ELEMENT_ID",
    "capture_lark_ids_from_response",
    "chunk_to_reasoning_text",
    "chunk_to_text",
    "is_reasoning_chunk",
    "mutate_card_request_object",
    "thinking_panel_element",
    "request_references_session_card",
    "apply_stats",
    "current_provider_model",
    "extract_stats_payload",
    "normalize_tool_args",
    "provider_model_name",
    "tool_name",
    "wrap_native_lark_methods",
    "install_reasoning_stream_patch",
    "uninstall_reasoning_stream_patch",
    "install_lifecycle_hook_passthrough",
    "uninstall_lifecycle_hook_passthrough",
]
