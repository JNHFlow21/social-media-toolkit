import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { main, uvCandidates } from "../bin/social-media-toolkit.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("npm and Python package versions stay aligned", () => {
  const npmPackage = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
  const pyproject = readFileSync(resolve(root, "pyproject.toml"), "utf8");
  const pythonVersion = pyproject.match(/^version = "([^"]+)"$/m)?.[1];
  assert.equal(npmPackage.version, pythonVersion);
});

test("uv candidates prefer an explicit executable and include portable defaults", () => {
  const candidates = uvCandidates(
    {
      SOCIAL_MEDIA_TOOLKIT_UV: "/custom/uv",
      UV_INSTALL_DIR: "/install/bin",
      XDG_BIN_HOME: "/xdg/bin",
    },
    "/home/example",
  );
  assert.equal(candidates[0], "/custom/uv");
  assert.ok(candidates.includes("/install/bin/uv"));
  assert.ok(candidates.includes("/xdg/bin/uv"));
  assert.ok(candidates.includes("/home/example/.local/bin/uv"));
});

test("dry run does not require uv or change the machine", async () => {
  const code = await main(["--dry-run"], { PATH: "" });
  assert.equal(code, 0);
});

test("unknown options fail before installation", async () => {
  await assert.rejects(() => main(["--unknown"], { PATH: "" }), /Unknown option/);
});
