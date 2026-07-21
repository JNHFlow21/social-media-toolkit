#!/usr/bin/env node

import {
  accessSync,
  chmodSync,
  existsSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { constants as fsConstants } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const UV_INSTALL_URLS = {
  unix: "https://astral.sh/uv/install.sh",
  windows: "https://astral.sh/uv/install.ps1",
};

export const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function executableName(name) {
  return process.platform === "win32" ? `${name}.exe` : name;
}

function isExecutable(path) {
  try {
    accessSync(path, process.platform === "win32" ? fsConstants.F_OK : fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function commandWorks(command, args = ["--version"], env = process.env) {
  const result = spawnSync(command, args, {
    env,
    encoding: "utf8",
    stdio: "pipe",
    windowsHide: true,
  });
  return !result.error && result.status === 0;
}

export function uvCandidates(env = process.env, home = homedir()) {
  const names = [];
  if (env.SOCIAL_MEDIA_TOOLKIT_UV) {
    names.push(env.SOCIAL_MEDIA_TOOLKIT_UV);
  }
  names.push("uv");

  const binary = executableName("uv");
  if (env.UV_INSTALL_DIR) {
    names.push(join(env.UV_INSTALL_DIR, binary));
  }
  if (env.XDG_BIN_HOME) {
    names.push(join(env.XDG_BIN_HOME, binary));
  }
  names.push(join(home, ".local", "bin", binary));
  names.push(join(home, ".cargo", "bin", binary));
  return [...new Set(names)];
}

export function resolveUv(env = process.env) {
  for (const candidate of uvCandidates(env)) {
    if ((candidate === "uv" || isExecutable(candidate)) && commandWorks(candidate, ["--version"], env)) {
      return candidate;
    }
  }
  return null;
}

function run(command, args, { env = process.env, capture = false } = {}) {
  const result = spawnSync(command, args, {
    env,
    encoding: "utf8",
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    windowsHide: true,
  });
  if (result.error) {
    throw new Error(`Could not run ${command}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const detail = capture ? (result.stderr || result.stdout || "").trim() : "";
    throw new Error(
      `${command} exited with code ${result.status}${detail ? `: ${detail}` : ""}`,
    );
  }
  return capture ? (result.stdout || "").trim() : "";
}

async function download(url, destination) {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`Download failed (${response.status}) from ${url}`);
  }
  writeFileSync(destination, new Uint8Array(await response.arrayBuffer()));
  if (process.platform !== "win32") {
    chmodSync(destination, 0o700);
  }
}

export async function installUv(env = process.env) {
  const installDir = env.UV_INSTALL_DIR || join(homedir(), ".local", "bin");
  const installEnv = { ...env, UV_INSTALL_DIR: installDir };
  const staging = mkdtempSync(join(tmpdir(), "social-media-toolkit-uv-"));
  const windows = process.platform === "win32";
  const script = join(staging, windows ? "install.ps1" : "install.sh");

  console.log("uv was not found; installing uv from astral.sh …");
  try {
    await download(windows ? UV_INSTALL_URLS.windows : UV_INSTALL_URLS.unix, script);
    if (windows) {
      const powershell = commandWorks("pwsh", ["-NoProfile", "-Command", "$PSVersionTable.PSVersion"], env)
        ? "pwsh"
        : "powershell.exe";
      run(
        powershell,
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
        { env: installEnv },
      );
    } else {
      run("sh", [script], { env: installEnv });
    }
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }

  const nextEnv = {
    ...installEnv,
    PATH: `${installDir}${delimiter}${env.PATH || ""}`,
  };
  const uv = resolveUv(nextEnv);
  if (!uv) {
    throw new Error(`uv installation finished, but no uv executable was found in ${installDir}`);
  }
  return { uv, env: nextEnv };
}

function printHelp() {
  console.log(`Social Media Toolkit one-command installer

Usage:
  npx -y github:JNHFlow21/social-media-toolkit

Options:
  --dry-run   Show the installation plan without changing the machine
  --help      Show this help

The installer uses an existing uv executable or installs uv from astral.sh,
then installs the socialkit CLI and social-media-toolkit-mcp with uv tool.`);
}

function pathContains(directory, env = process.env) {
  const target = resolve(directory);
  return (env.PATH || "")
    .split(delimiter)
    .filter(Boolean)
    .some((entry) => resolve(entry) === target);
}

export async function main(args = process.argv.slice(2), env = process.env) {
  if (args.includes("--help") || args.includes("-h")) {
    printHelp();
    return 0;
  }
  const unknown = args.filter((value) => value !== "--dry-run");
  if (unknown.length) {
    throw new Error(`Unknown option: ${unknown.join(", ")}. Run with --help for usage.`);
  }
  if (!existsSync(join(PACKAGE_ROOT, "pyproject.toml"))) {
    throw new Error("The npm package is missing pyproject.toml; installation cannot continue");
  }
  if (args.includes("--dry-run")) {
    console.log("Would install Social Media Toolkit with: uv tool install --force <package>");
    return 0;
  }

  let runtimeEnv = env;
  let uv = resolveUv(runtimeEnv);
  if (!uv) {
    ({ uv, env: runtimeEnv } = await installUv(runtimeEnv));
  }

  console.log("Installing Social Media Toolkit …");
  run(
    uv,
    ["tool", "install", "--force", "--quiet", "--no-config", PACKAGE_ROOT],
    { env: runtimeEnv },
  );

  const binDir = run(uv, ["tool", "dir", "--bin", "--no-config"], {
    env: runtimeEnv,
    capture: true,
  });
  const socialkit = join(binDir, executableName("socialkit"));
  if (!isExecutable(socialkit)) {
    throw new Error(`Installation completed, but socialkit was not found in ${binDir}`);
  }
  run(socialkit, ["--help"], { env: runtimeEnv, capture: true });

  let restartHint = false;
  if (!pathContains(binDir, runtimeEnv) && !runtimeEnv.UV_TOOL_BIN_DIR) {
    run(uv, ["tool", "update-shell", "--quiet", "--no-config"], { env: runtimeEnv });
    restartHint = true;
  }

  console.log("\n✓ Social Media Toolkit installed");
  console.log(`  CLI: ${join(binDir, executableName("socialkit"))}`);
  console.log(`  MCP: ${join(binDir, executableName("social-media-toolkit-mcp"))}`);
  console.log("\nNext: socialkit doctor");
  if (restartHint) {
    console.log("Open a new terminal first so the updated PATH takes effect.");
  }
  return 0;
}

const invokedDirectly = process.argv[1]
  && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));

if (invokedDirectly) {
  main().catch((error) => {
    console.error(`\nInstallation failed: ${error.message}`);
    process.exitCode = 1;
  });
}
