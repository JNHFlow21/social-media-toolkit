"""Public, read-only platform adapters."""

from .core import (
    BilibiliPlatformAdapter,
    DouyinPlatformAdapter,
    PlatformRouter,
    SocialPost,
    XiaoHongShuPlatformAdapter,
)
from .youtube import YouTubePlatformAdapter

__all__ = [
    "BilibiliPlatformAdapter",
    "DouyinPlatformAdapter",
    "PlatformRouter",
    "SocialPost",
    "XiaoHongShuPlatformAdapter",
    "YouTubePlatformAdapter",
]
