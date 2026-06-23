"""
流式文本归一化模块

移植自 hermes-feishu-streaming-card 的 session.py
处理增量 Markdown 文本，确保输出始终可渲染
"""
import re


class StreamingTextNormalizer:
    """流式文本归一化器"""

    def __init__(self):
        self.buffer = ""

    def feed(self, chunk: str) -> str:
        """
        喂入新的文本块，返回归一化后的完整文本

        Args:
            chunk: 新的文本块

        Returns:
            归一化后的完整文本
        """
        self.buffer += chunk

        # 去除不完整代码块
        normalized = self._remove_incomplete_code_blocks(self.buffer)

        # 去除零宽字符
        normalized = self._remove_zero_width_chars(normalized)

        return normalized

    def finalize(self) -> str:
        """
        完成流式输入，返回最终文本

        Returns:
            最终的完整文本
        """
        # 最终状态不需要移除不完整代码块
        return self._remove_zero_width_chars(self.buffer)

    def _remove_incomplete_code_blocks(self, text: str) -> str:
        """
        移除不完整的代码块

        如果文本中有未闭合的 ```，暂时不输出它
        """
        # 计算 ``` 的数量
        backtick_pattern = r'^```'
        lines = text.split('\n')
        backtick_count = sum(1 for line in lines if re.match(backtick_pattern, line.strip()))

        # 如果有未闭合的代码块
        if backtick_count % 2 == 1:
            # 找到最后一个 ``` 的位置
            last_backtick_idx = text.rfind('```')
            if last_backtick_idx != -1:
                # 暂不输出最后一个 ``` 及其后的内容
                return text[:last_backtick_idx].rstrip()

        return text

    def _remove_zero_width_chars(self, text: str) -> str:
        """
        移除零宽字符

        这些字符在某些飞书客户端可能导致显示问题
        """
        # 零宽空格 (U+200B)
        text = text.replace('​', '')
        # 零宽不连字 (U+200C)
        text = text.replace('‌', '')
        # 零宽连字 (U+200D)
        text = text.replace('‍', '')
        # 字节序标记 (U+FEFF)
        text = text.replace('﻿', '')

        return text
