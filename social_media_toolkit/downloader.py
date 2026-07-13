from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import re
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlparse

import requests

from social_post_extractor_mcp.social_extractor import HEADERS, SocialPost


ALLOWED_MEDIA = {"video", "cover", "images", "audio"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


class MediaDownloader:
    def __init__(self, *, max_bytes: int = 2 * 1024 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes

    def download_post(
        self,
        post: SocialPost,
        *,
        output_dir: str,
        include: Sequence[str] | str = ("video", "cover", "images"),
    ) -> dict[str, Any]:
        requested = _normalize_include(include)
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        stem = _safe_stem(f"{post.platform}-{post.post_id}")
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[dict[str, Any]] = []

        def attempt(
            kind: str,
            operation: Callable[[], dict[str, Any]],
            *,
            index: Optional[int] = None,
        ) -> None:
            try:
                items.append(operation())
            except Exception as exc:
                error: dict[str, Any] = {"kind": kind, "error": str(exc)}
                if index is not None:
                    error["index"] = index
                errors.append(error)

        if "video" in requested and post.content_type == "video":
            if post.platform in {"youtube", "bilibili"}:
                attempt("video", lambda: self._download_with_ytdlp(post, destination, stem))
            elif post.video_url:
                attempt(
                    "video",
                    lambda: self._download_http(
                        post.video_url,
                        destination / f"{stem}-video",
                        kind="video",
                        referer=post.page_url or post.resolved_url,
                        default_extension=".mp4",
                    ),
                )
            else:
                warnings.append("A video was requested but the platform returned no downloadable video URL")
        elif requested == ["video"]:
            warnings.append("A video was requested for a non-video post")

        cover_url = post.cover_url or (post.media or {}).get("cover_url")
        if "cover" in requested and cover_url:
            attempt(
                "cover",
                lambda: self._download_http(
                    cover_url,
                    destination / f"{stem}-cover",
                    kind="cover",
                    referer=post.page_url or post.resolved_url,
                    default_extension=".jpg",
                ),
            )
        elif "cover" in requested:
            warnings.append("A cover was requested but the platform returned no cover URL")

        if "images" in requested:
            seen = {cover_url} if cover_url else set()
            image_index = 0
            for image_url in post.image_urls or []:
                if not image_url or image_url in seen:
                    continue
                seen.add(image_url)
                image_index += 1
                attempt(
                    "image",
                    lambda image_url=image_url, image_index=image_index: self._download_http(
                        image_url,
                        destination / f"{stem}-image-{image_index:02d}",
                        kind="image",
                        index=image_index,
                        referer=post.page_url or post.resolved_url,
                        default_extension=".jpg",
                    ),
                    index=image_index,
                )
            if image_index == 0 and (post.content_type != "video" or requested == ["images"]):
                warnings.append("Images were requested but no non-cover images were available")

        audio_url = (post.media or {}).get("audio_url") or (post.extra or {}).get("audio_url")
        if "audio" in requested and audio_url:
            attempt(
                "audio",
                lambda: self._download_http(
                    audio_url,
                    destination / f"{stem}-audio",
                    kind="audio",
                    referer=post.page_url or post.resolved_url,
                    default_extension=".m4a",
                ),
            )
        elif "audio" in requested:
            warnings.append("Audio was requested but the platform returned no separate audio URL")

        return {
            "status": "success" if not warnings and not errors else ("partial" if items else "error"),
            "platform": post.platform,
            "post_id": post.post_id,
            "output_dir": str(destination),
            "requested": requested,
            "downloaded_count": len(items),
            "items": items,
            "warnings": warnings,
            "errors": errors,
        }

    def _download_http(
        self,
        url: str,
        path_without_extension: Path,
        *,
        kind: str,
        referer: Optional[str],
        default_extension: str,
        index: Optional[int] = None,
    ) -> dict[str, Any]:
        _validate_remote_url(url)
        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer
        response = requests.get(url, headers=headers, timeout=60, stream=True, allow_redirects=True)
        try:
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip() or None
            content_length = _as_int(response.headers.get("Content-Length"))
            if content_length and content_length > self.max_bytes:
                raise RuntimeError(f"Remote media exceeds max_bytes ({content_length} > {self.max_bytes})")
            extension = _media_extension(url, content_type, default_extension)
            destination = path_without_extension.with_suffix(extension)
            digest = hashlib.sha256()
            written = 0
            with destination.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise RuntimeError(f"Downloaded media exceeds max_bytes ({self.max_bytes})")
                    digest.update(chunk)
                    file.write(chunk)
        except Exception:
            if "destination" in locals():
                destination.unlink(missing_ok=True)
            raise
        finally:
            response.close()

        result = {
            "kind": kind,
            "source_url": url,
            "local_path": str(destination),
            "bytes": written,
            "sha256": digest.hexdigest(),
            "mime_type": content_type or mimetypes.guess_type(destination.name)[0],
        }
        if index is not None:
            result["index"] = index
        return result

    def _download_with_ytdlp(self, post: SocialPost, output_dir: Path, stem: str) -> dict[str, Any]:
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("YouTube/Bilibili download requires yt-dlp: pip install yt-dlp") from exc

        page_url = post.page_url or post.resolved_url or post.source_url
        _validate_remote_url(page_url)
        prefix = f"{stem}-video"
        before = {path.resolve() for path in output_dir.glob(f"{prefix}*")}
        options = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "max_filesize": self.max_bytes,
            "outtmpl": str(output_dir / f"{prefix}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 60,
            "http_headers": {"Referer": page_url},
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(page_url, download=True)
        except Exception as exc:
            raise RuntimeError(f"yt-dlp media download failed: {exc}") from exc

        candidates = [
            path.resolve()
            for path in output_dir.glob(f"{prefix}*")
            if path.resolve() not in before
            and path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
            and not path.name.endswith((".part", ".ytdl"))
        ]
        if not candidates:
            candidates = [
                path.resolve()
                for path in output_dir.glob(f"{prefix}*")
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ]
        if not candidates:
            raise RuntimeError("yt-dlp finished but no merged video file was found")
        destination = max(candidates, key=lambda path: path.stat().st_size)
        return {
            "kind": "video",
            "source_url": page_url,
            "local_path": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": _sha256_file(destination),
            "mime_type": mimetypes.guess_type(destination.name)[0] or "video/mp4",
            "transport": "yt-dlp",
        }


def _normalize_include(include: Sequence[str] | str) -> list[str]:
    values = include.split(",") if isinstance(include, str) else list(include)
    normalized = []
    for value in values:
        item = str(value).strip().lower()
        if not item:
            continue
        if item not in ALLOWED_MEDIA:
            raise ValueError(f"Unsupported media selection: {item}. Choose from: {', '.join(sorted(ALLOWED_MEDIA))}")
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        raise ValueError("At least one media type must be selected")
    return normalized


def _safe_stem(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:120] or "social-post"


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Media URL must be an absolute HTTP(S) URL")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Local media URLs are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Private or local media IP addresses are not allowed")


def _media_extension(url: str, content_type: Optional[str], default: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix or ""):
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed if guessed and re.fullmatch(r"\.[a-z0-9]{1,5}", guessed) else default


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
