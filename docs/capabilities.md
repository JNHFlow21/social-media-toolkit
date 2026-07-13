# Capability Matrix

| Capability | Douyin | Xiaohongshu | Bilibili | YouTube |
|---|---:|---:|---:|---:|
| Shared URL detection | ✅ | ✅ | ✅ | ✅ |
| Post / author metadata | ✅ | ✅ | ✅ | ✅ |
| Public metrics | ✅ | ✅ | ✅ | ✅ when exposed |
| GetNote original content | ✅ | ✅ | ✅ | ✅ |
| Native subtitles | — | — | ✅ when exposed | ✅ manual, then automatic |
| Volcengine cloud ASR | ✅ video | ✅ video | ✅ | ✅ |
| Cover download | ✅ | ✅ | ✅ | ✅ |
| Video download | ✅ direct | ✅ direct | ✅ via yt-dlp | ✅ via yt-dlp |
| Image-post download | ✅ when exposed | ✅ | — | — |
| Public comments | ✅ top-level sample | — | — | — |

## Requirements and cost

| Dependency | Used for | Cost |
|---|---|---|
| GetNote CLI | First text route | CLI is open source; service OpenAPI may require membership |
| `VOLCENGINE_ASR_API_KEY` | Video without usable text/subtitles | May incur Volcengine usage charges |
| ffmpeg | Temporary ASR audio; stream merge | Free/open source |
| yt-dlp | YouTube metadata; Bilibili/YouTube downloads | Free/open source |

## Honest limitations

- Douyin comments are at most the public endpoint's returned top-level sample;
  replies are counted but reply bodies are not fetched.
- Platform pages and public endpoints can change.
- Native subtitles are not guaranteed for a particular video.
- No local ASR is available. A Volcengine failure is terminal and explicit.
- No OCR/Vision fallback is available. GetNote failure on an image post falls
  back only to the platform's text body.
- No browser automation, logged-in analytics, publishing, or private data access.
