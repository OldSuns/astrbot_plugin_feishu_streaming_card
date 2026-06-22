"""
Monkey Patch 模块

实现对 LarkMessageEvent.send_streaming 的拦截
"""
import time
import asyncio
from typing import Optional, Callable, Any
from astrbot.api import logger


class StreamingPatch:
    """流式发送方法的 Monkey Patch"""

    _original_send_streaming: Optional[Callable] = None
    _patch_token: str = "feishu_streaming_card_v1"
    _handler: Optional[Callable] = None
    _installed: bool = False

    @classmethod
    def install(cls, handler: Callable) -> bool:
        """
        安装 Patch

        Args:
            handler: 处理流式输出的回调函数
                    签名: async def handler(event, generator) -> Any

        Returns:
            是否安装成功
        """
        if cls._installed:
            logger.warning("[飞书流式卡片] Patch already installed")
            return False

        try:
            from astrbot.core.platform.sources.lark.lark_message_event import LarkMessageEvent
        except ImportError:
            logger.error("[飞书流式卡片] Cannot import LarkMessageEvent, Lark platform not available")
            return False

        # 检查是否已有其他插件安装了 patch
        if hasattr(LarkMessageEvent.send_streaming, '_patch_token'):
            existing_token = LarkMessageEvent.send_streaming._patch_token
            logger.warning(
                f"[飞书流式卡片] send_streaming already patched by: {existing_token}"
            )
            return False

        # 保存原方法
        cls._original_send_streaming = LarkMessageEvent.send_streaming
        cls._handler = handler

        # 定义 patched 方法
        async def patched_send_streaming(self, generator):
            try:
                return await cls._handler(self, generator)
            except Exception as e:
                logger.error(f"[飞书流式卡片] Streaming card failed: {e}", exc_info=True)
                # 降级到原方法
                if cls._original_send_streaming:
                    return await cls._original_send_streaming(self, generator)
                else:
                    # 如果原方法不可用，至少消费 generator
                    result = []
                    async for chunk in generator:
                        result.append(chunk)
                    return ''.join(result)

        # 标记 patch token
        patched_send_streaming._patch_token = cls._patch_token

        # 替换方法
        LarkMessageEvent.send_streaming = patched_send_streaming
        cls._installed = True

        logger.info("[飞书流式卡片] Monkey Patch installed successfully")
        return True

    @classmethod
    def uninstall(cls) -> bool:
        """
        卸载 Patch

        Returns:
            是否卸载成功
        """
        if not cls._installed:
            return False

        try:
            from astrbot.core.platform.sources.lark.lark_message_event import LarkMessageEvent
        except ImportError:
            return False

        # 检查是否是我们的 patch
        if not hasattr(LarkMessageEvent.send_streaming, '_patch_token'):
            return False

        if LarkMessageEvent.send_streaming._patch_token != cls._patch_token:
            logger.warning(
                "[飞书流式卡片] Cannot uninstall: different patch token"
            )
            return False

        # 恢复原方法
        if cls._original_send_streaming:
            LarkMessageEvent.send_streaming = cls._original_send_streaming
            cls._installed = False
            logger.info("[飞书流式卡片] Monkey Patch uninstalled successfully")
            return True

        return False

    @classmethod
    def is_installed(cls) -> bool:
        """是否已安装"""
        return cls._installed


class RateLimiter:
    """API 调用频率限制器"""

    def __init__(self, min_interval: float = 0.2):
        """
        Args:
            min_interval: 最小调用间隔（秒）
        """
        self.min_interval = min_interval
        self.last_update: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def should_update(self, key: str, force: bool = False) -> bool:
        """
        判断是否应该更新

        Args:
            key: 更新键（通常是 message_id）
            force: 是否强制更新

        Returns:
            是否应该更新
        """
        if force:
            return True

        async with self._lock:
            now = time.time()
            last = self.last_update.get(key, 0)

            if (now - last) >= self.min_interval:
                self.last_update[key] = now
                return True

            return False

    def mark_updated(self, key: str):
        """标记已更新"""
        self.last_update[key] = time.time()

    def cleanup_old_entries(self, max_age: float = 3600):
        """清理旧的记录"""
        now = time.time()
        old_keys = [
            key for key, timestamp in self.last_update.items()
            if (now - timestamp) > max_age
        ]
        for key in old_keys:
            self.last_update.pop(key, None)
