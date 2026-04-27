"""Environment file loading helpers."""

from __future__ import annotations

import os
from pathlib import Path


def default_env_paths(repo_root: Path) -> list[Path]:
    """Return local config files in precedence order."""
    paths = [
        repo_root / "config" / "social-post-extractor.env",
        repo_root / ".env",
        Path.home() / ".mcporter" / "secrets" / "social-post-extractor.env",
    ]
    appdata = os.getenv("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "social-post-extractor-mcp" / "config.env")
    return paths


def load_env_file(path: Path) -> None:
    """Load KEY=value or export KEY=value lines without overriding existing env."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip().strip("'\"")


def load_default_env_files(repo_root: Path) -> None:
    """Load all supported local config files without overriding existing env."""
    for path in default_env_paths(repo_root):
        load_env_file(path)
