"""AstrBot LLM 观测数据提取工具。"""
from typing import Any, Awaitable, Callable, Dict, Optional

from .session import CardSession


async def maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


def deep_get_first(obj, names, max_depth: int = 5):
    """从 dict/object 嵌套结构中查找第一个非空字段。"""
    seen = set()

    def walk(value, depth):
        if value is None or depth < 0:
            return None
        value_id = id(value)
        if value_id in seen:
            return None
        seen.add(value_id)

        if isinstance(value, dict):
            for name in names:
                candidate = value.get(name)
                if candidate not in (None, ""):
                    return candidate
            iterable = value.values()
        else:
            for name in names:
                candidate = getattr(value, name, None)
                if candidate not in (None, ""):
                    return candidate
            attrs = getattr(value, "__dict__", None)
            if not isinstance(attrs, dict):
                return None
            iterable = attrs.values()

        for child in iterable:
            if isinstance(child, (str, bytes, int, float, bool)):
                continue
            found = walk(child, depth - 1)
            if found not in (None, ""):
                return found
        return None

    return walk(obj, max_depth)


def _to_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except Exception:
        return default


def extract_stats_payload(resp) -> Dict[str, Any]:
    usage = (
        getattr(resp, "usage", None)
        or getattr(resp, "usage_metadata", None)
        or getattr(resp, "token_usage", None)
        or deep_get_first(resp, ("usage", "usage_metadata", "token_usage"), 4)
        or {}
    )

    def usage_get(*names):
        direct = deep_get_first(usage, names, 3)
        if direct not in (None, ""):
            return direct
        nested = deep_get_first(resp, names, 4)
        return nested if nested not in (None, "") else 0

    input_tokens = usage_get(
        "input_tokens", "prompt_tokens", "prompt_token_count",
        "input_token_count", "total_prompt_tokens", "input", "prompt",
    )
    if not input_tokens:
        input_other = usage_get("input_other")
        input_cached = usage_get("input_cached", "cached_tokens", "cached_token_count")
        if input_other or input_cached:
            input_tokens = _to_int(input_other) + _to_int(input_cached)

    output_tokens = usage_get(
        "output_tokens", "completion_tokens", "candidates_token_count",
        "completion_token_count", "generated_tokens", "output", "completion",
    )

    model = deep_get_first(
        resp,
        (
            "model", "model_name", "llm_model", "provider_model",
            "model_id", "deployment_name", "engine",
        ),
        5,
    )

    return {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "model": str(model) if model else None,
    }


def provider_model_name(provider) -> Optional[str]:
    if provider is None:
        return None

    meta = None
    meta_func = getattr(provider, "meta", None)
    if callable(meta_func):
        try:
            meta = meta_func()
        except Exception:
            meta = None

    for value in (
        getattr(meta, "model", None),
        getattr(provider, "model", None),
        getattr(provider, "model_name", None),
    ):
        if value and value != "Unknown":
            return str(value)

    get_model = getattr(provider, "get_model", None)
    if callable(get_model):
        try:
            model = get_model()
            if model and model != "Unknown":
                return str(model)
        except Exception:
            pass

    provider_config = getattr(provider, "provider_config", None)
    if isinstance(provider_config, dict):
        model = provider_config.get("model") or provider_config.get("model_name")
        if model and model != "Unknown":
            return str(model)

    return None


async def current_provider_model(context, event, awaiter: Callable[[Any], Awaitable[Any]] = maybe_await) -> Optional[str]:
    if context is None:
        return None

    umo = getattr(event, "unified_msg_origin", None)

    get_using_provider = getattr(context, "get_using_provider", None)
    if callable(get_using_provider):
        try:
            try:
                provider = get_using_provider(umo=umo)
            except TypeError:
                provider = get_using_provider(umo)
            provider = await awaiter(provider)
            model = provider_model_name(provider)
            if model:
                return model
        except Exception:
            pass

    provider_id = None
    get_provider_id = getattr(context, "get_current_chat_provider_id", None)
    if callable(get_provider_id):
        try:
            try:
                provider_id = get_provider_id(umo=umo)
            except TypeError:
                provider_id = get_provider_id(umo)
            provider_id = await awaiter(provider_id)
        except Exception:
            provider_id = None

    if not provider_id:
        return None

    for owner in (
        context,
        getattr(context, "provider_manager", None),
    ):
        get_provider = getattr(owner, "get_provider_by_id", None)
        if callable(get_provider):
            try:
                provider = await awaiter(get_provider(provider_id))
                model = provider_model_name(provider)
                if model:
                    return model
            except Exception:
                pass

    provider_manager = getattr(context, "provider_manager", None)
    provider_insts = getattr(provider_manager, "provider_insts", None) or []
    for provider in provider_insts:
        try:
            meta = provider.meta()
            if getattr(meta, "id", None) == provider_id:
                model = provider_model_name(provider)
                if model:
                    return model
        except Exception:
            continue

    return None


def tool_name(tool) -> str:
    return (
        getattr(tool, "name", None)
        or getattr(tool, "func_name", None)
        or getattr(tool, "__name__", None)
        or str(tool)
    )


def normalize_tool_args(tool_args) -> Dict[str, Any]:
    if isinstance(tool_args, dict):
        return tool_args
    attrs = getattr(tool_args, "__dict__", None)
    if isinstance(attrs, dict):
        return dict(attrs)
    return {}


def apply_stats(session: CardSession, stats: Dict[str, Any]):
    if stats.get("input_tokens") not in (None, ""):
        try:
            value = int(stats.get("input_tokens") or 0)
            if value or not session.input_tokens:
                session.input_tokens = value
        except Exception:
            if not session.input_tokens:
                session.input_tokens = 0
    if stats.get("output_tokens") not in (None, ""):
        try:
            value = int(stats.get("output_tokens") or 0)
            if value or not session.output_tokens:
                session.output_tokens = value
        except Exception:
            if not session.output_tokens:
                session.output_tokens = 0
    if stats.get("model") and stats.get("model") != "Unknown":
        session.model = str(stats["model"])
