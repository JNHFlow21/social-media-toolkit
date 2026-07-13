from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


GETNOTE_INSTALL_HINT = "Install GetNote first: npm install -g @getnote/cli, then run: getnote auth login"


@dataclass
class GetNoteResult:
    status: str
    text: Optional[str] = None
    title: Optional[str] = None
    note_id: Optional[str] = None
    task_id: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def success(self) -> bool:
        return bool(self.text)


class GetNoteTextProvider:
    """GetNote-first text provider without storing credentials in this project."""

    def __init__(
        self,
        executable: str = "getnote",
        *,
        runner: Optional[Callable[[list[str], int], dict[str, Any]]] = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.executable = executable
        self._runner = runner or self._run_json
        self._sleeper = sleeper
        self._clock = clock

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def authenticated(self, *, timeout_sec: int = 15) -> bool:
        """Check auth without returning or logging any credential material."""
        if not self.available():
            return False
        try:
            proc = subprocess.run(
                [self.executable, "auth", "status"],
                text=True,
                capture_output=True,
                timeout=timeout_sec,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        output = f"{proc.stdout}\n{proc.stderr}".lower()
        rejected = "not authenticated" in output or "unauthenticated" in output
        return proc.returncode == 0 and "authenticated" in output and not rejected

    def extract(
        self,
        url: str,
        *,
        wait_sec: int = 300,
        interval_sec: int = 25,
        command_timeout_sec: int = 300,
        min_content_chars: int = 1,
    ) -> GetNoteResult:
        if not self.available():
            return GetNoteResult(status="unavailable", warnings=[GETNOTE_INSTALL_HINT])

        attempts = 1
        save_payload = self._runner(
            [self.executable, "save", url, "-o", "json"],
            command_timeout_sec,
        )
        content = _note_content(save_payload)
        task_id = _task_id(save_payload)
        note_id = _note_id(save_payload)
        if len(content) >= min_content_chars:
            return GetNoteResult(
                status="success",
                text=content,
                title=_note_title(save_payload),
                note_id=note_id,
                task_id=task_id,
                attempts=attempts,
            )

        if not task_id and not note_id:
            warning = _payload_error(save_payload) or "GetNote returned no content and no follow-up task/note id"
            if _looks_like_auth_error(warning):
                warning = f"{warning}. Run: getnote auth login"
            return GetNoteResult(status="failed", warnings=[warning], attempts=attempts)

        deadline = self._clock() + max(wait_sec, 0)
        last_task = save_payload
        while True:
            if note_id:
                attempts += 1
                note_payload = self._runner(
                    [self.executable, "note", str(note_id), "-o", "json"],
                    command_timeout_sec,
                )
                content = _note_content(note_payload)
                if len(content) >= min_content_chars:
                    warnings = []
                    stale_error = _payload_error(last_task)
                    if stale_error:
                        warnings.append(f"GetNote stale task message ignored because content is ready: {stale_error}")
                    return GetNoteResult(
                        status="success",
                        text=content,
                        title=_note_title(note_payload),
                        note_id=note_id,
                        task_id=task_id,
                        warnings=warnings,
                        attempts=attempts,
                    )

            if task_id:
                attempts += 1
                last_task = self._runner(
                    [self.executable, "task", str(task_id), "-o", "json"],
                    command_timeout_sec,
                )
                note_id = _note_id(last_task) or note_id

            if self._clock() >= deadline:
                warning = _payload_error(last_task) or "GetNote did not produce web_page.content before the wait budget expired"
                return GetNoteResult(
                    status="failed",
                    note_id=note_id,
                    task_id=task_id,
                    warnings=[warning],
                    attempts=attempts,
                )
            self._sleeper(max(interval_sec, 1))

    @staticmethod
    def _run_json(command: list[str], timeout: int) -> dict[str, Any]:
        try:
            proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"success": False, "_error": f"GetNote command timed out after {timeout}s"}
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"success": False, "_error": "GetNote returned non-JSON output"}
        payload["_returncode"] = proc.returncode
        if proc.returncode and not _payload_error(payload):
            payload["_error"] = f"GetNote command failed with exit code {proc.returncode}"
        return payload


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def _note(payload: dict[str, Any]) -> dict[str, Any]:
    value = _data(payload).get("note")
    return value if isinstance(value, dict) else {}


def _note_content(payload: dict[str, Any]) -> str:
    page = _note(payload).get("web_page")
    if isinstance(page, dict) and isinstance(page.get("content"), str):
        return page["content"].strip()
    return ""


def _note_title(payload: dict[str, Any]) -> Optional[str]:
    title = _note(payload).get("title")
    return title.strip() if isinstance(title, str) and title.strip() else None


def _note_id(payload: dict[str, Any]) -> Optional[str]:
    data = _data(payload)
    for value in (data.get("note_id"), _note(payload).get("note_id"), _note(payload).get("id")):
        if value is not None:
            return str(value)
    return None


def _task_id(payload: dict[str, Any]) -> Optional[str]:
    value = _data(payload).get("task_id")
    return str(value) if value is not None else None


def _payload_error(payload: dict[str, Any]) -> Optional[str]:
    data = _data(payload)
    for value in (
        data.get("error_msg"),
        data.get("msg"),
        payload.get("message"),
        payload.get("error"),
        payload.get("_error"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _looks_like_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("auth", "unauthorized", "api key", "登录", "认证"))
