import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { main, uvCandidates } from "../bin/social-media-toolkit.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("npm and Python package versions stay aligned", () => {
  const npmPackage = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
  const pyproject = readFileSync(resolve(root, "pyproject.toml"), "utf8");
  const sdkInit = readFileSync(resolve(root, "social_media_toolkit", "__init__.py"), "utf8");
  const mcpInit = readFileSync(resolve(root, "social_post_extractor_mcp", "__init__.py"), "utf8");
  const pythonVersion = pyproject.match(/^version = "([^"]+)"$/m)?.[1];
  const sdkVersion = sdkInit.match(/^__version__ = "([^"]+)"$/m)?.[1];
  const mcpVersion = mcpInit.match(/^__version__ = "([^"]+)"$/m)?.[1];
  assert.equal(npmPackage.version, pythonVersion);
  assert.equal(sdkVersion, pythonVersion);
  assert.equal(mcpVersion, pythonVersion);
});

test("uv candidates prefer an explicit executable and include portable defaults", () => {
  const fixtureRoot = resolve(root, "npm-tests", "fixtures");
  const explicitUv = join(fixtureRoot, "custom", "uv");
  const installBin = join(fixtureRoot, "install-bin");
  const xdgBin = join(fixtureRoot, "xdg-bin");
  const exampleHome = join(fixtureRoot, "home");
  const uvBinary = process.platform === "win32" ? "uv.exe" : "uv";
  const candidates = uvCandidates(
    {
      SOCIAL_MEDIA_TOOLKIT_UV: explicitUv,
      UV_INSTALL_DIR: installBin,
      XDG_BIN_HOME: xdgBin,
    },
    exampleHome,
  );
  assert.equal(candidates[0], explicitUv);
  assert.ok(candidates.includes(join(installBin, uvBinary)));
  assert.ok(candidates.includes(join(xdgBin, uvBinary)));
  assert.ok(candidates.includes(join(exampleHome, ".local", "bin", uvBinary)));
});

test("dry run does not require uv or change the machine", async () => {
  const code = await main(["--dry-run"], { PATH: "" });
  assert.equal(code, 0);
});

test("unknown options fail before installation", async () => {
  await assert.rejects(() => main(["--unknown"], { PATH: "" }), /Unknown option/);
});
