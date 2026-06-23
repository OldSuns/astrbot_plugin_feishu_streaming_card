"""飞书 CardKit 卡片与响应辅助函数。"""
import json

from .session import CardSession


THINKING_PANEL_TITLE = "模型思考"
THINKING_PANEL_ELEMENT_ID = "think_panel"


def thinking_panel_element(thinking_text: str) -> dict:
    return {
        "tag": "collapsible_panel",
        "element_id": THINKING_PANEL_ELEMENT_ID,
        "expanded": False,
        "background_color": "grey",
        "padding": "8px 8px 8px 8px",
        "margin": "4px 0px 4px 0px",
        "border": {
            "color": "grey",
            "corner_radius": "6px",
        },
        "header": {
            "title": {
                "tag": "plain_text",
                "content": THINKING_PANEL_TITLE,
            },
            "background_color": "grey",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": thinking_text,
            }
        ],
    }


def _is_thinking_panel(element: dict) -> bool:
    if element.get("tag") != "collapsible_panel":
        return False
    if element.get("element_id") == THINKING_PANEL_ELEMENT_ID:
        return True
    title = element.get("header", {}).get("title", {})
    return title.get("content") == THINKING_PANEL_TITLE


def _iter_card_elements(elements):
    if not isinstance(elements, list):
        return
    for element in elements:
        if not isinstance(element, dict):
            continue
        yield element
        nested = element.get("elements")
        if isinstance(nested, list):
            yield from _iter_card_elements(nested)


def remember_streaming_text_element(card: dict, session: CardSession) -> None:
    """记录原生 CardKit 正文 markdown 元素，供后续组件级插入定位。"""
    elements = card.get("body", {}).get("elements", [])
    for element in _iter_card_elements(elements):
        if element.get("tag") != "markdown":
            continue
        element_id = element.get("element_id")
        if element_id and element_id != THINKING_PANEL_ELEMENT_ID:
            session.streaming_text_element_id = str(element_id)
            return


def inject_thinking_panel(card: dict, thinking_text: str) -> dict:
    if not thinking_text:
        return card

    body = card.setdefault("body", {})
    elements = body.setdefault("elements", [])
    if not isinstance(elements, list):
        return card

    for element in elements:
        if isinstance(element, dict) and _is_thinking_panel(element):
            element["elements"] = [
                {
                    "tag": "markdown",
                    "content": thinking_text,
                }
            ]
            return card

    elements.insert(0, thinking_panel_element(thinking_text))
    return card


def _chunk_plain_text(chunk) -> str:
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


def is_reasoning_chunk(chunk) -> bool:
    """判断 chunk 是否是 AstrBot 的 reasoning 消息链。"""
    return getattr(chunk, "type", None) == "reasoning"


def chunk_to_reasoning_text(chunk) -> str:
    """从 AstrBot reasoning chunk 或 LLMResponse 中提取思考文本。"""
    if chunk is None:
        return ""

    if is_reasoning_chunk(chunk):
        return _chunk_plain_text(chunk)

    reasoning = getattr(chunk, "reasoning_content", None)
    if reasoning:
        return str(reasoning)

    return ""


def chunk_to_text(chunk) -> str:
    """从 AstrBot send_streaming chunk 中提取纯文本。"""
    if is_reasoning_chunk(chunk):
        return ""
    return _chunk_plain_text(chunk)


def status_tuple(session: CardSession) -> tuple[str, str]:
    return {
        "thinking": ("思考中...", "indigo"),
        "completed": ("✓ 完成", "green"),
        "failed": ("✗ 处理失败", "red"),
    }.get(session.status, ("思考中...", "indigo"))


def summary_content(session: CardSession, fallback: str) -> str:
    return (session.answer_text or "").strip() or fallback


def apply_card_header(card: dict, session: CardSession) -> dict:
    """给原生 CardKit 初始卡片注入插件标题区域。"""
    subtitle, template = status_tuple(session)

    remember_streaming_text_element(card, session)
    card.setdefault("config", {})
    card["config"].setdefault("summary", {})["content"] = summary_content(session, subtitle)
    card["header"] = {
        "template": template,
        "title": {"tag": "plain_text", "content": subtitle},
    }
    if session.thinking_text:
        inject_thinking_panel(card, session.thinking_text)
        session.thinking_panel_attached = True
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


def _looks_like_card_dict(data: dict) -> bool:
    body = data.get("body")
    if data.get("schema") or data.get("config"):
        return True
    return isinstance(body, dict) and isinstance(body.get("elements"), list)


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
        if _looks_like_card_dict(obj):
            apply_card_header(obj, session)
            changed = True
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
