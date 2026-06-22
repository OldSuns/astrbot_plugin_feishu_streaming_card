"""AstrBot lifecycle hook passthrough without marking the plugin reserved."""
from __future__ import annotations

from types import MethodType
from typing import Any, Callable


PATCH_TOKEN = "feishu_streaming_card_lifecycle_passthrough_v1"


def _target_event_names() -> set[str]:
    return {
        "OnLLMRequestEvent",
        "OnLLMResponseEvent",
        "OnAgentDoneEvent",
        "OnUsingLLMToolEvent",
        "OnLLMToolRespondEvent",
    }


def _event_name(event_type: Any) -> str:
    return str(getattr(event_type, "name", event_type))


def _is_own_handler(handler: Any, plugin: Any, plugin_cls: type) -> bool:
    module_path = getattr(handler, "handler_module_path", "")
    handler_module = getattr(getattr(handler, "handler", None), "__module__", "")
    module_names = {
        getattr(plugin_cls, "__module__", ""),
        "astrbot_plugin_feishu_streaming_card.main",
    }
    if module_path in module_names or handler_module in module_names:
        return True

    try:
        from astrbot.core.star.star import star_map
    except Exception:
        star_map = {}

    meta = star_map.get(module_path) if isinstance(star_map, dict) else None
    return bool(
        meta
        and (
            getattr(meta, "star_cls", None) is plugin
            or getattr(meta, "star_cls_type", None) is plugin_cls
        )
    )


def install_lifecycle_hook_passthrough(plugin: Any) -> bool:
    """Include this plugin's observability hooks without changing metadata.reserved."""
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:
        return False

    plugin_cls = plugin.__class__
    current = star_handlers_registry.get_handlers_by_event_type
    current_func = getattr(current, "__func__", current)
    if getattr(current_func, "_feishu_streaming_card_token", None) == PATCH_TOKEN:
        current_func._feishu_streaming_card_plugin = plugin
        current_func._feishu_streaming_card_plugin_cls = plugin_cls
        return True

    original = current

    def get_handlers_by_event_type(self, event_type, only_activated=True, plugins_name=None):
        handlers = list(original(event_type, only_activated, plugins_name))
        if (
            plugins_name is None
            or plugins_name == [""]
            or _event_name(event_type) not in _target_event_names()
        ):
            return handlers

        active_plugin = get_handlers_by_event_type._feishu_streaming_card_plugin
        active_cls = get_handlers_by_event_type._feishu_streaming_card_plugin_cls
        all_handlers = original(event_type, only_activated, None)
        existing = {getattr(handler, "handler_full_name", id(handler)) for handler in handlers}
        for handler in all_handlers:
            key = getattr(handler, "handler_full_name", id(handler))
            if key in existing:
                continue
            if _is_own_handler(handler, active_plugin, active_cls):
                handlers.append(handler)
                existing.add(key)
        return handlers

    get_handlers_by_event_type._feishu_streaming_card_token = PATCH_TOKEN
    get_handlers_by_event_type._feishu_streaming_card_original = original
    get_handlers_by_event_type._feishu_streaming_card_plugin = plugin
    get_handlers_by_event_type._feishu_streaming_card_plugin_cls = plugin_cls
    star_handlers_registry.get_handlers_by_event_type = MethodType(
        get_handlers_by_event_type,
        star_handlers_registry,
    )
    return True


def uninstall_lifecycle_hook_passthrough() -> bool:
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception:
        return False

    current = star_handlers_registry.get_handlers_by_event_type
    current_func = getattr(current, "__func__", current)
    if getattr(current_func, "_feishu_streaming_card_token", None) != PATCH_TOKEN:
        return False

    original: Callable | None = getattr(
        current_func,
        "_feishu_streaming_card_original",
        None,
    )
    if original is None:
        return False
    star_handlers_registry.get_handlers_by_event_type = original
    return True
