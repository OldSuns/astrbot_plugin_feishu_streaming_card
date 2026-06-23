"""Patch AstrBot agent streaming so Lark can receive reasoning chunks."""
from __future__ import annotations

import functools
import importlib
import sys
from typing import Callable


PATCH_TOKEN = "feishu_streaming_card_reasoning_stream_v1"
_RESTORE: list[tuple[object, str, object]] = []


def _is_lark_agent_runner(agent_runner) -> bool:
    event = (
        getattr(
            getattr(getattr(agent_runner, "run_context", None), "context", None),
            "event",
            None,
        )
    )
    if event is None:
        return False

    for method_name in ("get_platform_name", "get_platform_id"):
        method = getattr(event, method_name, None)
        if callable(method):
            try:
                if method() == "lark":
                    return True
            except Exception:
                pass

    platform_meta = getattr(event, "platform_meta", None)
    return getattr(platform_meta, "name", None) == "lark"


def _wrap_run_agent(original: Callable, enable_lark_reasoning: bool):
    @functools.wraps(original)
    async def wrapper(agent_runner, *args, **kwargs):
        if (
            getattr(wrapper, "_feishu_streaming_card_enable_lark_reasoning", False)
            and _is_lark_agent_runner(agent_runner)
        ):
            kwargs["show_reasoning"] = True
        async for chunk in original(agent_runner, *args, **kwargs):
            yield chunk

    wrapper._feishu_streaming_card_token = PATCH_TOKEN
    wrapper._feishu_streaming_card_original = original
    return wrapper


def install_reasoning_stream_patch(enable_lark_reasoning: bool) -> bool:
    """Force AstrBot to pass reasoning chunks into Lark streaming output."""
    modules = []
    for module_name in (
        "astrbot.core.astr_agent_run_util",
        "astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal",
    ):
        module = sys.modules.get(module_name)
        if module is None:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                module = None
        if module is not None:
            modules.append(module)

    installed = False
    for module in modules:
        for name in ("run_agent", "run_live_agent"):
            original = getattr(module, name, None)
            if not callable(original):
                continue

            if getattr(original, "_feishu_streaming_card_token", None) == PATCH_TOKEN:
                original._feishu_streaming_card_enable_lark_reasoning = enable_lark_reasoning
                installed = True
                continue

            wrapped = _wrap_run_agent(original, enable_lark_reasoning)
            wrapped._feishu_streaming_card_enable_lark_reasoning = enable_lark_reasoning
            _RESTORE.append((module, name, original))
            setattr(module, name, wrapped)
            installed = True

    return installed


def uninstall_reasoning_stream_patch() -> None:
    while _RESTORE:
        module, name, original = _RESTORE.pop()
        if getattr(getattr(module, name, None), "_feishu_streaming_card_token", None) == PATCH_TOKEN:
            setattr(module, name, original)
