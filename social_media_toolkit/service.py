from __future__ import annotations

import os
import shutil
from typing import Any, Optional, Sequence

from social_post_extractor_mcp.social_extractor import (
    DEFAULT_ASR_MODEL,
    DEFAULT_ASR_PROVIDER,
    BilibiliPlatformAdapter,
    DouyinPlatformAdapter,
    ExtractionContext,
    SocialExtractorService,
    SocialPost,
    XiaoHongShuPlatformAdapter,
    default_model_for_provider,
    fetch_douyin_public_comments,
    provider_asr_config,
    provider_volcengine_speech_config,
)

from .downloader import MediaDownloader
from .models import PostBundle, TextExtractionResult
from .platforms.youtube import YouTubePlatformAdapter
from .providers.getnote import GETNOTE_INSTALL_HINT, GetNoteTextProvider


class SocialMediaToolkit:
    """Public, side-effect-explicit API for social content extraction.

    Metadata inspection never writes files. Text extraction uses the following
    precedence: GetNote, native platform subtitles, then configured cloud ASR.
    Media is downloaded only through :meth:`download` or :meth:`capture` with
    an explicit ``output_dir``.
    """

    def __init__(
        self,
        *,
        extractor: Optional[SocialExtractorService] = None,
        getnote: Optional[GetNoteTextProvider] = None,
        downloader: Optional[MediaDownloader] = None,
    ) -> None:
        self.extractor = extractor or SocialExtractorService(
            platform_adapters=[
                DouyinPlatformAdapter(),
                XiaoHongShuPlatformAdapter(),
                BilibiliPlatformAdapter(),
                YouTubePlatformAdapter(),
            ]
        )
        self.getnote = getnote or GetNoteTextProvider()
        self.downloader = downloader or MediaDownloader()

    def inspect(self, url: str) -> dict[str, Any]:
        """Return a normalized PostBundle without downloading media."""
        post = self.extractor.parse_social_post(url)
        return PostBundle.from_social_post(post).to_dict()

    def get_text(
        self,
        url: str,
        *,
        prefer_getnote: bool = True,
        getnote_wait_sec: int = 300,
        getnote_interval_sec: int = 25,
        asr_provider: Optional[str] = None,
        asr_model: Optional[str] = None,
        _post: Optional[SocialPost] = None,
    ) -> dict[str, Any]:
        """Extract canonical text with deterministic provider precedence."""
        warnings: list[str] = []

        if prefer_getnote:
            try:
                getnote_result = self.getnote.extract(
                    url,
                    wait_sec=getnote_wait_sec,
                    interval_sec=getnote_interval_sec,
                )
            except Exception as exc:
                warnings.append(f"GetNote failed unexpectedly: {exc}")
            else:
                if getnote_result.success:
                    result = TextExtractionResult(
                        status="success",
                        provider="getnote",
                        text=getnote_result.text,
                        platform=_post.platform if _post else self._infer_platform(url),
                        post_id=_post.post_id if _post else None,
                        title=getnote_result.title or (_post.title if _post else None),
                        warnings=list(getnote_result.warnings),
                        metadata={
                            "route": "getnote.web_page.content",
                            "note_id": getnote_result.note_id,
                            "task_id": getnote_result.task_id,
                            "attempts": getnote_result.attempts,
                        },
                    )
                    return result.to_dict()
                warnings.extend(getnote_result.warnings)

        try:
            post = _post or self.extractor.parse_social_post(url)
        except Exception as exc:
            return TextExtractionResult(
                status="error",
                provider=None,
                text=None,
                platform=self._infer_platform(url),
                warnings=_dedupe(warnings + [f"Platform extraction failed: {exc}"]),
                metadata={"route": "failed_before_platform_fallback"},
            ).to_dict()

        native_subtitle = (post.extra or {}).get("subtitle_text")
        if isinstance(native_subtitle, str) and native_subtitle.strip():
            return TextExtractionResult(
                status="success",
                provider="platform_subtitle",
                text=native_subtitle.strip(),
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                warnings=_dedupe(warnings),
                metadata={
                    "route": f"{post.platform}.native_subtitle",
                    "subtitle": (post.extra or {}).get("subtitle") or {},
                },
            ).to_dict()

        if post.content_type != "video":
            body = (post.body or "").strip()
            if body:
                return TextExtractionResult(
                    status="success",
                    provider="platform_body",
                    text=body,
                    platform=post.platform,
                    post_id=post.post_id,
                    title=post.title,
                    warnings=_dedupe(warnings),
                    metadata={"route": f"{post.platform}.body"},
                ).to_dict()
            return TextExtractionResult(
                status="error",
                provider=None,
                text=None,
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                warnings=_dedupe(warnings + ["The post has no usable text body"]),
                metadata={"route": "no_text_available"},
            ).to_dict()

        resolved_provider = asr_provider or os.getenv("ASR_PROVIDER") or DEFAULT_ASR_PROVIDER
        resolved_model = (
            asr_model
            or os.getenv("ASR_MODEL")
            or default_model_for_provider(resolved_provider, "asr")
            or DEFAULT_ASR_MODEL
        )
        provider = self.extractor.asr_providers.get(resolved_provider)
        if provider is None:
            return TextExtractionResult(
                status="error",
                provider="cloud_asr",
                text=None,
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                warnings=_dedupe(warnings + [f"ASR provider is not supported: {resolved_provider}"]),
                metadata={"route": "cloud_asr", "asr_provider": resolved_provider, "asr_model": resolved_model},
            ).to_dict()

        context = ExtractionContext(asr_provider=resolved_provider, asr_model=resolved_model)
        try:
            transcript = provider.transcribe(post, context)
        except Exception as exc:
            return TextExtractionResult(
                status="error",
                provider="cloud_asr",
                text=None,
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                warnings=_dedupe(warnings + [f"Cloud ASR failed: {exc}"]),
                metadata={"route": "cloud_asr", "asr_provider": resolved_provider, "asr_model": resolved_model},
            ).to_dict()

        transcript = (transcript or "").strip()
        return TextExtractionResult(
            status="success" if transcript else "error",
            provider="cloud_asr",
            text=transcript or None,
            platform=post.platform,
            post_id=post.post_id,
            title=post.title,
            warnings=_dedupe(warnings + ([] if transcript else ["Cloud ASR returned empty text"])),
            metadata={"route": "cloud_asr", "asr_provider": resolved_provider, "asr_model": resolved_model},
        ).to_dict()

    def get_comments(self, url: str, *, sort_by: str = "likes", limit: int = 10) -> dict[str, Any]:
        """Return public comments when the platform adapter supports them."""
        post = self.extractor.parse_social_post(url)
        return self._comments_for_post(post, sort_by=sort_by, limit=limit)

    def download(
        self,
        url: str,
        *,
        output_dir: str,
        include: Sequence[str] | str = ("video", "cover", "images"),
    ) -> dict[str, Any]:
        """Download explicitly requested media and return a checksum manifest."""
        post = self.extractor.parse_social_post(url)
        return self.downloader.download_post(post, output_dir=output_dir, include=include)

    def capture(
        self,
        url: str,
        *,
        include_text: bool = True,
        include_comments: bool = False,
        comment_sort: str = "likes",
        comment_limit: int = 10,
        output_dir: Optional[str] = None,
        media: Sequence[str] | str = ("video", "cover", "images"),
        prefer_getnote: bool = True,
        getnote_wait_sec: int = 300,
        getnote_interval_sec: int = 25,
        asr_provider: Optional[str] = None,
        asr_model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build one normalized bundle and optionally enrich text/comments/media."""
        post = self.extractor.parse_social_post(url)
        bundle = PostBundle.from_social_post(post)

        if include_text:
            text_result = self.get_text(
                url,
                prefer_getnote=prefer_getnote,
                getnote_wait_sec=getnote_wait_sec,
                getnote_interval_sec=getnote_interval_sec,
                asr_provider=asr_provider,
                asr_model=asr_model,
                _post=post,
            )
            bundle.content.update(
                {
                    "canonical_text": text_result.get("text"),
                    "text_provider": text_result.get("provider"),
                    "transcript": text_result if text_result.get("provider") in {"platform_subtitle", "cloud_asr"} else None,
                }
            )
            bundle.provenance["routes"].append(f"text:{text_result.get('provider') or 'failed'}")
            bundle.provenance["warnings"].extend(text_result.get("warnings") or [])
            bundle.provenance["quality"] = "text_enriched" if text_result.get("status") == "success" else "metadata_only"

        if include_comments:
            if post.platform == "douyin":
                try:
                    comments = self._comments_for_post(post, sort_by=comment_sort, limit=comment_limit)
                except Exception as exc:
                    bundle.comments["coverage"] = "failed"
                    bundle.provenance["warnings"].append(f"Public comment extraction failed: {exc}")
                else:
                    bundle.comments = {
                        "items": comments.get("comments") or [],
                        "reported_total": comments.get("reported_comment_total"),
                        "coverage": comments.get("ranking_scope"),
                        "sort_by": comments.get("sort_by"),
                        "source": comments.get("source"),
                        "reply_bodies_included": comments.get("reply_bodies_included", False),
                    }
                    bundle.provenance["routes"].append("comments:douyin_public_mobile_share_api")
            else:
                bundle.provenance["warnings"].append(
                    f"Public comment extraction is not implemented for {post.platform}"
                )

        result = bundle.to_dict()
        if output_dir:
            result["downloads"] = self.downloader.download_post(post, output_dir=output_dir, include=media)
            result["provenance"]["routes"].append("media:explicit_download")
        return result

    def _comments_for_post(self, post: SocialPost, *, sort_by: str, limit: int) -> dict[str, Any]:
        if post.platform != "douyin":
            raise ValueError("Public comment extraction is currently implemented only for Douyin")
        helper = getattr(self.extractor, "get_douyin_comments_for_post", None)
        if callable(helper):
            return helper(post, sort_by=sort_by, limit=limit)
        result = fetch_douyin_public_comments(
            post.post_id,
            referer=post.page_url or post.resolved_url,
            sort_by=sort_by,
            limit=limit,
        )
        result.update(
            {
                "status": "success",
                "title": post.title,
                "author_name": post.author_name,
                "reported_comment_total": _as_int((post.public_metrics or {}).get("comments")),
                "page_url": post.page_url,
            }
        )
        return result

    def doctor(self) -> dict[str, Any]:
        """Report local capability state without exposing secret values."""
        asr_provider = os.getenv("ASR_PROVIDER") or DEFAULT_ASR_PROVIDER
        asr_secret_names = {
            "bailian": ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY"],
            "dashscope": ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY"],
            "doubao": ["DOUBAO_API_KEY", "ARK_API_KEY"],
            "siliconflow": ["SILICONFLOW_API_KEY"],
            "volcengine_speech": ["VOLCENGINE_SPEECH_APP_ID", "VOLCENGINE_SPEECH_ACCESS_TOKEN"],
        }.get(asr_provider, [])
        if asr_provider == "volcengine_speech":
            asr_configured = provider_volcengine_speech_config() is not None
        elif asr_provider in {"bailian", "dashscope", "doubao"}:
            lookup = "bailian" if asr_provider == "dashscope" else asr_provider
            asr_configured = provider_asr_config(lookup) is not None
        else:
            asr_configured = any(os.getenv(name) for name in asr_secret_names)

        getnote_available = self.getnote.available()
        getnote_authenticated = self.getnote.authenticated() if getnote_available else False
        ffmpeg_available = shutil.which("ffmpeg") is not None
        try:
            import yt_dlp  # noqa: F401

            yt_dlp_available = True
        except ImportError:
            yt_dlp_available = False

        warnings = []
        if not getnote_available:
            warnings.append(GETNOTE_INSTALL_HINT)
        elif not getnote_authenticated:
            warnings.append("GetNote is installed but not authenticated. Run: getnote auth login")
        if not ffmpeg_available:
            warnings.append("Install ffmpeg to merge separate video/audio streams")
        if not yt_dlp_available:
            warnings.append("Install yt-dlp for YouTube and Bilibili media downloads")
        if not asr_configured:
            warnings.append(
                f"Cloud ASR fallback is not configured; expected secret name(s): {', '.join(asr_secret_names) or 'provider-specific credentials'}"
            )

        return {
            "status": (
                "ready"
                if getnote_authenticated and yt_dlp_available and ffmpeg_available and asr_configured
                else "partial"
            ),
            "supported_platforms": ["douyin", "xiaohongshu", "bilibili", "youtube"],
            "text_precedence": ["getnote", "platform_subtitle", "cloud_asr"],
            "capabilities": {
                "getnote": {
                    "installed": getnote_available,
                    "authenticated": getnote_authenticated,
                    "install_hint": GETNOTE_INSTALL_HINT,
                },
                "yt_dlp": {"installed": yt_dlp_available},
                "ffmpeg": {"installed": ffmpeg_available},
                "cloud_asr": {
                    "provider": asr_provider,
                    "configured": asr_configured,
                    "expected_secret_names": asr_secret_names,
                },
            },
            "warnings": warnings,
        }

    def _infer_platform(self, url: str) -> Optional[str]:
        for adapter in self.extractor.platform_adapters:
            try:
                if adapter.can_handle(url):
                    name = adapter.__class__.__name__.lower()
                    if "douyin" in name:
                        return "douyin"
                    if "xiaohongshu" in name:
                        return "xiaohongshu"
                    if "bilibili" in name:
                        return "bilibili"
                    if "youtube" in name:
                        return "youtube"
            except Exception:
                continue
        return None


def _dedupe(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
