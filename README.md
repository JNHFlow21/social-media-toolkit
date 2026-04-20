# Social Post Extractor MCP

这个 MCP 用来统一采集抖音、小红书和 Bilibili 内容：提取作者信息、作品信息、公开视频指标、字幕/转写稿、小红书图文图片分析，并在需要时通过浏览器登录态拉取自己账号的复盘数据。

默认产物是：

- `script.md`：给人和 AI 继续阅读、整理、入库的内容稿
- `info.json`：给程序和 AI 使用的结构化数据，包括作者、指标、媒体、transcript、图片分析、模型信息和状态

设计原则：

- 外部视频只要 transcript 时，优先平台字幕；没有字幕才走云端 ASR。
- 小红书图文笔记走云端视觉模型分析图片。
- 自己账号复盘默认只抓数据，不做 ASR，因为原始稿通常已经在本地 `output/`。
- 不把视频作为长期文件下载到本地；视频转写走远程媒体 URL 和云端临时处理链路。
- API Key 只放本机配置或本机环境变量，不写进 Git。

---

## Prompt 1：让 AI 从零配置这个 MCP

把下面整段复制给你的 AI 助手。

````text
你现在是我的 MCP 配置工程师。请你帮我从零配置并验证 `social-post-extractor-mcp`，目标是让我可以稳定抓取抖音、小红书、Bilibili 的作者信息、作品信息、公开视频指标、字幕/转写稿，并且支持自己账号复盘数据。

仓库地址：
https://github.com/JNHFlow21/social-post-extractor-mcp

请按下面要求执行，不要只给我建议。

一、先确认前置条件

1. 检查本机是否有：
   - `git`
   - `python3`
   - `uv`
   - 一个可运行的 MCP 客户端，例如 mcporter、Claude Desktop、Codex、Claude Code 或其他支持 stdio MCP 的客户端
2. 如果我要做“自己账号复盘”，还要检查：
   - 已安装 `opencli`
   - 已安装 `bb-browser`
   - 本机浏览器已经登录抖音创作者中心、小红书创作者中心、Bilibili
   - 执行 `opencli doctor`，确认浏览器自动化环境可用
3. 如果我要做“外部内容 transcript / 小红书图文分析”，还要确认我有阿里云百炼 / DashScope 的 API Key。

二、安装仓库

如果本机还没有仓库，请执行：

```bash
git clone https://github.com/JNHFlow21/social-post-extractor-mcp.git
cd social-post-extractor-mcp
uv sync
```

如果本机已有仓库，请进入已有目录并执行：

```bash
git pull
uv sync
```

三、配置密钥

请不要把真实 API Key 写入 Git 仓库，也不要提交 `.env`。

优先使用这个本机密钥文件：

```bash
mkdir -p ~/.mcporter/secrets
cp .env.example ~/.mcporter/secrets/social-post-extractor.env
chmod 600 ~/.mcporter/secrets/social-post-extractor.env
```

然后让我把真实值填进去：

```bash
ASR_PROVIDER=bailian
ASR_MODEL=paraformer-v2
VISION_PROVIDER=bailian
VISION_MODEL=qwen3-vl-flash
CLEAN_PROVIDER=bailian
CLEAN_MODEL=qwen-flash
BAILIAN_API_KEY=我的真实百炼或DashScope API Key
```

如果我有自定义 URL，也一起写入：

```bash
BAILIAN_BASE_URL=我的DashScope兼容Base URL
BAILIAN_ASR_URL=我的ASR专用URL
```

如果我没有自定义 URL，就不要强行配置 URL，使用默认 DashScope 地址。

四、配置 MCP 客户端

如果我使用 mcporter，请先执行：

```bash
mcporter config list
```

确认当前有效配置文件。通常是：

```bash
~/.mcporter/mcporter.json
```

把下面这个 MCP server 加进去。注意把路径替换成本机真实路径：

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

说明：

- server 名可以叫 `douyin`，这是为了兼容旧调用；它实际支持抖音、小红书和 Bilibili。
- 如果你把 server 名改成 `social-post-extractor`，后面的 MCP 调用前缀也要同步改。
- 不建议把真实 API Key 直接写进 MCP JSON；本仓库会自动读取 `~/.mcporter/secrets/social-post-extractor.env`。

如果我使用 Claude Desktop、Codex、Claude Code 或其他 MCP 客户端，请使用同样的 stdio 配置结构：`command` 用 `/bin/zsh`，`args` 用 `cd 仓库目录 && exec .venv/bin/python -m social_post_extractor_mcp`。

五、运行验证

先在仓库目录执行：

```bash
uv run python -m unittest discover -s tests
uv run python -m compileall social_post_extractor_mcp
```

如果我使用 mcporter，再执行：

```bash
mcporter config list
mcporter call 'douyin.parse_social_post_info(share_link: "https://www.bilibili.com/video/BV1dQXrBVECR/")'
```

如果我提供抖音、小红书、Bilibili 的真实链接，请分别测试：

```bash
mcporter call 'douyin.parse_social_post_info(share_link: "我给你的链接")'
mcporter call 'douyin.social_extract_transcript(share_link: "我给你的视频链接", output_dir: "/tmp/social-post-extract")'
mcporter call 'douyin.social_capture_url(share_link: "我给你的链接", output_dir: "/tmp/social-post-extract")'
```

如果我要测试自己账号复盘，请测试：

```bash
mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "profile")'
mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "recent_posts", limit: 5)'
mcporter call 'douyin.social_analyze_owner_posts(platform: "xiaohongshu", report_type: "recent_posts", limit: 5)'
mcporter call 'douyin.social_analyze_owner_posts(platform: "bilibili", report_type: "recent_posts", limit: 5)'
```

六、配置完成后，请你教我怎么使用

请在最终回复里用中文教我：

1. 外部视频 transcript 应该调用哪个工具。
2. 小红书图文笔记应该调用哪个工具。
3. 只要作者和指标、不需要 ASR 时应该调用哪个工具。
4. 自己账号复盘应该调用哪个工具。
5. `script.md` 和 `info.json` 会保存在哪里。
6. 我以后给你链接时，你会如何判断走“外部素材提取”还是“自己账号复盘”。
7. 如果失败，我应该把哪些错误信息发给你排查。

七、必须遵守的规则

1. 不要把真实 API Key 写进 README、代码、测试或任何 Git 会提交的文件。
2. 不要长期下载和保存视频文件到本地。
3. 处理别人的视频时，可以走云端 ASR；处理我自己账号复盘时，默认只抓数据，不做 ASR。
4. 小红书图文笔记要保留正文、图片 URL 和云端视觉分析结果。
5. 所有结果都要尽量保留结构化字段，不要只给自然语言摘要。
6. 如果 MCP 配置、浏览器登录态或 API Key 有问题，请先定位问题并修复，再继续测试。
````

---

## Prompt 2：只把这个 MCP 接入已有 mcporter

如果你已经有仓库和 mcporter，把下面复制给 AI。

````text
请你只做一件事：把 `social-post-extractor-mcp` 接入我现有的 mcporter 配置，并完成 smoke test。

仓库路径：
请先帮我查找，常见位置是：
- 当前目录
- `~/Code/social-post-extractor-mcp`
- `~/Library/Mobile Documents/com~apple~CloudDocs/Codex/social-post-extractor-mcp`

要求：

1. 运行 `mcporter config list`，确认当前有效配置文件。
2. 检查仓库是否能运行：
   ```bash
   uv sync
   uv run python -m unittest discover -s tests
   ```
3. 检查是否存在：
   ```bash
   ~/.mcporter/secrets/social-post-extractor.env
   ```
4. 如果不存在，请根据 `.env.example` 创建它，并让我填入真实 API Key。
5. 在有效 mcporter 配置里添加这个 server：
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
6. 执行 smoke test：
   ```bash
   mcporter call 'douyin.parse_social_post_info(share_link: "https://www.bilibili.com/video/BV1dQXrBVECR/")'
   ```
7. 如果 smoke test 成功，请告诉我以后怎么调用；如果失败，请给出明确的失败原因和下一步修复动作。
````

---

## Prompt 3：让 AI 用真实链接完整测试

把你要测试的抖音、小红书、Bilibili 链接和下面这段一起发给 AI。

````text
请你用 `social-post-extractor-mcp` 测试我下面提供的所有链接。你要覆盖这些 use case：

1. 抖音公开视频：提取标题、作者、点赞、评论、转发、收藏、播放量/可见指标、媒体信息。
2. 抖音视频 transcript：优先平台可用字幕，没有字幕才走云端 ASR。
3. 小红书视频笔记：提取正文、作者、点赞、评论、收藏、分享、视频 transcript。
4. 小红书图文笔记：提取正文、作者、指标、图片 URL，并用云端视觉模型分析图片内容。
5. Bilibili 视频：提取标题、作者、播放、点赞、投币、收藏、弹幕、评论、字幕；没有字幕时再走云端 ASR。
6. 如果链接属于我自己的账号，还要额外测试 `social_analyze_owner_posts` 能否拿到创作者中心的复盘数据。

测试时请分别调用：

```bash
mcporter call 'douyin.parse_social_post_info(share_link: "链接")'
mcporter call 'douyin.social_extract_transcript(share_link: "视频链接", output_dir: "/tmp/social-post-extract")'
mcporter call 'douyin.social_capture_url(share_link: "链接", output_dir: "/tmp/social-post-extract")'
```

测试结果请按表格汇总：

- 平台
- 链接
- 是否成功
- post_id / BV 号
- 标题
- 作者
- 关键指标
- transcript 或图片分析是否成功
- 输出文件路径
- 失败原因和修复动作

不要只说“可以用”。必须给出实际调用结果和失败项。

下面是我的链接：

【把链接粘贴在这里】
````

---

## Prompt 4：让 AI 把外部素材提取进知识库

适合处理“别人的访谈、公开视频、小红书图文笔记”，目标是保存 transcript 和结构化信息。

````text
请你使用 `social-post-extractor-mcp` 把我给的外部社交平台内容整理成可进入知识库的素材。

输入链接可能来自：
- 抖音
- 小红书
- Bilibili

处理规则：

1. 统一调用：
   ```bash
   mcporter call 'douyin.social_capture_url(share_link: "链接", output_dir: "指定输出目录")'
   ```
2. 视频内容：
   - 优先使用平台字幕。
   - 没有字幕时使用云端 ASR。
   - 不要长期下载视频到本地。
3. 小红书图文：
   - 提取正文。
   - 保存图片 URL。
   - 使用云端视觉模型分析每张图片。
4. 输出必须包含：
   - `script.md`
   - `info.json`
5. 入库时保留双向链接：
   - 原始素材文件链接到后续 wiki/复盘/输出文档。
   - 后续文档也要能链接回原始素材。
6. 不要把 transcript 直接总结掉；先保留原文，再在需要时另开摘要或分析。

请先完成抓取和文件生成，然后告诉我：

- 内容保存在哪里
- transcript 或图片分析是否完整
- 作者和指标提取到了哪些字段
- 下一步可以如何进入知识库
````

---

## Prompt 5：让 AI 做自己账号复盘

适合处理“我自己的账号数据”。默认不做 ASR，只抓创作者中心数据，并和本地 `output/` 的原始稿或 transcript 对齐。

````text
请你使用 `social-post-extractor-mcp` 做我自己的账号复盘。

我的账号名：
【填写账号名，例如 AI杰瑞斯 / AI 杰瑞斯】

平台：
【douyin / xiaohongshu / bilibili，可以多个】

复盘规则：

1. 默认不要对我自己的作品跑云端 ASR。
2. 先用 `social_analyze_owner_posts` 抓创作者中心或账号后台数据：
   ```bash
   mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "profile")'
   mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "recent_posts", limit: 20)'
   mcporter call 'douyin.social_analyze_owner_posts(platform: "xiaohongshu", report_type: "recent_posts", limit: 20)'
   mcporter call 'douyin.social_analyze_owner_posts(platform: "bilibili", report_type: "recent_posts", limit: 20)'
   ```
3. 如果需要单篇详情，再调用：
   ```bash
   mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "post_detail", post_id: "作品ID")'
   ```
4. 把抓到的数据和本地 `output/` 里的原始文章、口播稿、视频 transcript 对齐。
5. 对每条作品生成或更新对应的 `_review.md`，不要把复盘数据塞回原稿正文里。
6. `_review.md` 要链接回原始 `output/` 文件；原始 `output/` 文件也要能链接到对应复盘文档。
7. 复盘重点：
   - 曝光 / 播放
   - 点击或进入主页
   - 点赞
   - 评论
   - 收藏
   - 转发
   - 涨粉 / 掉粉
   - 完播率或可用的留存指标
   - 作品标题、Hook、结构和数据表现之间的关系

如果浏览器登录态、opencli 或 bb-browser 失败，请先修复自动化环境，再继续复盘。
````

---

## Prompt 6：排错

MCP 配置失败、ASR 失败、作者指标缺失时，把下面复制给 AI。

````text
请你帮我排查 `social-post-extractor-mcp`。

你必须按顺序检查：

1. 当前目录是否是正确仓库：
   ```bash
   pwd
   git remote -v
   git status --short --branch
   ```
2. Python 环境：
   ```bash
   uv sync
   uv run python -m unittest discover -s tests
   uv run python -m compileall social_post_extractor_mcp
   ```
3. MCP 客户端配置：
   ```bash
   mcporter config list
   ```
   确认 server 名、command、args、仓库路径是否正确。
4. 密钥文件：
   ```bash
   ls -l ~/.mcporter/secrets/social-post-extractor.env
   ```
   只确认文件存在和权限，不要把真实 API Key 打印到聊天里。
5. API provider：
   - `ASR_PROVIDER=bailian`
   - `ASR_MODEL=paraformer-v2`
   - `VISION_PROVIDER=bailian`
   - `VISION_MODEL=qwen3-vl-flash`
   - `CLEAN_PROVIDER=bailian`
   - `CLEAN_MODEL=qwen-flash`
6. 外部链接解析：
   ```bash
   mcporter call 'douyin.parse_social_post_info(share_link: "链接")'
   ```
7. transcript 或图文分析：
   ```bash
   mcporter call 'douyin.social_capture_url(share_link: "链接", output_dir: "/tmp/social-post-extract")'
   ```
8. 自己账号复盘环境：
   ```bash
   opencli doctor
   mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "profile")'
   ```

输出时请给我：

- 失败发生在哪一层：仓库、依赖、MCP 配置、API Key、平台登录态、链接解析、云端模型、输出文件
- 证据：关键命令的结果摘要
- 修复动作：你已经做了什么，还需要我做什么
- 修复后重新跑的验证命令
````

---

## MCP 工具速查

推荐工具：

- `social_capture_url`：统一采集抖音、小红书、Bilibili，生成 `script.md` 和 `info.json`
- `social_extract_transcript`：只关心视频 transcript 时使用
- `parse_social_post_info`：只要作者、作品、公开视频指标、媒体信息时使用，不跑 ASR
- `social_analyze_owner_posts`：自己账号复盘数据，依赖浏览器登录态、`opencli`、`bb-browser`

兼容旧工具：

- `parse_douyin_video_info`
- `get_douyin_download_link`
- `extract_douyin_text`

---

## 环境变量速查

最小配置：

```bash
ASR_PROVIDER=bailian
ASR_MODEL=paraformer-v2
VISION_PROVIDER=bailian
VISION_MODEL=qwen3-vl-flash
CLEAN_PROVIDER=bailian
CLEAN_MODEL=qwen-flash
BAILIAN_API_KEY=your_bailian_or_dashscope_api_key
```

可选配置：

```bash
DASHSCOPE_API_KEY=your_dashscope_api_key
BAILIAN_BASE_URL=custom_base_url
DASHSCOPE_BASE_URL=custom_base_url
BAILIAN_ASR_URL=custom_asr_url
DASHSCOPE_ASR_URL=custom_asr_url
DASHSCOPE_SHORT_ASR_MODEL=qwen3-asr-flash
DASHSCOPE_LONG_ASR_MODEL=qwen3-asr-flash-filetrans
DASHSCOPE_SHORT_MAX_DURATION_SEC=300
```

推荐密钥位置：

```bash
~/.mcporter/secrets/social-post-extractor.env
```

---

## 常用调用

只解析信息：

```bash
mcporter call 'douyin.parse_social_post_info(share_link: "https://www.bilibili.com/video/BV1dQXrBVECR/")'
```

只提取 transcript：

```bash
mcporter call 'douyin.social_extract_transcript(share_link: "视频链接", output_dir: "/tmp/social-post-extract")'
```

完整采集：

```bash
mcporter call 'douyin.social_capture_url(share_link: "链接", output_dir: "/tmp/social-post-extract")'
```

自己账号复盘：

```bash
mcporter call 'douyin.social_analyze_owner_posts(platform: "douyin", report_type: "recent_posts", limit: 10)'
```

---

## License

Apache 2.0. See [LICENSE](./LICENSE).
