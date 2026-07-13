# Social Media Toolkit

一个可复用的社交媒体内容工具包，同时提供 **Python SDK、CLI 和 MCP Server**。

它把不同平台的数据统一为 `PostBundle`，支持获取：

- 作品与作者元数据
- 正文、平台字幕与云端 ASR 转写
- 视频、封面和图片文件
- 公开互动指标
- 已支持平台的公开评论
- 每一步的数据来源与降级路径（provenance）

> 当前版本：`0.2.0` Public Alpha。公开读取路径不依赖浏览器、CDP 或 Playwright；账号私有数据仍保留为独立的旧版可选能力。

## 支持情况

| 平台 | 元数据 | 文字 | 媒体下载 | 公开评论 |
|---|---:|---:|---:|---:|
| 抖音 | ✅ | GetNote → 云端 ASR | 视频 / 封面 | ✅ 顶层公开评论，最多 10 条 |
| 小红书 | ✅ | GetNote；旧版流程支持图文视觉提取 | 视频 / 封面 / 图片 | — |
| Bilibili | ✅ | GetNote → 原生字幕 → 云端 ASR | 视频 / 封面 | — |
| YouTube | ✅ | GetNote → 人工字幕 → 自动字幕 → 云端 ASR | 视频 / 封面 | — |

评论排序只针对公开接口实际返回的样本，不宣称是平台全量评论的全局排名。

## 设计原则

1. **文字有确定优先级**：GetNote `web_page.content` → 平台原生字幕 → 云端 ASR。
2. **副作用必须显式**：`inspect` 和 `text` 不写媒体文件；只有 `download` 或带 `output_dir` 的 `capture` 会下载。
3. **统一模型，不抹平差异**：公共字段进入 `PostBundle`，平台独有字段保留在来源和 metadata 中。
4. **结果可追溯**：返回 provider、route、warning、文件 SHA-256，不伪装降级结果。
5. **公开读取与账号写操作分离**：自动发布以后应作为独立模块，不能混进只读核心。

架构与能力边界见：

- [docs/architecture.md](docs/architecture.md)
- [docs/capabilities.md](docs/capabilities.md)

## 安装

要求：Python 3.10+、`uv`。媒体合并建议安装 `ffmpeg`。

```bash
git clone https://github.com/JNHFlow21/social-post-extractor-mcp.git
cd social-post-extractor-mcp
uv sync
```

文字获取优先使用 GetNote。没有安装时，工具会返回明确提示：

```bash
npm install -g @getnote/cli
getnote auth login
```

检查本机能力：

```bash
uv run socialkit doctor
```

`doctor` 只显示依赖状态和所需 secret **名称**，不会返回 secret 值。

## CLI

### 只看统一元数据

```bash
uv run socialkit inspect "SHARE_URL"
```

### 获取文字

```bash
uv run socialkit text "SHARE_URL"
```

跳过 GetNote，直接测试平台字幕 / ASR 降级：

```bash
uv run socialkit text "SHARE_URL" --no-getnote --asr-provider bailian
```

### 获取抖音公开评论

```bash
uv run socialkit comments "DOUYIN_URL" --sort likes --limit 10
uv run socialkit comments "DOUYIN_URL" --sort recent --limit 10
```

### 显式下载媒体

```bash
uv run socialkit download "SHARE_URL" \
  --include video,cover,images \
  --output "/absolute/path/to/output"
```

返回清单包含本地绝对路径、文件大小、MIME 和 SHA-256。

### 一次生成完整数据包

```bash
uv run socialkit capture "DOUYIN_URL" \
  --comments \
  --output "/absolute/path/to/output"
```

不传 `--output` 时只返回数据，不下载媒体。

## Python SDK

```python
from social_media_toolkit import SocialMediaToolkit

toolkit = SocialMediaToolkit()

metadata = toolkit.inspect("SHARE_URL")
text = toolkit.get_text("SHARE_URL")
comments = toolkit.get_comments("DOUYIN_URL", sort_by="likes", limit=10)

bundle = toolkit.capture(
    "SHARE_URL",
    include_text=True,
    include_comments=False,
)
```

## MCP Server

启动：

```bash
uv run social-media-toolkit-mcp
```

任意支持 stdio MCP 的客户端都可以使用绝对路径配置：

```json
{
  "mcpServers": {
    "social-media-toolkit": {
      "command": "/ABSOLUTE/PATH/social-post-extractor-mcp/.venv/bin/python",
      "args": ["-m", "social_post_extractor_mcp"]
    }
  }
}
```

推荐的新工具：

| MCP Tool | 作用 |
|---|---|
| `social_inspect` | 返回统一 `PostBundle`，不下载 |
| `social_get_text` | GetNote → 原生字幕 → 云端 ASR |
| `social_download` | 显式下载媒体并返回校验清单 |
| `social_get_comments` | 获取当前已支持平台的公开评论 |
| `social_capture_bundle` | 按需合并元数据、文字、评论和媒体 |
| `social_doctor` | 检查依赖和 secret 名称 |

为避免破坏现有用户，旧工具仍然保留：

- `parse_social_post_info`
- `get_douyin_comments`
- `social_extract_transcript`
- `social_capture_url`
- `extract_social_post_script`
- `social_analyze_owner_posts`
- `parse_douyin_video_info`
- `get_douyin_download_link`
- `extract_douyin_text`

## PostBundle

所有平台统一输出以下顶层结构：

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

## 云端 ASR 与 secret

GetNote 或平台字幕不可用时，视频才会进入云端 ASR。当前兼容的 provider 包括 Bailian / DashScope、Doubao / Ark、SiliconFlow 和 Volcengine Speech。

常用 secret 名称：

- `BAILIAN_API_KEY` 或 `DASHSCOPE_API_KEY`
- `DOUBAO_API_KEY` 或 `ARK_API_KEY`
- `SILICONFLOW_API_KEY`
- `VOLCENGINE_SPEECH_APP_ID`
- `VOLCENGINE_SPEECH_ACCESS_TOKEN`

**不要把真实 secret 写进仓库、README、MCP JSON、issue 或日志。** 使用操作系统密钥管理器、MCP 客户端 secret store 或进程级安全注入。本仓库的 `.env.example` 只是名称参考，不应写入真实值。

## 开发与验证

```bash
uv sync
uv run python -m unittest discover -s tests
uv run python -m compileall social_media_toolkit social_post_extractor_mcp
uv build
```

发布前还应使用自己有权访问的四个平台链接做只读 smoke test。不要把登录 Cookie、私有响应或用户内容提交为 fixture。

## 边界

- 平台接口可能变化，返回中会保留 warning 和 provenance。
- 无登录公开接口拿不到的内容，本项目不会伪装成“已完整获取”。
- 下载和使用内容时，使用者必须遵守平台条款、版权和当地法律。
- 自动上传 / 自动发布是有账号副作用的另一类产品，后续应独立为 publisher package。

版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## License

[Apache-2.0](LICENSE)
