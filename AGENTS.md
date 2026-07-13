# Agent Development Protocol

This repository is a public, reusable product. It must not depend on the Brain vault or any private user workspace.

## Product boundary

- Core package: `social_media_toolkit`.
- Thin MCP transport: `social_post_extractor_mcp`.
- Public read paths must not require browser automation, CDP, Playwright, or a logged-in session.
- Account-private analytics remain optional and isolated.
- Publishing/upload automation belongs in a separate future package because it has authenticated side effects.

## Invariants

1. Text precedence is GetNote original content → native platform subtitle → Volcengine cloud ASR.
2. Non-empty GetNote original content wins even when a task still contains a stale error message.
3. `inspect` and `text` do not download persistent media. Downloads require an explicit output directory.
4. Every normalized result preserves provenance, warnings, and platform limitations.
5. SDK, CLI, and MCP must call the same `SocialMediaToolkit`; do not add legacy aliases or a second scheduler.
6. Volcengine is the only ASR provider. Do not add local ASR or another cloud fallback.
7. Never claim a public comment sample is a global platform ranking.

## Secrets

- Never commit, print, log, or paste secret values.
- Do not ask users to send keys through chat.
- Do not create or load project `.env` or secret config files.
- Public documentation may list secret names only and should recommend an OS/client secret manager.
- On the maintainer machine, Agent Switch is the sole source of truth:
  - inspect names with `agent-switch secret list`;
  - run `agent-switch doctor` before MCP configuration changes;
  - use `agent-switch reconcile` for generated native MCP configuration.

## Change discipline

- Work from first principles and fix the root cause.
- Keep platform-specific extraction behind adapters and all routing inside the one toolkit service.
- Keep the normalized `PostBundle` schema stable; version breaking schema changes.
- No network call or file write at import time.
- Avoid hidden persistent downloads, browser launches, account actions, or unreported paid ASR calls.
- Unit tests must use synthetic fixtures and must not contain real cookies, tokens, or private content.

## Completion gate

Run all of the following before claiming completion:

```bash
uv sync
uv run python -m unittest discover -s tests
uv run python -m compileall social_media_toolkit social_post_extractor_mcp
uv build
git diff --check
```

For a release, also run authorized, read-only smoke tests for Douyin, Xiaohongshu, Bilibili, and YouTube. Report blocked platforms honestly; never weaken tests or fabricate success.
