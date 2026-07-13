# Capability Matrix

## Public core

| Capability | Douyin | Xiaohongshu | Bilibili | YouTube |
|---|---:|---:|---:|---:|
| Detect shared URL | ✅ | ✅ | ✅ | ✅ |
| Post metadata | ✅ | ✅ | ✅ | ✅ |
| Author metadata | ✅ | ✅ | ✅ | ✅ |
| Public metrics | ✅ | ✅ | ✅ | ✅ when exposed |
| GetNote canonical text | ✅ | ✅ | ✅ | ✅ |
| Native subtitles | — | — | ✅ when exposed | ✅ manual, then automatic |
| Cloud ASR fallback | ✅ | ✅ video | ✅ | ✅ |
| Cover download | ✅ | ✅ | ✅ | ✅ |
| Video download | ✅ direct | ✅ direct | ✅ via yt-dlp | ✅ via yt-dlp |
| Image-note download | — | ✅ | — | — |
| Public comments | ✅ top-level sample | — | — | — |

## Comment semantics

The Douyin public mobile share endpoint currently returns at most ten top-level comments plus reply counts. It does not return reply bodies. `likes` and `recent` sort only the retrieved public sample.

## Legacy optional capabilities

The compatibility package still includes:

- Xiaohongshu image OCR / vision extraction.
- Markdown and JSON artifact generation.
- Browser-backed owner analytics for a user's own logged-in account.

These are not required by the new public read API and should remain isolated from it.

## Known limitations

- Platforms can change public endpoints or require additional verification.
- Native subtitles may be unavailable for a particular video.
- Some YouTube/Bilibili media requires `ffmpeg` because audio and video are separate streams.
- Cloud ASR requires a configured provider and may incur cost.
- The toolkit does not bypass access controls, retrieve private comments, or automate publishing.
