# Changelog

## Unreleased

- Added an opt-in YouTube timed-transcript contract to the existing `text`
  interface across SDK, CLI, and MCP.
- Preserved manual/automatic YouTube subtitle cue intervals and added a
  timestamped Volcengine ASR fallback with sanitized utterance/word timing.
- Added explicit MD, SRT, and timeline JSON artifacts with stable video-id
  names, SHA-256 manifests, and automatic temporary-media cleanup.
- Kept the default GetNote-first canonical-text behavior backward compatible;
  timed mode deliberately skips non-timestamped GetNote text.

- Normalize second, millisecond, and microsecond platform timestamps before
  emitting `PostBundle.post.published_at` and `published_at_epoch`.
- Treat `text`, text-enabled `capture`, and full-chain smoke-test requests as
  the execution signal for configured GetNote and Volcengine ASR, without a
  second authorization prompt.

## 0.3.0 - 2026-07-13

- Reduced SDK, CLI, and MCP to one `SocialMediaToolkit` orchestrator.
- Removed the legacy MCP aliases, artifact writer, browser-backed owner analytics,
  OCR/Vision/cleanup pipeline, and repository environment-file loader.
- Standardized the final video text route on Volcengine big-model flash ASR using
  only `VOLCENGINE_ASR_API_KEY`.
- Removed every alternate cloud-ASR provider and all provider/model selectors.
- Made cloud failure terminal and explicit; no local ASR fallback exists.
- Updated GetNote parsing for the current `web_content` original-text field while
  accepting the previous nested response shape.
- Separated Bilibili video and audio stream URLs and added Douyin image-post parsing.
- Added installation/doctor guidance with official links, requirements, and cost boundaries.

## 0.2.0 - 2026-07-13

- Introduced the public `social_media_toolkit` Python SDK.
- Added normalized, versioned `PostBundle` output.
- Added GetNote-first text routing with native subtitle and cloud ASR fallbacks.
- Added YouTube metadata and subtitle support through `yt-dlp`.
- Added explicit media downloads with MIME, size, and SHA-256 manifests.
- Added `socialkit` CLI and six new MCP tools.
- Added public Douyin top-level comment retrieval and honest sample semantics.
- Preserved legacy MCP tool names for backward compatibility.
- Reworked documentation and secret handling for public reuse.

## 0.1.0

- Initial Douyin, Xiaohongshu, and Bilibili MCP extraction workflow.
