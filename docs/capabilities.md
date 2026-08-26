# Capability Matrix

| Capability | Douyin | Xiaohongshu | Bilibili | YouTube |
|---|---:|---:|---:|---:|
| Shared URL detection | ✅ | ✅ | ✅ | ✅ |
| Post / author metadata | ✅ | ✅ | ✅ | ✅ |
| Public metrics | ✅ | ✅ | ✅ | ✅ when exposed |
| GetNote original content | ✅ | ✅ | ✅ | ✅ |
| Optional TikHub media fallback | ✅ after free parser failure | — | — | — |
| Native subtitles | — | — | ✅ when exposed | ✅ manual, then automatic |
| Timed transcript artifacts | — | — | — | ✅ MD/SRT/JSON; default caption-first or forced ASR + anonymous speakers/context |
| Volcengine cloud ASR | ✅ video | ✅ video | ✅ | ✅ |
| Cover download | ✅ | ✅ | ✅ | ✅ |
| Video download | ✅ direct | ✅ direct | ✅ via yt-dlp | ✅ via yt-dlp |
| Image-post download | ✅ when exposed | ✅ | — | — |
| Public comments | ✅ top-level sample | — | — | — |

## Requirements and cost

| Dependency | Used for | Cost |
|---|---|---|
| GetNote CLI | First text route | CLI is open source; service OpenAPI may require membership |
| `TIKHUB_API_KEY` | Optional Douyin metadata/CDN fallback after free parsing fails | TikHub requests may incur charges |
| `VOLCENGINE_ASR_API_KEY` | Video without usable text/subtitles, or explicit forced ASR | May incur Volcengine usage charges |
| TOS credentials/config | Temporary private audio URL for standard ASR on 2–5 hour media | May incur TOS storage/traffic charges |
| ffmpeg | Temporary ASR audio; stream merge | Free/open source |
| yt-dlp | YouTube metadata; Bilibili/YouTube downloads | Free/open source |

## Honest limitations

- Douyin comment callers may request up to 1–100 top-level items. The toolkit
  returns whatever public sample is available, capped at the requested limit;
  it does not paginate or backfill. Replies are counted but reply bodies are
  not fetched.
- Platform pages and public endpoints can change.
- TikHub is optional and paid. Its Douyin CDN URLs are temporary and must not be
  treated as durable asset URLs; the toolkit records the route and cost warning.
- Native subtitles are not guaranteed for a particular video.
- Timed transcript mode currently supports one YouTube video URL at a time and
  requires an explicit output directory. GetNote is intentionally skipped
  because its canonical text does not guarantee source-video timecodes. Forced
  ASR uses flash through two hours and standard ASR through five hours; longer
  media is rejected without chunking. Speaker labels are anonymous voice
  clusters, not real-person identities.
- No local ASR is available. A Volcengine failure is terminal and explicit.
- No OCR/Vision fallback is available. GetNote failure on an image post falls
  back only to the platform's text body.
- No browser automation, logged-in analytics, publishing, or private data access.
