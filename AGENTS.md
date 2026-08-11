# Agent Development Protocol

This repository is a public, reusable product. It must not depend on the Brain vault or any private user workspace.

## Product boundary

- Core package: `social_media_toolkit`.
- Thin MCP transport: `social_post_extractor_mcp`.
- Thin npm bootstrap: `package.json` plus `bin/social-media-toolkit.mjs`; it may install `uv` and must delegate the actual CLI/MCP installation to `uv tool`.
- Public read paths must not require browser automation, CDP, Playwright, or a logged-in session.
- Account-private analytics remain optional and isolated.
- Publishing/upload automation belongs in a separate future package because it has authenticated side effects.

## Invariants

1. Plain canonical-text precedence is GetNote original content → native platform subtitle → Volcengine cloud ASR.
2. Timed YouTube transcripts are a separate evidence contract: manual subtitle cues → automatic subtitle cues → timestamped Volcengine cloud ASR. Callers may explicitly force Volcengine ASR, optionally with anonymous speaker diarization and bounded public-metadata context; that mode must bypass native captions. Non-timestamped GetNote text must not short-circuit either route.
3. Non-empty GetNote original content wins even when a task still contains a stale error message on the plain canonical-text route.
4. `inspect` and plain `text` do not download persistent media. Timed text requires an explicit output directory and may write only requested transcript artifacts; media remains temporary. Downloads require an explicit output directory.
5. Every normalized result preserves provenance, warnings, and platform limitations.
6. SDK, CLI, and MCP must call the same `SocialMediaToolkit`; do not add legacy aliases or a second scheduler.
7. Volcengine is the only ASR provider. Do not add local ASR or another cloud fallback.
8. Never claim a public comment sample is a global platform ranking.
9. A plain `text`, text-enabled `capture`, timed-text, or full-chain smoke-test request runs its configured text route automatically. Do not ask for a second authorization; report the route and any possible usage charge afterward.
10. The NPX path must be a real tested installer, not a documentation alias. It must not collect credentials, load project secrets, or duplicate Python runtime behavior in JavaScript.

## Secrets

- Never commit, print, log, or paste secret values.
- Do not ask users to send keys through chat.
- Do not create or load project `.env` or secret config files.
- The public runtime must work from standard process environment variables alone. Agent Switch is an optional maintainer integration; never import it or make it a package/runtime requirement.
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
- Keep persistent downloads explicit. GetNote processing and temporary Volcengine ASR are the documented default side effects of text requests; do not add a confirmation gate, and never hide which route ran.
- Avoid browser launches, unrelated account actions, or unreported paid ASR calls.
- Unit tests must use synthetic fixtures and must not contain real cookies, tokens, or private content.

## Completion gate

Run all of the following before claiming completion:

```bash
uv sync
uv run python -m unittest discover -s tests
uv run python -m compileall social_media_toolkit social_post_extractor_mcp
uv build
npm test
npm pack --dry-run
git diff --check
```

For a release, also run live smoke tests for Douyin, Xiaohongshu, Bilibili, and YouTube. When the test includes text, run configured GetNote and Volcengine ASR without asking for separate authorization. Report side effects, possible charges, and blocked platforms honestly; never weaken tests or fabricate success.
