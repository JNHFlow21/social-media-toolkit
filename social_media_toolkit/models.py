from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


SCHEMA_VERSION = "1.0"


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_to_iso(value: Any) -> Optional[str]:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class TextExtractionResult:
    status: str
    provider: Optional[str]
    text: Optional[str]
    platform: Optional[str] = None
    post_id: Optional[str] = None
    title: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PostBundle:
    source: dict[str, Any]
    post: dict[str, Any]
    author: dict[str, Any]
    media: dict[str, list[dict[str, Any]]]
    metrics: dict[str, Any]
    content: dict[str, Any]
    comments: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_social_post(cls, social_post: Any) -> "PostBundle":
        media = social_post.media or {}
        video_url = media.get("video_url") or social_post.video_url
        cover_url = media.get("cover_url") or social_post.cover_url
        image_urls = list(media.get("image_urls") or social_post.image_urls or [])
        audio_url = media.get("audio_url") or (social_post.extra or {}).get("audio_url")

        covers = []
        if cover_url:
            covers.append({"type": "cover", "url": cover_url, "primary": True})

        images = []
        seen_images: set[str] = set()
        for url in image_urls:
            if not url or url in seen_images or url == cover_url:
                continue
            seen_images.add(url)
            images.append({"type": "image", "url": url, "index": len(images) + 1})

        return cls(
            source={
                "platform": social_post.platform,
                "content_type": social_post.content_type,
                "original_url": social_post.source_url,
                "resolved_url": social_post.resolved_url,
                "page_url": social_post.page_url,
                "post_id": social_post.post_id,
                "retrieved_at": utc_now_iso(),
                "platform_data": dict(social_post.extra or {}),
            },
            post={
                "title": social_post.title,
                "caption": social_post.body,
                "published_at": epoch_to_iso(social_post.publish_time),
                "published_at_epoch": social_post.publish_time,
                "duration_sec": social_post.duration_sec,
                "tags": list(social_post.tags or []),
            },
            author=dict(social_post.author_profile or {}),
            media={
                "videos": ([{"type": "video", "url": video_url}] if video_url else []),
                "covers": covers,
                "images": images,
                "audio": ([{"type": "audio", "url": audio_url}] if audio_url else []),
            },
            metrics=dict(social_post.public_metrics or {}),
            content={
                "canonical_text": None,
                "text_provider": None,
                "native_subtitle": (social_post.extra or {}).get("subtitle_text"),
                "transcript": None,
            },
            comments={
                "items": [],
                "reported_total": (social_post.public_metrics or {}).get("comments"),
                "coverage": "not_requested",
            },
            provenance={
                "quality": "metadata_only",
                "routes": [f"platform:{social_post.platform}"],
                "warnings": [],
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
