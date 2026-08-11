import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("npm artifact contains runtime sources and no generated Python cache", () => {
  const result = spawnSync("npm", ["pack", "--dry-run", "--json"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  assert.equal(result.status, 0, result.stderr);
  const artifact = JSON.parse(result.stdout)[0];
  const paths = artifact.files.map((item) => item.path);
  assert.ok(paths.includes("bin/social-media-toolkit.mjs"));
  assert.ok(paths.includes("pyproject.toml"));
  assert.ok(paths.includes("README.zh-CN.md"));
  assert.ok(paths.includes("docs/assets/social-preview.png"));
  assert.ok(paths.includes("CHANGELOG.md"));
  assert.ok(paths.includes("SECURITY.md"));
  assert.ok(paths.includes("social_media_toolkit/service.py"));
  assert.ok(paths.includes("social_post_extractor_mcp/server.py"));
  assert.equal(paths.some((path) => path.includes("__pycache__") || path.endsWith(".pyc")), false);
});
