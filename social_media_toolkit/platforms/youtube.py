from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from xml.etree import ElementTree

import requests

from .core import HEADERS, PlatformAdapter, SocialPost, _normalize_media_url
from ..transcripts import normalize_segments


YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
SUBTITLE_LANG_PRIORITY = ("zh-Hans", "zh-Hant", "zh-CN", "zh-TW", "zh", "en")
SUBTITLE_FORMAT_PRIORITY = ("vtt", "json3", "srv3", "ttml")
TIMED_SUBTITLE_FORMAT_PRIORITY = ("json3", "vtt", "srv3", "ttml")


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
        timed_segments, timed_subtitle_meta = _extract_youtube_timed_subtitle(info, page_url)
        channel_id = info.get("channel_id") or info.get("uploader_id")
        author_name = info.get("channel") or info.get("uploader")
        thumbnail = _normalize_media_url(info.get("thumbnail"))
        playable_url = _select_playable_url(info)
        audio_url = _select_audio_url(info)
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
            "audio_url": audio_url,
        }
        extra = {
            "subtitle_text": subtitle_text,
            "subtitle": subtitle_meta,
            "timed_subtitle": timed_subtitle_meta,
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
            transcript_segments=timed_segments,
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
    return _select_subtitle_track_with_preferences(
        tracks_by_language,
        language_priority=SUBTITLE_LANG_PRIORITY,
        format_priority=SUBTITLE_FORMAT_PRIORITY,
    )


def _select_subtitle_track_with_preferences(
    tracks_by_language: dict[str, Any],
    *,
    language_priority: tuple[str, ...],
    format_priority: tuple[str, ...],
) -> Optional[tuple[str, dict[str, Any]]]:
    if not isinstance(tracks_by_language, dict) or not tracks_by_language:
        return None

    languages = list(tracks_by_language)
    ordered_languages: list[str] = []
    for preferred in language_priority:
        ordered_languages.extend(
            language for language in languages
            if language not in ordered_languages and (language == preferred or language.startswith(preferred + "-"))
        )
    ordered_languages.extend(language for language in languages if language not in ordered_languages)

    for language in ordered_languages:
        tracks = tracks_by_language.get(language) or []
        if not isinstance(tracks, list):
            continue
        for preferred_format in format_priority:
            for track in tracks:
                if isinstance(track, dict) and track.get("url") and track.get("ext") == preferred_format:
                    return language, track
        for track in tracks:
            if isinstance(track, dict) and track.get("url"):
                return language, track
    return None


def _extract_youtube_timed_subtitle(
    info: dict[str, Any],
    referer: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for source_name, tracks_by_language in (
        ("manual", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    ):
        language_priority = _timed_language_priority(info.get("language"), tracks_by_language)
        selected = _select_subtitle_track_with_preferences(
            tracks_by_language,
            language_priority=language_priority,
            format_priority=TIMED_SUBTITLE_FORMAT_PRIORITY,
        )
        if not selected:
            continue
        language, track = selected
        url = track.get("url")
        if not url:
            continue
        try:
            response = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=30)
            response.raise_for_status()
            segments = _parse_timed_subtitle_payload(response.text, track.get("ext") or "vtt")
        except Exception:
            continue
        if segments:
            return segments, {
                "source": source_name,
                "language": language,
                "format": track.get("ext"),
                "timing_precision": "caption_cue",
            }
    return [], {}


def _timed_language_priority(reported_language: Any, tracks_by_language: dict[str, Any]) -> tuple[str, ...]:
    priorities: list[str] = []

    def add(value: Any) -> None:
        normalized = str(value or "").strip()
        if normalized and normalized not in priorities:
            priorities.append(normalized)

    reported = str(reported_language or "").strip()
    for language in tracks_by_language if isinstance(tracks_by_language, dict) else []:
        if str(language).endswith("-orig"):
            add(language)
    add(reported)
    if "-" in reported:
        add(reported.split("-", 1)[0])
    add("en")
    for language in SUBTITLE_LANG_PRIORITY:
        add(language)
    return tuple(priorities)


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


def _parse_timed_subtitle_payload(payload: str, extension: str) -> list[dict[str, Any]]:
    extension = (extension or "").lower()
    if extension == "json3":
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        segments = []
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            text = "".join(
                str(segment.get("utf8") or "")
                for segment in event.get("segs") or []
                if isinstance(segment, dict)
            ).strip()
            if not text:
                continue
            start_ms = _as_int(event.get("tStartMs"))
            duration_ms = _as_int(event.get("dDurationMs"))
            segments.append(
                {
                    "start_ms": start_ms,
                    "end_ms": start_ms + max(0, duration_ms),
                    "text": text,
                }
            )
        return _finalize_segment_ends(segments)

    if extension in {"ttml", "srv3"}:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return []
        segments = []
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag not in {"p", "text"}:
                continue
            text = "".join(element.itertext()).strip()
            if not text:
                continue
            start_ms = _parse_time_value_ms(element.attrib.get("begin") or element.attrib.get("start"))
            end_ms = _parse_time_value_ms(element.attrib.get("end"))
            if end_ms <= start_ms:
                duration_ms = _parse_time_value_ms(element.attrib.get("dur"))
                end_ms = start_ms + duration_ms
            segments.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
        return _finalize_segment_ends(segments)

    lines = payload.splitlines()
    segments = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        start_raw, end_raw = line.split("-->", 1)
        start_ms = _parse_time_value_ms(start_raw.strip().split()[0])
        end_ms = _parse_time_value_ms(end_raw.strip().split()[0])
        index += 1
        text_lines = []
        while index < len(lines):
            candidate = lines[index].strip()
            if "-->" in candidate:
                break
            index += 1
            if not candidate:
                if text_lines:
                    break
                continue
            candidate = re.sub(r"<[^>]+>", "", candidate)
            candidate = html.unescape(candidate).strip()
            if candidate:
                text_lines.append(candidate)
        text = " ".join(text_lines).strip()
        if text:
            segments.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})
    return _finalize_segment_ends(segments)


def _finalize_segment_ends(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments.sort(key=lambda item: (_as_int(item.get("start_ms")), _as_int(item.get("end_ms"))))
    for index, segment in enumerate(segments):
        start_ms = _as_int(segment.get("start_ms"))
        end_ms = _as_int(segment.get("end_ms"))
        next_start = (
            _as_int(segments[index + 1].get("start_ms"))
            if index + 1 < len(segments)
            else 0
        )
        if end_ms <= start_ms:
            segment["end_ms"] = max(start_ms + 1, next_start or start_ms + 2000)
        elif next_start > start_ms and end_ms > next_start:
            # YouTube automatic captions use rolling display windows whose
            # advertised cue durations overlap heavily. Keep the cue starts
            # authoritative and clamp each end to the next cue so generated
            # SRT files do not display several partial phrases at once.
            segment["end_ms"] = next_start
    return normalize_segments(segments)


def _parse_time_value_ms(value: Any) -> int:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return 0
    if raw.endswith("ms"):
        try:
            return max(0, round(float(raw[:-2])))
        except ValueError:
            return 0
    if raw.endswith("s"):
        raw = raw[:-1]
    if ":" not in raw:
        try:
            return max(0, round(float(raw) * 1000))
        except ValueError:
            return 0
    try:
        parts = [float(part) for part in raw.split(":")]
    except ValueError:
        return 0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return max(0, round(seconds * 1000))


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


def _select_audio_url(info: dict[str, Any]) -> Optional[str]:
    formats = [item for item in info.get("formats") or [] if isinstance(item, dict) and item.get("url")]
    candidates = [
        item for item in formats
        if item.get("acodec") not in {None, "none"} and item.get("vcodec") in {None, "none"}
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: _as_int(item.get("abr") or item.get("tbr")))
    return _normalize_media_url(candidates[-1]["url"])


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
