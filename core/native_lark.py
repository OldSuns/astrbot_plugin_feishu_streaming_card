"""原生 Lark streaming 方法包装。"""
from typing import Any, Awaitable, Callable

from .lark_card import (
    capture_lark_ids_from_response,
    mutate_card_request_object,
    request_references_session_card,
)
from .session import CardSession


def wrap_native_lark_methods(
    event,
    session: CardSession,
    awaiter: Callable[[Any], Awaitable[Any]],
):
    """临时包装原生流式内部方法，仅用于捕获最终 message_id/card_id。"""
    captured: dict[str, str] = {}
    restore: list[tuple[Any, str, Any]] = []
    card_message_depth = 0

    def remember(values: dict[str, str]) -> None:
        if values.get("message_id"):
            captured["message_id"] = values["message_id"]
            session.feishu_message_id = values["message_id"]
        if values.get("card_id"):
            captured["card_id"] = values["card_id"]
            session.card_id = values["card_id"]

    def make_wrapper(name: str, original):
        async def wrapper(*args, **kwargs):
            nonlocal card_message_depth
            is_message_api = name in (
                "_send_card_message",
                "_send_message",
                "message_create",
                "message_reply",
            )
            message_references_card = card_message_depth > 0
            enters_card_message_scope = False

            if name in ("_create_streaming_card", "card_create"):
                for arg in args:
                    mutate_card_request_object(arg, session)
                for value in kwargs.values():
                    mutate_card_request_object(value, session)

            if is_message_api:
                for arg in args:
                    if isinstance(arg, str) and arg.startswith("card"):
                        remember({"card_id": arg})
                        message_references_card = True
                        if name == "_send_card_message":
                            enters_card_message_scope = True
                    elif request_references_session_card(arg, session):
                        message_references_card = True
                for value in kwargs.values():
                    if request_references_session_card(value, session):
                        message_references_card = True
                        if name == "_send_card_message":
                            enters_card_message_scope = True

            if name == "_send_card_message" and session.card_id:
                enters_card_message_scope = message_references_card

            if enters_card_message_scope:
                card_message_depth += 1
            try:
                result = original(*args, **kwargs)
                result = await awaiter(result)
            finally:
                if enters_card_message_scope:
                    card_message_depth -= 1

            values = capture_lark_ids_from_response(result)
            if isinstance(result, str):
                if name in ("_create_streaming_card", "card_create"):
                    values["card_id"] = result
                elif is_message_api:
                    values["message_id"] = result

            # 只记录当前 CardKit 卡片对应的 message_id。
            # 普通文本回退消息不能 patch 成卡片。
            if is_message_api and not message_references_card:
                values.pop("message_id", None)

            remember(values)
            return result

        return wrapper

    for name in (
        "_create_streaming_card",
        "_send_card_message",
        "_send_message",
        "_close_streaming_mode",
    ):
        original = getattr(event, name, None)
        if callable(original):
            restore.append((event, name, original))
            setattr(event, name, make_wrapper(name, original))

    lark_client = getattr(event, "bot", None)

    card_api = getattr(
        getattr(getattr(lark_client, "cardkit", None), "v1", None),
        "card",
        None,
    )
    if card_api is not None:
        for method_name in ("create", "acreate"):
            original = getattr(card_api, method_name, None)
            if callable(original):
                restore.append((card_api, method_name, original))
                setattr(card_api, method_name, make_wrapper("card_create", original))

    message_api = getattr(
        getattr(getattr(lark_client, "im", None), "v1", None),
        "message",
        None,
    )
    if message_api is not None:
        for method_name, wrapper_name in (
            ("create", "message_create"),
            ("acreate", "message_create"),
            ("reply", "message_reply"),
            ("areply", "message_reply"),
        ):
            original = getattr(message_api, method_name, None)
            if callable(original):
                restore.append((message_api, method_name, original))
                setattr(message_api, method_name, make_wrapper(wrapper_name, original))

    return captured, restore
