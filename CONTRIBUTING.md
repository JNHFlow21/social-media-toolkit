# Contributing to Social Media Toolkit

Thank you for helping make public social-media extraction more portable,
auditable, and honest.

## Before opening a change

- Use a GitHub Issue for bugs, platform breakage, or a focused feature proposal.
- Security vulnerabilities belong in a
  [private security advisory](https://github.com/JNHFlow21/social-media-toolkit/security/advisories/new),
  not a public issue.
- Authenticated publishing, private analytics, browser automation, local ASR,
  and a second orchestration layer are outside this repository's product
  boundary.

## Development setup

Requirements:

- Python 3.10–3.12
- [`uv`](https://docs.astral.sh/uv/)
- Node.js 18+
- `ffmpeg` only for ASR and merged-media integration testing

```bash
git clone https://github.com/JNHFlow21/social-media-toolkit.git
cd social-media-toolkit
uv sync --locked
npm test
uv run python -m unittest discover -s tests
```

## Architecture rules

1. `SocialMediaToolkit` remains the only workflow orchestrator used by the SDK,
   CLI, and MCP transport.
2. Public read paths must not require cookies, a browser profile, or a logged-in
   account.
3. `inspect` must not start GetNote, ASR, or a persistent download. When the
   free Douyin parser fails, it may call optional TikHub only if its key is
   configured and must disclose the paid, ephemeral-media route.
4. Persistent media and transcript files require an explicit output directory.
5. Every result preserves provenance, warnings, platform limitations, and any
   provider cost/temporary-URL disclosure.
6. Secret values must never appear in source, fixtures, logs, command
   arguments, documentation, or issue content.
7. Tests use synthetic fixtures. Do not commit real posts, chats, cookies,
   account data, or private media.

See [docs/architecture.md](docs/architecture.md) and
[docs/capabilities.md](docs/capabilities.md) before changing a route or result
contract.

## Required validation

Run the full local gate:

```bash
uv sync --locked
uv run python -m unittest discover -s tests
uv run python -m compileall social_media_toolkit social_post_extractor_mcp
uv build
npm test
npm pack --dry-run
git diff --check
```

When platform behavior changes, include a deterministic synthetic regression
test. A live smoke test can support the change, but it cannot replace the
fixture or be committed with private output.

## Pull requests

Keep pull requests narrow and include:

- the user-visible problem and boundary;
- the commands run and their exit state;
- any network, credential, cost, or persistence change;
- synchronized English and Simplified Chinese documentation for changed public
  behavior;
- a changelog entry when the change is user-visible.

Maintainers may request changes when a claim exceeds available evidence or a
new path depends on machine-local state.

## Repository Pulse maintenance

Stars, forks, and commit count refresh with the standard GitHub Actions token.
GitHub restricts clone and visitor Traffic endpoints to repository
Administration read access. To refresh those owner-only aggregates on schedule,
set the Actions secret `REPOSITORY_METRICS_TOKEN` to a fine-grained token that
is limited to this repository and grants only **Administration: read**. If the
secret is absent, the workflow keeps the checked-in, dated aggregate snapshot;
it never fabricates traffic or exposes referrers, paths, or visitor identities.
