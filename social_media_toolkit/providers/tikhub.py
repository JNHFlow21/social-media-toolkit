"""Optional TikHub fallback for public Douyin media resolution.

TikHub is not a text or ASR provider. It is consulted only after the free
public Douyin adapter fails and only when ``TIKHUB_API_KEY`` is configured.
The returned CDN URLs are temporary inputs for inspection, explicit download,
or the existing Volcengine ASR route; the toolkit never persists them by
itself.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from social_media_toolkit.platforms.core import SocialPost
from social_media_toolkit.providers.volcengine import _load_secret


TIKHUB_API_KEY_SECRET_NAME = "TIKHUB_API_KEY"
TIKHUB_DOUYIN_WEB_SHARE_ENDPOINT = (
    "https://api.tikhub.io/api/v1/douyin/web/fetch_one_video_by_share_url"
)
TIKHUB_DOUYIN_DOCS_URL = "https://docs.tikhub.io/257556744e0"
TIKHUB_REQUEST_TIMEOUT_SECONDS = 60


class TikHubError(RuntimeError):
    """A terminal TikHub media-resolution error."""


class TikHubDouyinMediaProvider:
    """Resolve one public Douyin share URL through TikHub's Web API."""

    provider_name = "tikhub"
    secret_name = TIKHUB_API_KEY_SECRET_NAME
    endpoint = TIKHUB_DOUYIN_WEB_SHARE_ENDPOINT

    def configured(self) -> bool:
        return bool(_load_secret(self.secret_name))

    def fetch_post(self, share_text: str) -> SocialPost:
        api_key = _load_secret(self.secret_name)
        if not api_key:
            raise TikHubError(
                f"Missing {self.secret_name}. Configure the optional TikHub fallback first: "
                f"{TIKHUB_DOUYIN_DOCS_URL}"
            )

        source_url = _extract_share_url(share_text)
        try:
            response = requests.get(
                self.endpoint,
                params={"share_url": source_url},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json",
                    "User-Agent": "social-media-toolkit/0.4",
                },
                timeout=TIKHUB_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TikHubError(f"TikHub Douyin request failed: {exc}") from exc

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise TikHubError("TikHub returned non-JSON data") from exc
        if not isinstance(payload, dict):
            raise TikHubError("TikHub returned an invalid response object")

        code = payload.get("code")
        if str(code) != "200":
            message = payload.get("message_zh") or payload.get("message") or "unknown error"
            raise TikHubError(f"TikHub Douyin request returned code {code}: {message}")

        data = payload.get("data")
        detail = _extract_aweme_detail(data)
        if not detail:
            reason = _extract_filter_reason(data)
            suffix = f" ({reason})" if reason else ""
            raise TikHubError(f"TikHub returned no public Douyin work{suffix}")

        return _social_post_from_detail(detail, source_url=source_url)


def _extract_share_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>()]+", str(text or ""))
    if not match:
        raise TikHubError("A public Douyin share URL is required")
    url = match.group(0).rstrip(".,;:!?)]}\"'")
    host = urlparse(url).netloc.lower()
    if not (host.endswith("douyin.com") or host.endswith("iesdouyin.com")):
        raise TikHubError("TikHub Douyin fallback accepts Douyin URLs only")
    return url


def _extract_aweme_detail(value: Any, *, depth: int = 0) -> Optional[dict[str, Any]]:
    if depth > 4:
        return None
    if isinstance(value, dict):
        if value.get("aweme_id") and any(
            value.get(key) is not None for key in ("video", "images", "image_post_info")
        ):
            return value
        for key in ("aweme_detail", "data", "result"):
            candidate = _extract_aweme_detail(value.get(key), depth=depth + 1)
            if candidate:
                return candidate
        for key in ("aweme_list", "item_list"):
            entries = value.get(key)
            if isinstance(entries, list):
                for entry in entries:
                    candidate = _extract_aweme_detail(entry, depth=depth + 1)
                    if candidate:
                        return candidate
    elif isinstance(value, list):
        for entry in value:
            candidate = _extract_aweme_detail(entry, depth=depth + 1)
            if candidate:
                return candidate
    return None


def _extract_filter_reason(value: Any, *, depth: int = 0) -> Optional[str]:
    if depth > 4:
        return None
    if isinstance(value, dict):
        entries = value.get("filter_list")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                reason = (
                    entry.get("filter_reason")
                    or entry.get("reason")
                    or entry.get("detail_msg")
                    or entry.get("notice")
                )
                if reason is not None:
                    return str(reason)
        for key in ("data", "result"):
            reason = _extract_filter_reason(value.get(key), depth=depth + 1)
            if reason:
                return reason
    elif isinstance(value, list):
        for entry in value:
            reason = _extract_filter_reason(entry, depth=depth + 1)
            if reason:
                return reason
    return None


def _social_post_from_detail(detail: dict[str, Any], *, source_url: str) -> SocialPost:
    post_id = str(detail.get("aweme_id") or detail.get("group_id") or "").strip()
    if not post_id:
        raise TikHubError("TikHub Douyin response is missing aweme_id")

    video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
    video_url = _select_video_url(video)
    audio_url = _select_audio_url(video)
    image_urls = _select_image_urls(detail)
    aweme_type = detail.get("aweme_type")
    is_image_note = bool(image_urls) and (aweme_type in {68, 150} or not video_url)
    if not is_image_note and not video_url:
        raise TikHubError("TikHub Douyin response contains no playable media URL")

    cover = video.get("cover") if isinstance(video.get("cover"), dict) else {}
    cover_url = _select_address_url(cover) or (image_urls[0] if image_urls else None)
    author = detail.get("author") if isinstance(detail.get("author"), dict) else {}
    statistics = detail.get("statistics") if isinstance(detail.get("statistics"), dict) else {}
    sec_uid = author.get("sec_uid")
    uid = author.get("uid") or author.get("user_id")
    unique_id = author.get("unique_id")
    author_name = author.get("nickname") or unique_id
    avatar_url = _select_address_url(author.get("avatar_thumb"))
    author_profile = {
        "id": uid or sec_uid,
        "name": author_name,
        "handle": unique_id,
        "sec_uid": sec_uid,
        "avatar_url": avatar_url,
        "profile_url": f"https://www.douyin.com/user/{sec_uid}" if sec_uid else None,
        "extra": author,
    }
    duration_sec = (
        _duration_seconds(video.get("duration"), milliseconds=True)
        if video.get("duration") is not None
        else _duration_seconds(detail.get("duration"))
    )
    title = re.sub(
        r'[\\/:*?"<>|]',
        "_",
        str(detail.get("desc") or f"douyin_{post_id}").strip(),
    )
    share_kind = "note" if is_image_note else "video"
    page_url = f"https://www.douyin.com/{share_kind}/{post_id}"
    warnings = [
        "Free public Douyin extraction failed; TikHub media fallback was used",
        "TikHub requests may incur usage charges",
        "Douyin CDN media URLs are temporary and may expire",
    ]
    media = {
        "cover_url": cover_url,
        "image_urls": image_urls if is_image_note else ([cover_url] if cover_url else []),
        "video_url": None if is_image_note else video_url,
        "audio_url": None if is_image_note else audio_url,
    }

    return SocialPost(
        platform="douyin",
        content_type="image_note" if is_image_note else "video",
        source_url=source_url,
        resolved_url=page_url,
        post_id=post_id,
        title=title,
        body=str(detail.get("desc") or "").strip(),
        author_name=author_name,
        author_id=author_profile["id"],
        publish_time=detail.get("create_time"),
        cover_url=cover_url,
        duration_sec=duration_sec,
        video_url=media["video_url"],
        image_urls=media["image_urls"],
        page_url=page_url,
        author_profile=author_profile,
        public_metrics={
            "views": statistics.get("play_count"),
            "likes": statistics.get("digg_count"),
            "comments": statistics.get("comment_count"),
            "shares": statistics.get("share_count"),
            "collects": statistics.get("collect_count"),
        },
        media=media,
        extra={
            "aweme_type": aweme_type,
            "statistics": statistics,
            "metadata_route": "tikhub.douyin.web.fetch_one_video_by_share_url",
            "metadata_provider": "tikhub",
            "media_urls_ephemeral": True,
            "may_incur_usage_cost": True,
            "warnings": warnings,
        },
    )


def _select_video_url(video: dict[str, Any]) -> Optional[str]:
    for key in ("play_addr_h264", "play_addr"):
        url = _select_address_url(video.get(key))
        if url:
            return url
    for rendition in video.get("bit_rate") or []:
        if not isinstance(rendition, dict):
            continue
        url = _select_address_url(rendition.get("play_addr"))
        if url:
            return url
    return None


def _select_audio_url(video: dict[str, Any]) -> Optional[str]:
    for rendition in video.get("bit_rate_audio") or []:
        if not isinstance(rendition, dict):
            continue
        for key in ("audio_addr", "play_addr", "play_url"):
            url = _select_address_url(rendition.get(key))
            if url:
                return url
    return None


def _select_image_urls(detail: dict[str, Any]) -> list[str]:
    raw_images = detail.get("images") or ((detail.get("image_post_info") or {}).get("images")) or []
    output: list[str] = []
    for image in raw_images:
        if not isinstance(image, dict):
            continue
        candidates = [
            image,
            image.get("display_image"),
            image.get("owner_watermark_image"),
        ]
        url = None
        for candidate in candidates:
            if not candidate:
                continue
            url = _select_address_url(candidate)
            if url:
                break
        if url and url not in output:
            output.append(url)
    return output


def _select_address_url(value: Any) -> Optional[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, dict):
        raw_urls = value.get("url_list") or []
        if isinstance(raw_urls, list):
            candidates.extend(str(url) for url in raw_urls if url)
        for key in ("url", "uri"):
            url = value.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://", "//")):
                candidates.append(url)
    elif isinstance(value, list):
        for entry in value:
            url = _select_address_url(entry)
            if url:
                candidates.append(url)

    normalized = [_normalize_url(url) for url in candidates]
    normalized = [url for url in normalized if url]
    if not normalized:
        return None
    def rank(url: str) -> int:
        host = urlparse(url).netloc.lower()
        if host == "v3-dy-o.zjcdn.com":
            return 0
        if host.endswith("zjcdn.com") and "experiment" not in host:
            return 1
        if host.endswith("douyinvod.com"):
            return 2
        if host not in {"douyin.com", "www.douyin.com"}:
            return 3
        return 4

    return min(enumerate(normalized), key=lambda item: (rank(item[1]), item[0]))[1]


def _normalize_url(url: str) -> str:
    value = str(url).strip()
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("http://"):
        value = "https://" + value[len("http://") :]
    return value


def _duration_seconds(value: Any, *, milliseconds: bool = False) -> Optional[int]:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    if milliseconds or duration > 10_000:
        duration /= 1000
    return max(1, round(duration))
