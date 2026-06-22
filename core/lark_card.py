"""飞书 CardKit 卡片与响应辅助函数。"""
import json

from .session import CardSession


def chunk_to_text(chunk) -> str:
    """从 AstrBot send_streaming chunk 中提取纯文本。"""
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk

    chain = getattr(chunk, "chain", None)
    if isinstance(chain, list):
        parts = []
        for comp in chain:
            text = getattr(comp, "text", None)
            if text:
                parts.append(str(text))
        return "".join(parts)

    text = getattr(chunk, "text", None)
    if text:
        return str(text)

    return ""


def status_tuple(session: CardSession) -> tuple[str, str]:
    return {
        "thinking": ("思考中...", "indigo"),
        "completed": ("✓ 完成", "green"),
        "failed": ("✗ 处理失败", "red"),
    }.get(session.status, ("思考中...", "indigo"))


def build_streaming_card(session: CardSession) -> dict:
    """构建 CardKit streaming 初始卡片。"""
    subtitle, template = status_tuple(session)

    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "update_multi": True,
            "summary": {"content": subtitle},
        },
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "AstrBot"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
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


def apply_card_header(card: dict, session: CardSession) -> dict:
    """给原生 CardKit 初始卡片注入插件标题区域。"""
    subtitle, template = status_tuple(session)

    card.setdefault("config", {})
    card["config"].setdefault("summary", {})["content"] = subtitle
    card["header"] = {
        "template": template,
        "title": {"tag": "plain_text", "content": "AstrBot"},
        "subtitle": {"tag": "plain_text", "content": subtitle},
    }
    return card


def mutate_card_json_text(text: str, session: CardSession) -> str:
    """如果字符串是飞书卡片 JSON，则注入标题后返回。"""
    try:
        data = json.loads(text)
    except Exception:
        return text

    if not isinstance(data, dict):
        return text
    if "body" not in data and "schema" not in data and "config" not in data:
        return text

    apply_card_header(data, session)
    return json.dumps(data, ensure_ascii=False)


def mutate_card_request_object(obj, session: CardSession, seen=None) -> bool:
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
                new_value = mutate_card_json_text(value, session)
                if new_value != value:
                    obj[key] = new_value
                    changed = True
            elif mutate_card_request_object(value, session, seen):
                changed = True
        return changed

    if isinstance(obj, list):
        for i, value in enumerate(list(obj)):
            if isinstance(value, str):
                new_value = mutate_card_json_text(value, session)
                if new_value != value:
                    obj[i] = new_value
                    changed = True
            elif mutate_card_request_object(value, session, seen):
                changed = True
        return changed

    if isinstance(obj, (str, bytes, int, float, bool)):
        return False

    attrs = getattr(obj, "__dict__", None)
    if not isinstance(attrs, dict):
        return False

    for key, value in list(attrs.items()):
        if isinstance(value, str):
            new_value = mutate_card_json_text(value, session)
            if new_value != value:
                try:
                    setattr(obj, key, new_value)
                    changed = True
                except Exception:
                    pass
        elif mutate_card_request_object(value, session, seen):
            changed = True

    return changed


def capture_lark_ids_from_response(response) -> dict[str, str]:
    """从 lark-oapi response/data 中提取 message_id/card_id。"""
    captured: dict[str, str] = {}
    for obj in (response, getattr(response, "data", None)):
        if obj is None:
            continue
        message_id = getattr(obj, "message_id", None)
        card_id = getattr(obj, "card_id", None)
        if message_id:
            captured["message_id"] = message_id
        if card_id:
            captured["card_id"] = card_id
    return captured


def request_references_session_card(obj, session: CardSession, seen=None) -> bool:
    """判断 message create/reply 请求是否发送的是当前 CardKit 卡片。"""
    if obj is None:
        return False
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return False
    seen.add(obj_id)

    if isinstance(obj, str):
        if session.card_id and session.card_id in obj:
            return True
        return "card_id" in obj and ('"type"' in obj or "'type'" in obj)

    if isinstance(obj, dict):
        if session.card_id and obj.get("card_id") == session.card_id:
            return True
        return any(
            request_references_session_card(value, session, seen)
            for value in obj.values()
        )

    if isinstance(obj, (bytes, int, float, bool)):
        return False

    if isinstance(obj, (list, tuple, set)):
        return any(
            request_references_session_card(value, session, seen)
            for value in obj
        )

    attrs = getattr(obj, "__dict__", None)
    if isinstance(attrs, dict):
        return any(
            request_references_session_card(value, session, seen)
            for value in attrs.values()
        )

    return False
