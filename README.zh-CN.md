# Social Media Toolkit

<p align="center">
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/JNHFlow21/social-media-toolkit/actions/workflows/ci.yml"><img alt="持续集成状态" src="https://github.com/JNHFlow21/social-media-toolkit/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/JNHFlow21/social-media-toolkit/releases"><img alt="最新 GitHub Release" src="https://img.shields.io/github/v/release/JNHFlow21/social-media-toolkit?display_name=tag&sort=semver"></a>
  <a href="LICENSE"><img alt="Apache 2.0 许可证" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.10 或更高版本" src="https://img.shields.io/badge/python-%3E%3D3.10-3776AB.svg">
  <img alt="安装器需要 Node.js 18 或更高版本" src="https://img.shields.io/badge/installer-Node.js%20%3E%3D18-339933.svg">
  <img alt="Model Context Protocol Server" src="https://img.shields.io/badge/MCP-server-6C47FF.svg">
</p>

![Social Media Toolkit 架构：公开社交媒体链接通过统一工具包转换成规范化 JSON、逐字稿、媒体和 MCP 工具](docs/assets/social-preview.png)

> **成熟度：Alpha。** `PostBundle` 已版本化，但公开平台页面和接口可能随时变化。

把抖音、小红书、Bilibili 和 YouTube 的公开链接，统一转换成文字、元数据、视频、封面、图片和公开评论；YouTube 还支持可回到原视频的 MD/SRT/JSON 时间轴逐字稿。

同时提供：

- Python SDK
- `socialkit` CLI
- MCP Server

当前版本：`0.4.0`。

## 一行安装

机器上有 Node.js 18+（自带 `npm` / `npx`）即可：

```bash
npx -y github:JNHFlow21/social-media-toolkit
```

安装完成后直接使用：

```bash
socialkit doctor
```

这条命令会：

1. 使用本机已有的 `uv`；如果没有，就通过 [Astral 官方安装器](https://docs.astral.sh/uv/getting-started/installation/) 安装 `uv`。
2. 通过隔离的 `uv tool` 环境安装 Python 包及 `yt-dlp-ejs`，不污染业务项目。
3. 安装 `socialkit`、`social-media-toolkit-mcp` 两个主要命令。
4. 不要求 clone 仓库，不要求 Agent Switch，也不会创建项目 `.env`。

重复运行同一条命令就是覆盖安装/更新。卸载：

```bash
uv tool uninstall social-media-toolkit
```

如果安装后当前终端暂时找不到 `socialkit`，重新打开终端即可；安装器会把 `uv tool` 的命令目录加入后续终端的 `PATH`。

<details>
<summary>开发者手动安装</summary>

```bash
git clone https://github.com/JNHFlow21/social-media-toolkit.git
cd social-media-toolkit
uv sync
uv run socialkit doctor
```

</details>

## 第一个可观察结果

只解析公开元数据，不下载媒体，也不触发 GetNote 或 ASR：

```bash
socialkit inspect "SHARE_URL"
```

返回的是统一且带 provenance 的 `PostBundle`。下面是合成示例，真实字段取决于源站公开了什么：

```json
{
  "schema_version": "1.0",
  "source": {"platform": "youtube", "url": "https://example.invalid/public-post"},
  "post": {"title": "Example public post"},
  "media": {"videos": [], "covers": [], "images": [], "audio": []},
  "content": {},
  "comments": {},
  "provenance": {"routes": ["platform:public"]}
}
```

## 独立运行：不依赖 Agent Switch

这是一个标准 Python 开源项目。任何用户都可以直接安装和运行，**不需要 Agent Switch，也不依赖作者的本地工作区、Skill 或私有配置**。

- 元数据读取、公开媒体下载、公开评论，以及带原生字幕的 Bilibili/YouTube 文字提取，不需要火山 API Key。
- GetNote 是可选的第一文字来源；未安装或未登录时会自动继续走平台原生字幕或火山 ASR。
- 只有进入火山云 ASR 时才需要标准进程环境变量 `VOLCENGINE_ASR_API_KEY`。
- Agent Switch 只是维护者机器上的可选 secret-manager 适配；代码始终优先读取标准环境变量，找不到 Agent Switch 也能正常运行。
- 项目不会读取仓库 `.env`，也不会依赖任何机器专属路径。

## 按需配置的两项增强能力

### 1. GetNote：优先获取现成文字

```bash
npm install -g @getnote/cli
getnote auth login
```

- 官方说明：[@getnote/cli](https://www.npmjs.com/package/@getnote/cli)
- 源码：[iswalle/getnote-cli](https://github.com/iswalle/getnote-cli)
- 费用：GetNote CLI 本身开源，但其 OpenAPI / Skill 当前需要得到大脑会员。
- 凭据：由 GetNote 自己管理，不要复制到本项目。

GetNote 没有安装、没有登录或处理失败时，工具会继续检查平台原生字幕；视频仍无字幕时才进入火山云 ASR。

### 默认执行规则

调用 `text`、启用文字的 `capture`，或者要求进行完整链路测试，即表示执行当前已配置的文字链路：GetNote → 平台原生字幕 → 火山云 ASR。工具不会再弹出二次授权确认。

- GetNote 可能会把链接保存到用户自己的 GetNote 账号。
- 火山 ASR 可能消耗资源包或产生按量费用。
- 执行结果必须说明实际命中了哪条路径以及是否调用了火山 ASR。
- 如果只想读取元数据且不触发 GetNote / ASR，请使用 `inspect`。

### 2. 火山引擎云 ASR：唯一语音转写服务

本项目只认一个 secret 名称：

```text
VOLCENGINE_ASR_API_KEY
```

- 接口文档：[大模型录音文件识别极速版 API](https://www.volcengine.com/docs/6561/1631584?lang=zh)
- 标准版接口文档：[大模型录音文件识别标准版 API](https://www.volcengine.com/docs/6561/1354868?lang=zh)
- 产品页面：[豆包语音识别](https://www.volcengine.com/product/asr)
- 费用：云服务可能产生按量费用或消耗资源包；是否有试用额度以火山引擎控制台当前显示为准。
- 本项目使用：`volc.bigasr.auc_turbo`，与 `cloud-transcript` Skill 的火山云转写路径保持一致。

时长路由是固定的：`≤2h` 使用极速版，`2h–5h` 使用标准版，`>5h`
在下载媒体和调用 ASR 前直接拒绝。标准版要求一个临时 TOS 对象作为火山服务可下载的
音频 URL；任务完成或失败后都会删除该对象。除 `VOLCENGINE_ASR_API_KEY` 外，
标准版还需要 `TOS_ACCESS_KEY`、`TOS_SECRET_KEY`，以及非敏感配置
`TOS_BUCKET`、`TOS_REGION`、`TOS_ENDPOINT`。这些配置既可来自进程环境，也可将
非敏感部分写入 `~/.config/social-media-toolkit/config.json` 的
`volcengine_tos` 对象；TOS 密钥不得写入该文件或项目 `.env`。

请通过操作系统、MCP 客户端或 Agent 的 secret manager 注入，**不要创建项目 `.env`**。

普通 shell 用户可以通过隐藏输入把 Key 只放入当前进程环境；该方式不需要 Agent Switch，也不会把 Key 写进命令历史：

```bash
read -s VOLCENGINE_ASR_API_KEY
export VOLCENGINE_ASR_API_KEY
socialkit doctor
```

使用结束后可执行 `unset VOLCENGINE_ASR_API_KEY`。MCP 用户应通过客户端自己的 secret store 或安全环境注入同名变量。

如果本机已经使用 Agent Switch，也可以选择通过隐藏输入写入；这只是可选集成：

```bash
read -s VOLCENGINE_ASR_API_KEY
printf %s "$VOLCENGINE_ASR_API_KEY" | agent-switch secret set --stdin VOLCENGINE_ASR_API_KEY
unset VOLCENGINE_ASR_API_KEY
```

云 ASR 失败时会直接返回具体错误。**不会切到本地 Whisper，不会切到其他云厂商，也不会让用户无提示地继续等待。**

## 支持哪些功能

| 功能 | 作用 | 需要什么 | 是否可能付费 |
|---|---|---|---|
| 统一元数据 | 标题、作者、发布时间、互动指标、媒体地址 | Python 依赖；YouTube 需要 `yt-dlp` | 否 |
| 获取文字 | GetNote → 原生字幕 → 火山云 ASR | GetNote；无字幕视频需要 `VOLCENGINE_ASR_API_KEY` 和 `ffmpeg` | GetNote 会员、火山 ASR 可能付费 |
| YouTube 时间轴逐字稿 | 默认：人工字幕 cue → 自动字幕 cue → 火山云 ASR；可显式强制 ASR + 匿名说话人分离；输出 MD/SRT/JSON | `yt-dlp`、`ffmpeg`；2–5h 另需临时 TOS 配置 | 调用火山 ASR/TOS 时可能付费 |
| 下载视频 | 下载完整视频并生成 SHA-256 清单 | 抖音/小红书走公开 CDN；B站/YouTube 需要 `yt-dlp` 和 `ffmpeg` | 工具本身免费 |
| 下载封面/图片 | 保存封面和图文图片 | 公开链接 | 工具本身免费 |
| 获取评论 | 获取抖音公开接口返回的一级评论样本 | 不需要登录或 Cookie | 否 |
| 完整数据包 | 合并元数据、文字、评论和按需下载 | 取决于启用的能力 | 取决于 GetNote/火山 ASR |
| 环境检查 | 检查依赖、登录状态和 secret 名称 | 无 | 否 |

### 平台矩阵

| 平台 | 元数据 | 文字 | 视频 | 封面/图片 | 公开评论 |
|---|---:|---:|---:|---:|---:|
| 抖音 | ✅ | GetNote → 火山 ASR | ✅ | ✅，含公开图集 | ✅ 可请求 1–100 条一级评论样本；源站可能少返回 |
| 小红书 | ✅ | GetNote → 图文正文 / 火山 ASR | ✅ | ✅ | — |
| Bilibili | ✅ | GetNote → 原生字幕 → 火山 ASR | ✅ | ✅ | — |
| YouTube | ✅ | GetNote → 人工字幕 → 自动字幕 → 火山 ASR | ✅ | ✅ | — |

评论的 `likes` / `recent` 排序，只针对公开接口实际返回的样本，不代表平台全量评论的全局排名。`--limit`、MCP `limit` 和 bundle `comment_limit` 接受 `1..100`，表示最多返回多少条。源站给多少就返回多少：请求 20、源站给 19，就返回 19，不翻页、不补抓。

## 唯一文字处理路径

```mermaid
flowchart LR
    U["公开链接"] --> G["GetNote 原始内容"]
    G -->|"没有可用文字"| N["平台原生字幕"]
    N -->|"视频仍无字幕"| V["火山引擎云 ASR"]
    V -->|"失败"| E["直接返回失败原因"]
```

没有以下路径：

- 本地 Whisper / 本地 ASR
- 其他云 ASR provider
- OCR / Vision 模型兜底
- LLM 清洗或改写
- 浏览器、CDP、Playwright、登录态抓取
- 自动生成 `script.md` / `info.json`

文字结果不会下载持久媒体。火山转写需要的音频只存在于临时目录：YouTube
通过 yt-dlp 的重试/续传路径获取临时音频，其他平台读取公开媒体 URL，再用
`ffmpeg` 转成单声道 16kHz MP3、调用云端、随后删除临时目录。

### YouTube 时间轴逐字稿

普通 `text` 追求“拿到可读的 canonical text”，因此 GetNote 可以优先命中。时间轴模式追求“每句话能回到原视频”，所以是另一条确定性链路：

```text
YouTube 人工字幕 cue → YouTube 自动字幕 cue → 火山云 ASR utterance/word 时间轴
```

没有时间码的 GetNote 文本不会截断时间轴模式。该模式要求显式输出目录，只持久化请求的逐字稿文件；用于 ASR 的视频/音频始终在临时目录中并在调用结束后删除。

需要同一套音频统一做匿名说话人分离时，调用方可显式传入
`--force-asr --speaker-info`。这会绕过已有 YouTube 字幕，强制使用火山
ASR，并在 JSON `segments[*].speaker` 中写入 `SPEAKER_01` 这类匿名标签。
可再通过 `--asr-context-file` 传入一个非空 JSON 对象作为公开元数据词汇
上下文；context 可能改善专名转写，但不会把匿名标签映射成真实人物。

默认产物：

```text
youtube-<video-id>-transcript.md
youtube-<video-id>-transcript.srt
youtube-<video-id>-transcript.timeline.json
```

JSON 保存规范化 `segments`，火山响应包含词级边界时还会保存脱敏后的 `words`。返回清单会明确记录 provider、route、timing precision、segment count、校验哈希以及临时媒体是否删除，不保存云端原始响应或 YouTube 的临时签名媒体 URL。

## CLI

### 检查安装状态

```bash
socialkit doctor
```

输出只包含：

- 依赖是否安装
- GetNote 是否登录
- `VOLCENGINE_ASR_API_KEY` 是否配置
- 官方配置链接
- 缺失项

不会输出 secret 值。

### 解析元数据，不下载

```bash
socialkit inspect "SHARE_URL"
```

### 获取文字

```bash
socialkit text "SHARE_URL"
```

### 获取 YouTube 带时间轴逐字稿

```bash
socialkit text "YOUTUBE_URL" \
  --timed \
  --output "/absolute/path/to/transcripts" \
  --outputs md,srt,json
```

`--timed` 目前只接受单个 YouTube 视频 URL；即使链接带播放列表参数，也不会抓取整个播放列表。

强制 ASR、匿名说话人分离和公开元数据 context：

```bash
socialkit text "YOUTUBE_URL" \
  --timed \
  --force-asr \
  --speaker-info \
  --asr-context-file "/absolute/path/to/public-context.json" \
  --output "/absolute/path/to/transcripts" \
  --outputs json,md,srt
```

`--speaker-info` 和 `--asr-context-file` 必须与 `--force-asr` 同时使用。
强制路由会按时长自动选择极速版或标准版，最长接受 5 小时媒体；超过 5 小时直接
返回暂不支持，不自动分片。两条路线都保留口头重复和语气词
（`enable_ddc=false`），使用 `ssd_version=200`，且不把混合后的 YouTube
音频当作独立左右声道。

### 获取抖音公开评论

```bash
socialkit comments "DOUYIN_URL" --sort likes --limit 10
socialkit comments "DOUYIN_URL" --sort likes --limit 20
socialkit comments "DOUYIN_URL" --sort likes --limit 50
socialkit comments "DOUYIN_URL" --sort recent --limit 100
```

### 显式下载媒体

```bash
socialkit download "SHARE_URL" \
  --include video,cover,images \
  --output "/absolute/path/to/output"
```

### 生成完整数据包

```bash
socialkit capture "SHARE_URL" \
  --comments \
  --output "/absolute/path/to/output"
```

不传 `--output` 就不会持久下载媒体。

## Python SDK

```python
from social_media_toolkit import SocialMediaToolkit

toolkit = SocialMediaToolkit()

metadata = toolkit.inspect("SHARE_URL")
text = toolkit.get_text("SHARE_URL")
timed = toolkit.get_text(
    "YOUTUBE_URL",
    timed=True,
    output_dir="/absolute/path/to/transcripts",
    outputs="md,srt,json",
)
speaker_timed = toolkit.get_text(
    "YOUTUBE_URL",
    timed=True,
    output_dir="/absolute/path/to/transcripts",
    outputs="json,md,srt",
    force_asr=True,
    speaker_info=True,
    asr_context={
        "context_type": "dialog_ctx",
        "context_data": [{"text": "Podcast: Example; guest: Jane Doe"}],
    },
)
comments = toolkit.get_comments("DOUYIN_URL", sort_by="likes", limit=10)

bundle = toolkit.capture(
    "SHARE_URL",
    include_text=True,
    include_comments=False,
)
```

没有 `asr_provider`、`asr_model` 或本地 fallback 参数，避免同一链接产生多套行为。

## MCP Server

启动：

```bash
social-media-toolkit-mcp
```

stdio MCP 示例：

```json
{
  "mcpServers": {
    "social-media-toolkit": {
      "command": "social-media-toolkit-mcp"
    }
  }
}
```

不要把 secret 直接写进这段 JSON。通过客户端 secret store 或安全的进程环境注入。如果 MCP 客户端不继承 shell 的 `PATH`，运行 `uv tool dir --bin`，再把上面的 `command` 换成该目录下 `social-media-toolkit-mcp` 的绝对路径。

只保留六个 MCP Tool：

| MCP Tool | 作用 |
|---|---|
| `social_inspect` | 返回统一 `PostBundle`，不下载、不转写 |
| `social_get_text` | 默认执行 canonical text 路径；`timed=true` 时写出 YouTube MD/SRT/JSON；可显式 `force_asr`、`speaker_info`、`asr_context_json` |
| `social_get_comments` | 获取当前支持的公开评论样本 |
| `social_download` | 显式下载媒体并返回校验清单 |
| `social_capture_bundle` | 按需合并数据和下载 |
| `social_doctor` | 检查依赖和配置，仅显示 secret 名称 |

MCP、CLI 和 Python SDK 都调用同一个 `SocialMediaToolkit`，不存在第二套兼容调度器。

## PostBundle

```json
{
  "schema_version": "1.0",
  "source": {},
  "post": {},
  "author": {},
  "media": {
    "videos": [],
    "covers": [],
    "images": [],
    "audio": []
  },
  "metrics": {},
  "content": {},
  "comments": {},
  "provenance": {}
}
```

## 开发与验证

```bash
uv sync --locked
uv run python -m unittest discover -s tests
uv run python -m compileall social_media_toolkit social_post_extractor_mcp
uv build
npm test
npm pack --dry-run
git diff --check
```

测试必须使用合成 fixture，不得提交 Cookie、Token、真实用户数据或私人内容。

## 边界

- 只处理使用者有权访问的公开 URL，不绕过访问控制。
- 平台可能更改公开页面或接口；失败时返回来源和原因，不伪造成功。
- 下载、保存和再发布内容时，使用者必须遵守平台条款、版权和当地法律。
- 自动上传与自动发布属于另一个有账号副作用的产品，不在本工具包内。
- 不采集产品遥测；网络访问只用于公开平台、可选 GetNote，以及调用方显式配置的火山 ASR/TOS。
- 只有显式输出目录和可选的用户配置文件会产生持久写入；ASR 临时媒体和标准版 TOS 对象会在返回前删除。

架构和能力边界：

- [docs/architecture.md](docs/architecture.md)
- [docs/capabilities.md](docs/capabilities.md)
- [CHANGELOG.md](CHANGELOG.md)

## 兼容性与分发

- Python 3.10–3.12 是受支持的包运行时。
- Node.js 18+ 只用于一行安装器。
- CI 覆盖 macOS、Linux 和 Windows；平台端接口仍可能独立变化。
- 正式用户路径是 GitHub-backed `npx` 安装器和隔离的 `uv tool` 环境。
- GitHub Releases 提供版本化 sdist、wheel、npm tarball 和 SHA-256 校验文件。

## 仓库活动

[![JNHFlow21/social-media-toolkit Repository Pulse](https://raw.githubusercontent.com/JNHFlow21/social-media-toolkit/metrics/repository-metrics.svg)](https://github.com/JNHFlow21/social-media-toolkit)

GitHub Traffic 是带日期的滚动 14 天 owner snapshot；公开 Stars、Forks 和 Commit 数自动刷新。Clone/访客数据不等于安装成功或持续使用。

## 贡献、支持与安全

- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- Bug 与功能建议：[GitHub Issues](https://github.com/JNHFlow21/social-media-toolkit/issues)
- 安全漏洞：使用 [GitHub 私密漏洞报告](https://github.com/JNHFlow21/social-media-toolkit/security/advisories/new)，不要公开提交安全 Issue
- 安全策略：[SECURITY.md](SECURITY.md)

## License

[Apache-2.0](LICENSE)
