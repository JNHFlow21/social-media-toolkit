#!/usr/bin/env python3
"""Thin MCP transport for the single SocialMediaToolkit orchestrator."""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from social_media_toolkit import SocialMediaToolkit


mcp = FastMCP(
    "Social Media Toolkit",
    dependencies=["requests", "mcp", "yt-dlp"],
)

# Exactly one orchestrator. MCP, CLI, and Python all execute the same service.
_TOOLKIT = SocialMediaToolkit()


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _run(operation) -> str:
    try:
        return _json(operation())
    except Exception as exc:
        return _json({"status": "error", "error": str(exc)})


@mcp.tool()
def social_inspect(share_link: str) -> str:
    """Parse one public URL into PostBundle; never download media or run ASR."""
    return _run(lambda: _TOOLKIT.inspect(share_link))


@mcp.tool()
def social_get_text(
    share_link: str,
    timed: bool = False,
    output_dir: Optional[str] = None,
    outputs: str = "md,srt,json",
) -> str:
    """Get canonical text, or write timed YouTube MD/SRT/JSON artifacts."""
    return _run(
        lambda: _TOOLKIT.get_text(
            share_link,
            timed=timed,
            output_dir=output_dir,
            outputs=outputs,
        )
    )


@mcp.tool()
def social_get_comments(
    share_link: str,
    sort_by: str = "likes",
    limit: int = 10,
) -> str:
    """Get supported public comments; currently Douyin top-level sample only."""
    return _run(lambda: _TOOLKIT.get_comments(share_link, sort_by=sort_by, limit=limit))


@mcp.tool()
def social_download(
    share_link: str,
    output_dir: str,
    include: str = "video,cover,images",
) -> str:
    """Explicitly download selected media and return a checksum manifest."""
    return _run(lambda: _TOOLKIT.download(share_link, output_dir=output_dir, include=include))


@mcp.tool()
def social_capture_bundle(
    share_link: str,
    include_text: bool = True,
    include_comments: bool = False,
    comment_sort: str = "likes",
    comment_limit: int = 10,
    output_dir: Optional[str] = None,
    media: str = "video,cover,images",
) -> str:
    """Build one PostBundle and optionally attach comments and explicit downloads."""
    return _run(
        lambda: _TOOLKIT.capture(
            share_link,
            include_text=include_text,
            include_comments=include_comments,
            comment_sort=comment_sort,
            comment_limit=comment_limit,
            output_dir=output_dir,
            media=media,
        )
    )


@mcp.tool()
def social_doctor() -> str:
    """Show dependency/configuration status and secret names, never secret values."""
    return _run(_TOOLKIT.doctor)


@mcp.prompt()
def social_media_toolkit_guide() -> str:
    """Canonical setup and routing guide."""
    return """
# Social Media Toolkit

Only six tools are supported:
- `social_inspect`
- `social_get_text`
- `social_get_comments`
- `social_download`
- `social_capture_bundle`
- `social_doctor`

Text always follows one route:
1. GetNote original content
2. Native Bilibili/YouTube subtitle when available
3. Volcengine cloud ASR using `VOLCENGINE_ASR_API_KEY`

For a YouTube evidence transcript, call `social_get_text` with `timed=true`
and an absolute `output_dir`. This skips non-timestamped GetNote text, preserves
manual/automatic subtitle cues, and falls back to timestamped Volcengine ASR.
It writes the requested `md,srt,json` artifacts and removes temporary media.

There is no local ASR and no alternate cloud-ASR provider. If Volcengine ASR
fails, return its error directly. Run `social_doctor` after installation for
setup commands and official links. Media writes and timed transcript artifacts
require an explicit `output_dir`.
"""


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
