"""
飞书流式卡片插件核心模块
"""
from .session import CardSession, ToolCall, SessionManager
from .render import render_card
from .normalizer import StreamingTextNormalizer, normalize_stream_text
from .patch import StreamingPatch
from .lark_card import (
    build_streaming_card,
    capture_lark_ids_from_response,
    chunk_to_text,
    mutate_card_request_object,
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
    "normalize_stream_text",
    "StreamingPatch",
    "build_streaming_card",
    "capture_lark_ids_from_response",
    "chunk_to_text",
    "mutate_card_request_object",
    "request_references_session_card",
    "apply_stats",
    "current_provider_model",
    "extract_stats_payload",
    "normalize_tool_args",
    "provider_model_name",
    "tool_name",
    "wrap_native_lark_methods",
    "install_lifecycle_hook_passthrough",
    "uninstall_lifecycle_hook_passthrough",
]
