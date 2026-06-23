"""
飞书卡片渲染模块

移植自 hermes-feishu-streaming-card 的 render.py
"""
import json
from typing import Dict
from .lark_card import summary_content
from .session import CardSession


FOOTER_STYLE_SMALL = "small"
FOOTER_STYLE_MD = "md"
SUPPORTED_FOOTER_STYLES = {FOOTER_STYLE_SMALL, FOOTER_STYLE_MD}
TOOL_RANGE_LATEST = "latest"
TOOL_RANGE_EARLIEST = "earliest"
SUPPORTED_TOOL_RANGES = {TOOL_RANGE_LATEST, TOOL_RANGE_EARLIEST}


def render_card(
    session: CardSession,
    show_thinking: bool = True,
    show_tools: bool = True,
    show_footer: bool = True,
    footer_style: str = FOOTER_STYLE_SMALL,
    tool_limit: int = 5,
    tool_range: str = TOOL_RANGE_LATEST,
) -> str:
    """
    渲染飞书卡片 JSON

    Args:
        session: 卡片会话
        show_thinking: 是否显示思考过程
        show_tools: 是否显示工具调用
        show_footer: 是否显示统计信息
        footer_style: 统计信息样式，small 使用 notation 小字号，md 使用 lark_md 灰字
        tool_limit: 最多显示几个工具调用
        tool_range: latest 显示最新调用，earliest 显示最早调用

    Returns:
        飞书卡片 JSON 字符串
    """
    if footer_style not in SUPPORTED_FOOTER_STYLES:
        raise ValueError(
            f"Unsupported footer_style: {footer_style}. "
            f"Expected one of: {', '.join(sorted(SUPPORTED_FOOTER_STYLES))}"
        )
    if tool_range not in SUPPORTED_TOOL_RANGES:
        raise ValueError(
            f"Unsupported tool_range: {tool_range}. "
            f"Expected one of: {', '.join(sorted(SUPPORTED_TOOL_RANGES))}"
        )

    # 状态映射
    status_info = _get_status_info(session)

    # 构建卡片
    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": summary_content(session, status_info["subtitle"])}
        },
        "header": {
            "template": status_info["template"],
            "title": {
                "tag": "plain_text",
                "content": status_info["subtitle"]
            }
        },
        "body": {
            "elements": []
        }
    }

    # 思考过程（如果有）
    if show_thinking and session.thinking_text:
        card["body"]["elements"].append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**思考过程：**\n\n{session.thinking_text}"
            }
        })

        # 如果有回答内容，添加分隔线
        if session.answer_text:
            card["body"]["elements"].append({
                "tag": "hr"
            })

    # 主内容（回答）
    if session.answer_text:
        card["body"]["elements"].append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": session.answer_text
            }
        })
    elif not session.thinking_text:
        # 如果没有思考也没有回答，显示加载中
        card["body"]["elements"].append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "*正在处理中...*"
            }
        })

    # 工具调用历史
    tools, hidden_tool_count = _select_tools(session.tools, tool_limit, tool_range)
    if show_tools and tools:
        card["body"]["elements"].append({
            "tag": "hr"
        })

        tool_content = _render_tool_summary(tools, hidden_tool_count)
        card["body"]["elements"].append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": tool_content
            }
        })

    # Footer 统计信息（完成状态）
    if show_footer and session.is_terminal:
        card["body"]["elements"].append(_render_footer(session, footer_style))

    return json.dumps(card, ensure_ascii=False)


def _render_footer(session: CardSession, footer_style: str) -> Dict:
    if footer_style == FOOTER_STYLE_MD:
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"<font color='grey'>{session.footer_text}</font>",
            },
        }

    return {
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": session.footer_text,
            "text_size": "notation",
            "text_color": "grey",
        },
    }


def _get_status_info(session: CardSession) -> Dict[str, str]:
    """
    获取状态信息

    Returns:
        包含 subtitle 和 template 的字典
    """
    status_map = {
        "thinking": {
            "subtitle": "思考中...",
            "template": "indigo"
        },
        "completed": {
            "subtitle": "✓ 完成",
            "template": "green"
        },
        "failed": {
            "subtitle": "✗ 处理失败",
            "template": "red"
        }
    }

    return status_map.get(session.status, status_map["thinking"])


def _select_tools(tools, limit: int, tool_range: str):
    limit = int(limit)
    if limit <= 0:
        return [], 0
    if tool_range == TOOL_RANGE_EARLIEST:
        return tools[:limit], max(len(tools) - limit, 0)
    return tools[-limit:], max(len(tools) - limit, 0)


def _render_tool_summary(tools, hidden_tool_count: int) -> str:
    """
    渲染工具调用摘要

    Returns:
        Markdown 格式的工具调用历史
    """
    lines = ["**🔧 工具调用：**\n"]

    for i, tool in enumerate(tools, 1):
        lines.append(f"{i}. `{tool.name}`")
    if hidden_tool_count:
        lines.append(f"+{hidden_tool_count}")

    return "\n".join(lines)
