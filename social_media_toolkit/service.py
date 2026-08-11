"""The single public orchestrator for Social Media Toolkit."""

from __future__ import annotations

import shutil
from typing import Any, Optional, Sequence

from .downloader import MediaDownloader
from .models import PostBundle, TextExtractionResult
from .platforms.core import (
    BilibiliPlatformAdapter,
    DouyinPlatformAdapter,
    PlatformRouter,
    SocialPost,
    XiaoHongShuPlatformAdapter,
)
from .platforms.youtube import YouTubePlatformAdapter
from .providers.getnote import (
    GETNOTE_DOCS_URL,
    GETNOTE_INSTALL_HINT,
    GetNoteTextProvider,
)
from .providers.volcengine import (
    MAX_FLASH_DURATION_SECONDS,
    MAX_STANDARD_DURATION_SECONDS,
    TOS_ACCESS_KEY_SECRET_NAME,
    TOS_SECRET_KEY_SECRET_NAME,
    VOLCENGINE_ASR_DOCS_URL,
    VOLCENGINE_ASR_PRODUCT_URL,
    VOLCENGINE_ASR_RESOURCE_ID,
    VOLCENGINE_ASR_SECRET_NAME,
    VolcengineASR,
)
from .transcripts import (
    SUPPORTED_TRANSCRIPT_OUTPUTS,
    build_timed_transcript_document,
    write_timed_transcript_artifacts,
)


class SocialMediaToolkit:
    """One deterministic path for metadata, text, media, and comments.

    Plain canonical text follows this contract:

    ``GetNote original content -> native subtitle -> Volcengine cloud ASR``.

    Timed YouTube evidence follows manual cues -> automatic cues -> timestamped
    Volcengine ASR. There is no provider selector, local ASR, OCR, cleanup
    model, browser path, or implicit persistent media download.
    """

    def __init__(
        self,
        *,
        router: Optional[PlatformRouter] = None,
        getnote: Optional[GetNoteTextProvider] = None,
        asr: Optional[VolcengineASR] = None,
        downloader: Optional[MediaDownloader] = None,
    ) -> None:
        self.router = router or PlatformRouter(
            platform_adapters=[
                DouyinPlatformAdapter(),
                XiaoHongShuPlatformAdapter(),
                BilibiliPlatformAdapter(),
                YouTubePlatformAdapter(),
            ]
        )
        self.getnote = getnote or GetNoteTextProvider()
        self.asr = asr or VolcengineASR()
        self.downloader = downloader or MediaDownloader()

    def inspect(self, url: str) -> dict[str, Any]:
        """Return a normalized PostBundle without downloading media or running ASR."""
        return PostBundle.from_social_post(self.router.parse(url)).to_dict()

    def get_text(
        self,
        url: str,
        *,
        timed: bool = False,
        output_dir: Optional[str] = None,
        outputs: Sequence[str] | str = SUPPORTED_TRANSCRIPT_OUTPUTS,
        force_asr: bool = False,
        speaker_info: bool = False,
        asr_context: Optional[dict[str, Any]] = None,
        _post: Optional[SocialPost] = None,
    ) -> dict[str, Any]:
        """Get canonical text, or explicitly request durable timed artifacts.

        The default route is unchanged and may use GetNote. ``timed=True`` is
        a different evidence contract: it requires an output directory, skips
        non-timestamped GetNote text, and preserves YouTube cue or ASR timing.
        """
        if timed:
            if not output_dir:
                return TextExtractionResult(
                    status="error",
                    provider=None,
                    text=None,
                    platform=self._infer_platform(url),
                    warnings=["Timed transcript mode requires output_dir"],
                    metadata={"route": "timed_transcript.output_required"},
                ).to_dict()
            return self.get_timed_transcript(
                url,
                output_dir=output_dir,
                outputs=outputs,
                force_asr=force_asr,
                speaker_info=speaker_info,
                asr_context=asr_context,
                _post=_post,
            )

        if force_asr or speaker_info or asr_context:
            return TextExtractionResult(
                status="error",
                provider=None,
                text=None,
                platform=self._infer_platform(url),
                warnings=["ASR forcing, speaker diarization, and ASR context require timed mode"],
                metadata={"route": "asr_options.timed_required"},
            ).to_dict()

        warnings: list[str] = []

        try:
            getnote_result = self.getnote.extract(url)
        except Exception as exc:
            warnings.append(f"GetNote failed: {exc}")
        else:
            if getnote_result.success:
                return TextExtractionResult(
                    status="success",
                    provider="getnote",
                    text=getnote_result.text,
                    platform=_post.platform if _post else self._infer_platform(url),
                    post_id=_post.post_id if _post else None,
                    title=getnote_result.title or (_post.title if _post else None),
                    warnings=list(getnote_result.warnings),
                    metadata={
                        "route": "getnote.original_content",
                        "note_id": getnote_result.note_id,
                        "task_id": getnote_result.task_id,
                    },
                ).to_dict()
            warnings.extend(getnote_result.warnings)

        try:
            post = _post or self.router.parse(url)
        except Exception as exc:
            return TextExtractionResult(
                status="error",
                provider=None,
                text=None,
                platform=self._infer_platform(url),
                warnings=_dedupe(warnings + [f"Platform extraction failed: {exc}"]),
                metadata={"route": "platform_extraction_failed"},
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

        try:
            transcript = self.asr.transcribe(post).strip()
        except Exception as exc:
            return TextExtractionResult(
                status="error",
                provider=self.asr.provider_name,
                text=None,
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                warnings=_dedupe(warnings + [str(exc)]),
                metadata={
                    "route": "volcengine.cloud_asr_failed",
                    "secret_name": VOLCENGINE_ASR_SECRET_NAME,
                    "docs_url": VOLCENGINE_ASR_DOCS_URL,
                    "local_fallback": False,
                },
            ).to_dict()

        if not transcript:
            return TextExtractionResult(
                status="error",
                provider=self.asr.provider_name,
                text=None,
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                warnings=_dedupe(warnings + ["Volcengine cloud ASR returned empty text"]),
                metadata={"route": "volcengine.cloud_asr_empty", "local_fallback": False},
            ).to_dict()

        return TextExtractionResult(
            status="success",
            provider=self.asr.provider_name,
            text=transcript,
            platform=post.platform,
            post_id=post.post_id,
            title=post.title,
            warnings=_dedupe(warnings),
            metadata={
                "route": "volcengine.bigmodel_flash",
                "resource_id": VOLCENGINE_ASR_RESOURCE_ID,
                "local_fallback": False,
            },
        ).to_dict()

    def get_timed_transcript(
        self,
        url: str,
        *,
        output_dir: str,
        outputs: Sequence[str] | str = SUPPORTED_TRANSCRIPT_OUTPUTS,
        force_asr: bool = False,
        speaker_info: bool = False,
        asr_context: Optional[dict[str, Any]] = None,
        _post: Optional[SocialPost] = None,
    ) -> dict[str, Any]:
        """Write a YouTube transcript whose intervals map to the source video."""
        try:
            post = _post or self.router.parse(url)
        except Exception as exc:
            return {
                "status": "error",
                "provider": None,
                "platform": self._infer_platform(url),
                "warnings": [f"Platform extraction failed: {exc}"],
                "metadata": {"route": "timed_transcript.platform_extraction_failed"},
            }

        if post.platform != "youtube":
            return {
                "status": "error",
                "provider": None,
                "platform": post.platform,
                "post_id": post.post_id,
                "title": post.title,
                "warnings": ["Timed transcript mode currently supports YouTube URLs only"],
                "metadata": {"route": "timed_transcript.unsupported_platform"},
            }

        if (speaker_info or asr_context) and not force_asr:
            return {
                "status": "error",
                "provider": None,
                "platform": post.platform,
                "post_id": post.post_id,
                "title": post.title,
                "warnings": ["Speaker diarization and ASR context require force_asr=True"],
                "metadata": {"route": "timed_transcript.force_asr_required"},
            }

        subtitle_meta = (post.extra or {}).get("timed_subtitle") or {}
        native_segments = list(post.transcript_segments or [])
        segments = list(native_segments)
        words = list(post.transcript_words or [])
        warnings: list[str] = []
        temp_media_deleted = True
        speaker_diarization: dict[str, Any] | None = None
        asr_config: dict[str, Any] | None = None
        if segments and not force_asr:
            subtitle_source = str(subtitle_meta.get("source") or "native")
            provider = "platform_subtitle"
            route = f"youtube.{subtitle_source}_subtitle_timed"
            timing_precision = str(subtitle_meta.get("timing_precision") or "caption_cue")
            duration_ms = max(0, int(post.duration_sec or 0) * 1000)
            temporary_media = "not_created"
        else:
            try:
                timeline = self.asr.transcribe_timed(
                    post,
                    speaker_info=speaker_info,
                    context=asr_context,
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "provider": self.asr.provider_name,
                    "platform": post.platform,
                    "post_id": post.post_id,
                    "title": post.title,
                    "warnings": [str(exc)],
                    "metadata": {
                        "route": "volcengine.cloud_asr_timed_failed",
                        "secret_name": VOLCENGINE_ASR_SECRET_NAME,
                        "local_fallback": False,
                        "getnote_used": False,
                    },
                }
            segments = list(timeline.get("segments") or [])
            words = list(timeline.get("words") or [])
            warnings.extend(timeline.get("warnings") or [])
            provider = self.asr.provider_name
            route = str(timeline.get("route") or "volcengine.bigmodel_flash_timed")
            timing_precision = str(timeline.get("timing_precision") or "asr_utterance")
            duration_ms = int(timeline.get("duration_ms") or 0)
            speaker_diarization = dict(timeline.get("speaker_diarization") or {})
            asr_config = dict(timeline.get("asr_config") or {})
            temp_media_deleted = bool(timeline.get("temp_media_deleted", True))
            temporary_media = "deleted" if temp_media_deleted else "unknown"

        try:
            document = build_timed_transcript_document(
                platform=post.platform,
                post_id=post.post_id,
                title=post.title,
                source_url=post.page_url or post.resolved_url or post.source_url,
                original_url=post.source_url,
                duration_ms=duration_ms,
                provider=provider,
                route=route,
                timing_precision=timing_precision,
                segments=segments,
                words=words,
                speaker_diarization=speaker_diarization,
                asr_config=asr_config,
            )
            artifacts = write_timed_transcript_artifacts(
                document,
                output_dir=output_dir,
                outputs=outputs,
            )
        except Exception as exc:
            return {
                "status": "error",
                "provider": provider,
                "platform": post.platform,
                "post_id": post.post_id,
                "title": post.title,
                "warnings": _dedupe(warnings + [str(exc)]),
                "metadata": {"route": "timed_transcript.artifact_write_failed"},
            }

        return {
            "status": "success",
            "provider": provider,
            "platform": post.platform,
            "post_id": post.post_id,
            "title": post.title,
            "source_url": document["source"]["url"],
            "duration_ms": document["source"]["duration_ms"],
            "timing_precision": document["timing_precision"],
            "segment_count": document["segment_count"],
            "word_count": document["word_count"],
            "speaker_diarization": speaker_diarization,
            "asr_config": asr_config,
            "artifacts": artifacts,
            "temporary_media": temporary_media,
            "temp_media_deleted": temp_media_deleted,
            "warnings": _dedupe(warnings),
            "metadata": {
                "route": route,
                "getnote_used": False,
                "subtitle": subtitle_meta,
                "force_asr": force_asr,
                "native_subtitle_bypassed": bool(force_asr and native_segments),
                "context_provided": bool(asr_context),
                "local_fallback": False,
            },
        }

    def get_comments(self, url: str, *, sort_by: str = "likes", limit: int = 10) -> dict[str, Any]:
        post = self.router.parse(url)
        return self.router.get_douyin_comments_for_post(post, sort_by=sort_by, limit=limit)

    def download(
        self,
        url: str,
        *,
        output_dir: str,
        include: Sequence[str] | str = ("video", "cover", "images"),
    ) -> dict[str, Any]:
        post = self.router.parse(url)
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
    ) -> dict[str, Any]:
        """Create one bundle; persistent media remains opt-in through output_dir."""
        post = self.router.parse(url)
        bundle = PostBundle.from_social_post(post)

        if include_text:
            text_result = self.get_text(url, _post=post)
            bundle.content.update(
                {
                    "canonical_text": text_result.get("text"),
                    "text_provider": text_result.get("provider"),
                    "transcript": (
                        text_result
                        if text_result.get("provider") in {"platform_subtitle", self.asr.provider_name}
                        else None
                    ),
                }
            )
            bundle.provenance["routes"].append(f"text:{text_result.get('provider') or 'failed'}")
            bundle.provenance["warnings"].extend(text_result.get("warnings") or [])
            bundle.provenance["quality"] = (
                "text_enriched" if text_result.get("status") == "success" else "metadata_only"
            )

        if include_comments:
            if post.platform != "douyin":
                bundle.provenance["warnings"].append(
                    f"Public comment extraction is not implemented for {post.platform}"
                )
            else:
                try:
                    comments = self.router.get_douyin_comments_for_post(
                        post,
                        sort_by=comment_sort,
                        limit=comment_limit,
                    )
                except Exception as exc:
                    bundle.comments["coverage"] = "failed"
                    bundle.provenance["warnings"].append(f"Public comment extraction failed: {exc}")
                else:
                    bundle.comments = {
                        "items": comments.get("comments") or [],
                        "reported_total": comments.get("reported_comment_total"),
                        "coverage": comments.get("ranking_scope"),
                        "status": comments.get("status"),
                        "sort_by": comments.get("sort_by"),
                        "source": comments.get("source"),
                        "requested_limit": comments.get("requested_limit"),
                        "returned_count": comments.get("returned_count"),
                        "reply_bodies_included": comments.get("reply_bodies_included", False),
                    }
                    bundle.provenance["routes"].append("comments:douyin_public_mobile_share_api")

        result = bundle.to_dict()
        if output_dir:
            result["downloads"] = self.downloader.download_post(post, output_dir=output_dir, include=media)
            result["provenance"]["routes"].append("media:explicit_download")
        return result

    def doctor(self) -> dict[str, Any]:
        """Report readiness and setup links without exposing any secret value."""
        getnote_installed = self.getnote.available()
        getnote_authenticated = self.getnote.authenticated() if getnote_installed else False
        asr_configured = self.asr.configured()
        standard_configured = bool(
            getattr(self.asr, "standard_configured", lambda: False)()
        )
        ffmpeg_installed = shutil.which("ffmpeg") is not None
        try:
            import yt_dlp  # noqa: F401

            yt_dlp_installed = True
        except ImportError:
            yt_dlp_installed = False

        warnings: list[str] = []
        if not getnote_installed:
            warnings.append(GETNOTE_INSTALL_HINT)
        elif not getnote_authenticated:
            warnings.append("GetNote is installed but not authenticated. Run: getnote auth login")
        if not asr_configured:
            warnings.append(
                f"Volcengine cloud ASR is not configured. Required secret: {VOLCENGINE_ASR_SECRET_NAME}. "
                f"Setup: {VOLCENGINE_ASR_DOCS_URL}"
            )
        elif not standard_configured:
            warnings.append(
                "Volcengine standard ASR for 2–5 hour media is not configured. "
                f"Required secret names: {TOS_ACCESS_KEY_SECRET_NAME}, {TOS_SECRET_KEY_SECRET_NAME}."
            )
        if not ffmpeg_installed:
            warnings.append("Install ffmpeg for temporary ASR audio and merged media downloads")
        if not yt_dlp_installed:
            warnings.append("Install yt-dlp for YouTube and Bilibili support")

        return {
            "status": (
                "ready"
                if getnote_authenticated and asr_configured and ffmpeg_installed and yt_dlp_installed
                else "partial"
            ),
            "supported_platforms": ["douyin", "xiaohongshu", "bilibili", "youtube"],
            "text_route": ["getnote", "platform_subtitle", "volcengine_cloud_asr"],
            "timed_transcript": {
                "platforms": ["youtube"],
                "route": ["manual_subtitle_cues", "automatic_subtitle_cues", "volcengine_timed_asr"],
                "force_asr": True,
                "speaker_diarization": True,
                "asr_context": True,
                "max_flash_duration_seconds": MAX_FLASH_DURATION_SECONDS,
                "max_standard_duration_seconds": MAX_STANDARD_DURATION_SECONDS,
                "max_supported_duration_seconds": MAX_STANDARD_DURATION_SECONDS,
                "standard_long_recording": standard_configured,
                "over_limit_behavior": "reject_before_media_download",
                "outputs": list(SUPPORTED_TRANSCRIPT_OUTPUTS),
                "requires_output_dir": True,
                "getnote_used": False,
            },
            "local_asr_fallback": False,
            "capabilities": {
                "getnote": {
                    "installed": getnote_installed,
                    "authenticated": getnote_authenticated,
                    "install_command": "npm install -g @getnote/cli",
                    "login_command": "getnote auth login",
                    "docs_url": GETNOTE_DOCS_URL,
                    "may_require_paid_membership": True,
                },
                "volcengine_cloud_asr": {
                    "configured": asr_configured,
                    "secret_name": VOLCENGINE_ASR_SECRET_NAME,
                    "docs_url": VOLCENGINE_ASR_DOCS_URL,
                    "product_url": VOLCENGINE_ASR_PRODUCT_URL,
                    "may_incur_usage_cost": True,
                },
                "volcengine_standard_asr": {
                    "configured": standard_configured,
                    "duration_seconds": {
                        "minimum_exclusive": MAX_FLASH_DURATION_SECONDS,
                        "maximum_inclusive": MAX_STANDARD_DURATION_SECONDS,
                    },
                    "temporary_storage": "volcengine_tos",
                    "secret_names": [TOS_ACCESS_KEY_SECRET_NAME, TOS_SECRET_KEY_SECRET_NAME],
                    "temporary_object_deleted": True,
                },
                "ffmpeg": {"installed": ffmpeg_installed, "free": True},
                "yt_dlp": {"installed": yt_dlp_installed, "free": True},
            },
            "warnings": warnings,
        }

    def _infer_platform(self, url: str) -> Optional[str]:
        for adapter in self.router.platform_adapters:
            try:
                if not adapter.can_handle(url):
                    continue
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
