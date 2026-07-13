from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from xml.etree import ElementTree

import requests

from social_post_extractor_mcp.social_extractor import HEADERS, PlatformAdapter, SocialPost, _normalize_media_url


YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
SUBTITLE_LANG_PRIORITY = ("zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh", "en")
SUBTITLE_FORMAT_PRIORITY = ("vtt", "json3", "srv3", "ttml")


class YouTubePlatformAdapter(PlatformAdapter):
    def can_handle(self, share_text: str) -> bool:
        lowered = share_text.lower()
        return any(host in lowered for host in YOUTUBE_HOSTS)

    def fetch_post(self, share_text: str) -> SocialPost:
        source_url = _extract_url(share_text)
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("YouTube support requires yt-dlp: pip install yt-dlp") from exc

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(source_url, download=False)
        except Exception as exc:
            raise RuntimeError(f"YouTube metadata extraction failed: {exc}") from exc

        if not isinstance(info, dict) or not info.get("id"):
            raise RuntimeError("YouTube returned no usable video metadata")

        video_id = str(info["id"])
        page_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        subtitle_text, subtitle_meta = _extract_youtube_subtitle(info, page_url)
        channel_id = info.get("channel_id") or info.get("uploader_id")
        author_name = info.get("channel") or info.get("uploader")
        thumbnail = _normalize_media_url(info.get("thumbnail"))
        playable_url = _select_playable_url(info)
        timestamp = info.get("timestamp") or _upload_date_epoch(info.get("upload_date"))

        public_metrics = {
            "views": info.get("view_count"),
            "likes": info.get("like_count"),
            "comments": info.get("comment_count"),
            "shares": info.get("repost_count"),
        }
        author_profile = {
            "id": channel_id,
            "name": author_name,
            "handle": info.get("uploader_id") or info.get("channel_id"),
            "avatar_url": None,
            "profile_url": info.get("channel_url") or info.get("uploader_url"),
            "followers": info.get("channel_follower_count"),
            "extra": {
                "channel_id": channel_id,
                "channel_is_verified": info.get("channel_is_verified"),
            },
        }
        media = {
            "cover_url": thumbnail,
            "image_urls": [thumbnail] if thumbnail else [],
            "video_url": playable_url,
        }
        extra = {
            "subtitle_text": subtitle_text,
            "subtitle": subtitle_meta,
            "availability": info.get("availability"),
            "live_status": info.get("live_status"),
            "categories": info.get("categories") or [],
            "language": info.get("language"),
        }
        return SocialPost(
            platform="youtube",
            content_type="video",
            source_url=source_url,
            resolved_url=page_url,
            post_id=video_id,
            title=(info.get("title") or video_id).strip(),
            body=(info.get("description") or "").strip(),
            author_name=author_name,
            author_id=channel_id,
            publish_time=timestamp,
            cover_url=thumbnail,
            duration_sec=_as_int(info.get("duration")),
            video_url=playable_url,
            image_urls=media["image_urls"],
            page_url=page_url,
            tags=list(info.get("tags") or []),
            author_profile=author_profile,
            public_metrics=public_metrics,
            media=media,
            extra=extra,
        )


def _extract_youtube_subtitle(info: dict[str, Any], referer: str) -> tuple[Optional[str], dict[str, Any]]:
    for source_name, tracks_by_language in (
        ("manual", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    ):
        selected = _select_subtitle_track(tracks_by_language)
        if not selected:
            continue
        language, track = selected
        url = track.get("url")
        if not url:
            continue
        try:
            response = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=30)
            response.raise_for_status()
            text = _parse_subtitle_payload(response.text, track.get("ext") or "vtt")
        except Exception:
            continue
        if text:
            return text, {
                "source": source_name,
                "language": language,
                "format": track.get("ext"),
            }
    return None, {}


def _select_subtitle_track(tracks_by_language: dict[str, Any]) -> Optional[tuple[str, dict[str, Any]]]:
    if not isinstance(tracks_by_language, dict) or not tracks_by_language:
        return None

    languages = list(tracks_by_language)
    ordered_languages: list[str] = []
    for preferred in SUBTITLE_LANG_PRIORITY:
        ordered_languages.extend(
            language for language in languages
            if language not in ordered_languages and (language == preferred or language.startswith(preferred + "-"))
        )
    ordered_languages.extend(language for language in languages if language not in ordered_languages)

    for language in ordered_languages:
        tracks = tracks_by_language.get(language) or []
        if not isinstance(tracks, list):
            continue
        for preferred_format in SUBTITLE_FORMAT_PRIORITY:
            for track in tracks:
                if isinstance(track, dict) and track.get("url") and track.get("ext") == preferred_format:
                    return language, track
        for track in tracks:
            if isinstance(track, dict) and track.get("url"):
                return language, track
    return None


def _parse_subtitle_payload(payload: str, extension: str) -> str:
    if extension == "json3":
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        lines = []
        for event in data.get("events") or []:
            text = "".join(str(segment.get("utf8") or "") for segment in event.get("segs") or []).strip()
            if text:
                lines.append(text)
        return _dedupe_lines(lines)

    if extension in {"ttml", "srv3"}:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return ""
        lines = ["".join(element.itertext()).strip() for element in root.iter() if element.tag.rsplit("}", 1)[-1] in {"p", "text"}]
        return _dedupe_lines(lines)

    lines = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "Kind:", "Language:")):
            continue
        if "-->" in line or re.fullmatch(r"\d+", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = html.unescape(line).strip()
        if line:
            lines.append(line)
    return _dedupe_lines(lines)


def _dedupe_lines(lines: list[str]) -> str:
    output: list[str] = []
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized and (not output or normalized != output[-1]):
            output.append(normalized)
    return "\n".join(output)


def _select_playable_url(info: dict[str, Any]) -> Optional[str]:
    if info.get("url"):
        return _normalize_media_url(info["url"])
    formats = [item for item in info.get("formats") or [] if isinstance(item, dict) and item.get("url")]
    for predicate in (
        lambda item: item.get("vcodec") not in {None, "none"} and item.get("acodec") not in {None, "none"},
        lambda item: item.get("acodec") not in {None, "none"},
        lambda item: item.get("vcodec") not in {None, "none"},
    ):
        candidates = [item for item in formats if predicate(item)]
        if candidates:
            candidates.sort(key=lambda item: (_as_int(item.get("height")), _as_int(item.get("tbr"))))
            return _normalize_media_url(candidates[-1]["url"])
    return None


def _upload_date_epoch(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        return None
    parsed = datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s]+", text)
    if not match:
        raise ValueError("No valid YouTube URL found")
    return match.group(0).rstrip(".,;)")
