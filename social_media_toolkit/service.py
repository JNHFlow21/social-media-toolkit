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
    VOLCENGINE_ASR_DOCS_URL,
    VOLCENGINE_ASR_PRODUCT_URL,
    VOLCENGINE_ASR_RESOURCE_ID,
    VOLCENGINE_ASR_SECRET_NAME,
    VolcengineASR,
)


class SocialMediaToolkit:
    """One deterministic path for metadata, text, media, and comments.

    Text always follows this contract:

    ``GetNote original content -> native subtitle -> Volcengine cloud ASR``.

    There is no provider selector, local ASR, OCR, cleanup model, browser path,
    or implicit persistent download in this orchestrator.
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

    def get_text(self, url: str, *, _post: Optional[SocialPost] = None) -> dict[str, Any]:
        """Get canonical text through the one supported text route."""
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
                        "sort_by": comments.get("sort_by"),
                        "source": comments.get("source"),
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
