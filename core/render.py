"""
飞书卡片渲染模块

移植自 hermes-feishu-streaming-card 的 render.py
"""
import json
from typing import Dict, Any, Optional
from .session import CardSession


def render_card(
    session: CardSession,
    show_thinking: bool = True,
    show_tools: bool = True,
    show_footer: bool = True
) -> str:
    """
    渲染飞书卡片 JSON

    Args:
        session: 卡片会话
        show_thinking: 是否显示思考过程
        show_tools: 是否显示工具调用
        show_footer: 是否显示统计信息

    Returns:
        飞书卡片 JSON 字符串
    """
    # 状态映射
    status_info = _get_status_info(session)

    # 构建卡片
    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": status_info["subtitle"]}
        },
        "header": {
            "template": status_info["template"],
            "title": {
                "tag": "plain_text",
                "content": "AstrBot"
            },
            "subtitle": {
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

    # 工具调用历史（完成状态且有工具调用）
    if show_tools and session.is_terminal and session.has_tools:
        card["body"]["elements"].append({
            "tag": "hr"
        })

        tool_content = _render_tool_summary(session)
        card["body"]["elements"].append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": tool_content
            }
        })

    # Footer 统计信息（完成状态）
    if show_footer and session.is_terminal:
        card["body"]["elements"].append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"<font color='grey'>{session.footer_text}</font>"
            }
        })

    return json.dumps(card, ensure_ascii=False)


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


def _render_tool_summary(session: CardSession) -> str:
    """
    渲染工具调用摘要

    Returns:
        Markdown 格式的工具调用历史
    """
    lines = ["**🔧 工具调用：**\n"]

    for i, tool in enumerate(session.tools, 1):
        status_emoji = {
            "running": "⏳",
            "completed": "✓",
            "failed": "✗"
        }.get(tool.status, "?")

        lines.append(f"{i}. {status_emoji} `{tool.name}`")

        # 如果有结果，显示简短摘要
        if tool.result:
            result_preview = tool.result[:50]
            if len(tool.result) > 50:
                result_preview += "..."
            lines.append(f"   *→ {result_preview}*")

    return "\n".join(lines)
