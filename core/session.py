"""
会话状态管理模块

移植自 hermes-feishu-streaming-card 的 session.py
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import time


@dataclass
class ToolCall:
    """工具调用记录"""
    name: str
    args: Dict[str, Any]
    status: str = "running"  # running | completed | failed
    result: Optional[str] = None
    start_time: float = field(default_factory=time.time)

    @property
    def duration(self) -> float:
        return time.time() - self.start_time


@dataclass
class CardSession:
    """卡片会话状态"""
    conversation_id: str
    message_id: str
    chat_id: str
    unified_msg_origin: str = ""
    feishu_message_id: Optional[str] = None
    card_id: Optional[str] = None

    status: str = "thinking"  # thinking | completed | failed
    start_time: float = field(default_factory=time.time)

    thinking_text: str = ""
    answer_text: str = ""
    streaming_text_element_id: str = "markdown_1"
    thinking_panel_attached: bool = False
    tools: List[ToolCall] = field(default_factory=list)

    model: str = "Unknown"
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def duration(self) -> float:
        """总耗时"""
        return time.time() - self.start_time

    @property
    def footer_text(self) -> str:
        """Footer 统计文本"""
        duration = self.duration
        tps = self._tps_for_duration(duration)
        return f"{duration:.1f}s · {self.model} · ↑{self.input_tokens} ↓{self.output_tokens} · {tps:.1f} tps"

    @property
    def tps(self) -> float:
        """输出 token 生成速度。"""
        duration = self.duration
        if duration <= 0 or self.output_tokens <= 0:
            return 0.0
        return self.output_tokens / duration

    def _tps_for_duration(self, duration: float) -> float:
        if duration <= 0 or self.output_tokens <= 0:
            return 0.0
        return self.output_tokens / duration

    @property
    def has_tools(self) -> bool:
        """是否有工具调用"""
        return len(self.tools) > 0

    @property
    def is_terminal(self) -> bool:
        """是否处于终止状态"""
        return self.status in ("completed", "failed")


class SessionManager:
    """会话管理器"""

    def __init__(self, max_sessions: int = 100, ttl: int = 3600):
        self.sessions: Dict[str, CardSession] = {}
        self.max_sessions = max_sessions
        self.ttl = ttl

    def get_or_create(self, key: str, **kwargs) -> CardSession:
        """获取或创建会话"""
        # 清理过期会话
        self._cleanup_expired()

        # 检查容量
        if key not in self.sessions and len(self.sessions) >= self.max_sessions:
            oldest_key = min(
                self.sessions.keys(),
                key=lambda k: self.sessions[k].start_time
            )
            self.sessions.pop(oldest_key)

        if key not in self.sessions:
            self.sessions[key] = CardSession(**kwargs)

        return self.sessions[key]

    def get(self, key: str) -> Optional[CardSession]:
        """获取会话"""
        return self.sessions.get(key)

    def remove(self, key: str) -> None:
        """移除会话"""
        self.sessions.pop(key, None)

    def _cleanup_expired(self):
        """清理过期会话"""
        now = time.time()
        expired = [
            key for key, session in self.sessions.items()
            if (now - session.start_time) > self.ttl
        ]
        for key in expired:
            self.sessions.pop(key)
