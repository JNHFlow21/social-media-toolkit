# Social Post Extractor MCP

统一提取抖音、小红书、Bilibili 内容的 MCP Server。它可以解析作者信息、作品信息、公开视频指标、字幕/转写稿、小红书图文图片内容，并在需要时通过浏览器登录态拉取自己账号的复盘数据。

默认产物：

- `script.md`：给人和 AI 继续阅读、整理、入库的内容稿
- `info.json`：给程序和 AI 使用的结构化数据，包括作者、指标、媒体、transcript、图片分析、模型信息和状态

## 学员怎么安装

把这个 GitHub 链接发给你的 AI Agent：

```text
https://github.com/JNHFlow21/social-post-extractor-mcp
```

然后对它说：

```text
请打开这个仓库，先读 README.md 和 AGENTS.md，然后帮我安装并配置这个 MCP。不要让我复制长提示词，请一步一步带我完成 API Key、MCP 客户端和 smoke test。
```

安装 agent 应该优先执行 [AGENTS.md](AGENTS.md) 里的流程。

## 能做什么

- 抖音：公开视频信息、作者信息、指标、视频 transcript
- 小红书：视频笔记 transcript、图文笔记正文和图片视觉分析
- Bilibili：公开视频信息、作者信息、指标、视频 transcript
- 自己账号复盘：通过本机浏览器登录态拉取作品列表、账号概览、作品详情等数据

设计原则：

- 外部视频优先使用平台字幕；没有字幕时才走云端 ASR。
- 小红书图文笔记走云端视觉模型分析图片。
- 自己账号复盘默认只抓数据，不做 ASR。
- 不把视频作为长期文件下载到本地。
- API Key 默认放在 MCP 仓库内的本机配置文件，不写进 Git。

## 前置条件

- `git`
- Python `3.10+`
- `uv`
- 一个支持 stdio MCP 的客户端，例如 mcporter、Claude Desktop、Claude Code、Codex 或 OpenClaw
- 阿里云百炼 / DashScope API Key，用于 ASR、视觉模型和清理模型

获取 API Key 的官方说明：

- 百炼 API Key 文档：https://help.aliyun.com/zh/model-studio/get-api-key
- 百炼控制台直达入口：https://bailian.console.aliyun.com/
- 百炼兼容 OpenAI Base URL 默认可用：`https://dashscope.aliyuncs.com/compatible-mode/v1`

找 API Key 的路径：

1. 打开百炼控制台直达入口。
2. 登录阿里云账号。
3. 页面右上角选择 `华北2（北京）`。
4. 进入 `API Key` / `密钥管理` 页面。
5. 点击 `创建 API Key`。
6. 归属业务空间选 `默认业务空间`。
7. 创建后复制 `sk-...` 开头的 API Key，填到 `config/social-post-extractor.env`。

如果要做“自己账号复盘”，还需要：

- 本机浏览器已经登录对应平台的创作者后台
- 可用的 browser-backed CLI 环境，例如 `opencli` / `bb-browser`

## 快速安装

```bash
git clone https://github.com/JNHFlow21/social-post-extractor-mcp.git
cd social-post-extractor-mcp
uv sync
```

如果已经克隆过：

```bash
cd social-post-extractor-mcp
git pull
uv sync
```

## 配置 API Key

不要配置系统环境变量。推荐把明文 key 放在 MCP 仓库里的本机配置文件：

```bash
mkdir -p config
cp .env.example config/social-post-extractor.env
chmod 600 config/social-post-extractor.env
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force config
Copy-Item .env.example config/social-post-extractor.env
```

然后把 `config/social-post-extractor.env` 里的占位值改成真实值：

```bash
export ASR_PROVIDER=bailian
export ASR_MODEL=paraformer-v2
export VISION_PROVIDER=bailian
export VISION_MODEL=qwen3-vl-flash
export CLEAN_PROVIDER=bailian
export CLEAN_MODEL=qwen-flash
export BAILIAN_API_KEY=sk-your-real-api-key
```

这个文件在 `.gitignore` 里，不会被提交。也兼容不带 `export` 的 `KEY=value` 写法。

服务会按顺序读取：

1. `config/social-post-extractor.env`
2. `.env`
3. `~/.mcporter/secrets/social-post-extractor.env`
4. Windows `%APPDATA%\social-post-extractor-mcp\config.env`

## MCP 客户端配置

server 名可以继续叫 `douyin`，这是为了兼容旧调用；它实际支持抖音、小红书和 Bilibili。

macOS / Linux stdio 配置示例，注意把路径替换成你本机真实路径：

```json
{
  "mcpServers": {
    "douyin": {
      "command": "/bin/zsh",
      "args": [
        "-lc",
        "cd '/ABSOLUTE/PATH/social-post-extractor-mcp' && exec '.venv/bin/python' -m social_post_extractor_mcp"
      ]
    }
  }
}
```

Windows PowerShell stdio 配置示例：

```json
{
  "mcpServers": {
    "douyin": {
      "command": "powershell",
      "args": [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "Set-Location 'C:\\ABSOLUTE\\PATH\\social-post-extractor-mcp'; & '.\\.venv\\Scripts\\python.exe' -m social_post_extractor_mcp"
      ]
    }
  }
}
```

不要把真实 API Key 直接写进 MCP JSON；本服务会自动读取 `config/social-post-extractor.env`。

## 验证

在仓库目录执行：

```bash
uv run python -m unittest discover -s tests
uv run python -m compileall social_post_extractor_mcp
```

如果使用 mcporter：

```bash
mcporter config list
mcporter call 'douyin.parse_social_post_info(share_link: "https://www.bilibili.com/video/BV1dQXrBVECR/")'
```

真实提取 transcript 时建议给 `output_dir`：

```bash
mcporter call --timeout 86400000 'douyin.social_extract_transcript(share_link: "你的抖音/小红书/Bilibili链接", output_dir: "/tmp/social-post-extract")'
```

## 工具列表

- `parse_social_post_info`：只解析作者、作品、指标和媒体信息，不做 ASR
- `social_extract_transcript`：提取视频 transcript，优先平台字幕，没有字幕时走云端 ASR
- `social_capture_url`：统一采集链接，输出 `script.md` 和 `info.json`
- `extract_social_post_script`：兼容旧入口，功能接近 `social_capture_url`
- `social_analyze_owner_posts`：拉取自己账号复盘数据，需要浏览器登录态
- `parse_douyin_video_info`、`get_douyin_download_link`、`extract_douyin_text`：旧版兼容工具

## 常见问题

如果提示 API Key 不存在：

- 检查 `config/social-post-extractor.env` 是否存在
- 检查 `BAILIAN_API_KEY` 或 `DASHSCOPE_API_KEY` 是否填了真实值
- 重启 MCP 客户端

如果 transcript 失败：

- 先用 `parse_social_post_info` 确认链接能解析
- 确认视频 URL 可访问
- 确认百炼 / DashScope 服务已开通并有额度
- 把完整错误信息发给安装 agent 排查

如果自己账号复盘失败：

- 先确认浏览器已经登录对应创作者后台
- 确认 browser-backed CLI 环境可用
- 重新运行 `social_analyze_owner_posts`

## 安全规则

- 不要提交真实 API Key。
- 不要把真实 API Key 贴到公开聊天或 issue。
- 不要长期保存别人的视频文件。
- 处理别人的公开视频时可以走云端 ASR；处理自己账号复盘时默认只抓数据。
- 结果尽量保留结构化字段，不要只保留自然语言摘要。

## License

Apache-2.0
