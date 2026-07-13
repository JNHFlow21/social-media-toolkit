# Agent Reach Integration

`Agent Reach` can call this project through any stdio MCP client such as `mcporter`.

## Call chain

`Agent Reach skill` → MCP client → `social-media-toolkit` server → public toolkit / compatibility adapters

The historical MCP alias `douyin` can remain in an existing client configuration. The server itself now supports Douyin, Xiaohongshu, Bilibili, and YouTube.

## MCP configuration

Use an absolute interpreter path and keep credentials out of this JSON:

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

Inject optional cloud-provider credentials with the MCP client's secret manager or an OS-level secret manager. Expected names are documented in `README.md`; values must never be written here.

## Preferred tools

- `social_inspect`: normalized metadata, no persistent download.
- `social_get_text`: GetNote → native subtitle → cloud ASR.
- `social_download`: explicit media download with checksum manifest.
- `social_get_comments`: supported public comments.
- `social_capture_bundle`: combined normalized bundle.

Legacy tools remain available for old Agent Reach prompts:

- `parse_social_post_info`
- `social_extract_transcript`
- `social_capture_url`
- `extract_social_post_script`
- `parse_douyin_video_info`
- `get_douyin_download_link`
- `extract_douyin_text`

## Verification

After changing the client configuration:

1. Run `uv run socialkit doctor`.
2. List the MCP tool schemas from the client.
3. Run an authorized metadata-only link for each required platform.
4. Test text extraction separately so any cloud ASR cost is explicit.
5. Test downloads only with an explicit temporary output directory.

Historical smoke-test claims must not be treated as current proof after platform or dependency changes.
