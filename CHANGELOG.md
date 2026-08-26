# Changelog

## Unreleased

- Added an optional TikHub fallback for public Douyin metadata and temporary
  CDN media resolution when the free public adapter fails.
- Kept TikHub out of the canonical text-provider chain: GetNote remains first,
  TikHub only supplies ephemeral media, and Volcengine remains the only ASR.
- Added secure `TIKHUB_API_KEY` loading from the process environment or the
  maintainer's optional Agent Switch FD path, plus explicit paid-route and
  expiring-URL provenance.
- Added resumable retries for temporary non-YouTube media downloads and prefer
  stable direct Douyin CDN candidates over redirect/experiment endpoints.
- Added synthetic video/image fixtures, service fallback coverage, doctor
  reporting, and synchronized English/Simplified Chinese documentation.

## 0.4.0 - 2026-08-11

- Added duration-aware Volcengine routing: flash ASR through two hours,
  asynchronous standard ASR through five hours using a temporary private TOS
  object, and an early unsupported error above five hours.
- Kept the same timed transcript, speaker-diarization, context, and cleanup
  contract across both ASR routes; standard-route TOS objects are deleted after
  success or failure.

- Added a dependency-free Node.js bootstrap so the public toolkit can be
  installed or updated with one NPX command from GitHub.
- The bootstrap reuses `uv` when present, installs official `uv` from
  `astral.sh` when absent, delegates to an isolated `uv tool` environment, and
  verifies the installed `socialkit` command.
- Simplified README CLI and MCP examples to use globally installed commands
  instead of repository-specific `uv run` paths.
- Documented and regression-tested the standalone public runtime: normal
  process environment variables work without Agent Switch, which remains an
  optional maintainer-only secret-manager fallback.
- Updated repository and installation URLs for the `social-media-toolkit`
  GitHub repository name.
- Installed yt-dlp's recommended default extras (including `yt-dlp-ejs`) and
  enabled Deno, Node.js, Bun, and QuickJS discovery for portable YouTube use.
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
- Added a canonical English README and synchronized Simplified Chinese README,
  an exact 1280×640 social preview, focused GitHub discovery metadata, and a
  privacy-safe Repository Pulse chart.
- Added cross-platform Python and Node.js CI, Git history secret scanning,
  contributor and security policies, issue/PR templates, dependency updates,
  and versioned GitHub Release artifacts with SHA-256 checksums.
- Hardened XML subtitle parsing against entity-expansion and external-entity payloads.
- Refreshed the locked dependency graph to patched MCP, HTTP, and parser releases.

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
