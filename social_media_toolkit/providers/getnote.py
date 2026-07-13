"""GetNote original-content provider.

Current GetNote CLI versions wait for URL processing in ``getnote save``. The
toolkit therefore performs one save, then at most one explicit note/task lookup
when an identifier is returned. It does not maintain a second polling engine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


GETNOTE_DOCS_URL = "https://www.npmjs.com/package/@getnote/cli"
GETNOTE_REPOSITORY_URL = "https://github.com/iswalle/getnote-cli"
GETNOTE_INSTALL_HINT = (
    "Install GetNote: npm install -g @getnote/cli; then run: getnote auth login. "
    f"Docs: {GETNOTE_DOCS_URL}"
)


@dataclass
class GetNoteResult:
    status: str
    text: Optional[str] = None
    title: Optional[str] = None
    note_id: Optional[str] = None
    task_id: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.text)


class GetNoteTextProvider:
    """Read original text through GetNote without storing credentials here."""

    def __init__(
        self,
        executable: str = "getnote",
        *,
        runner: Optional[Callable[[list[str], int], dict[str, Any]]] = None,
    ) -> None:
        self.executable = executable
        self._runner = runner or self._run_json

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def authenticated(self, *, timeout_sec: int = 15) -> bool:
        if not self.available():
            return False
        try:
            process = subprocess.run(
                [self.executable, "auth", "status"],
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        output = f"{process.stdout}\n{process.stderr}".lower()
        rejected = any(
            marker in output
            for marker in ("not authenticated", "unauthenticated", "not logged in", "未登录", "未认证")
        )
        return process.returncode == 0 and not rejected

    def extract(self, url: str, *, command_timeout_sec: int = 300) -> GetNoteResult:
        if not self.available():
            return GetNoteResult(status="unavailable", warnings=[GETNOTE_INSTALL_HINT])

        save_payload = self._runner(
            [self.executable, "save", url, "-o", "json"],
            command_timeout_sec,
        )
        result = _result_from_payload(save_payload)
        if result.success:
            return result

        note_id = result.note_id
        task_id = result.task_id
        task_payload: dict[str, Any] | None = None

        if not note_id and task_id:
            task_payload = self._runner(
                [self.executable, "task", task_id, "-o", "json"],
                command_timeout_sec,
            )
            task_result = _result_from_payload(task_payload)
            if task_result.success:
                return task_result
            note_id = task_result.note_id

        if note_id:
            note_payload = self._runner(
                [self.executable, "note", note_id, "-o", "json"],
                command_timeout_sec,
            )
            note_result = _result_from_payload(note_payload)
            note_result.task_id = task_id
            if note_result.success:
                stale_error = _payload_error(task_payload or save_payload)
                if stale_error:
                    note_result.warnings.append(
                        f"GetNote stale task message ignored because original content is ready: {stale_error}"
                    )
                return note_result

        warning = _payload_error(task_payload or save_payload) or "GetNote returned no original content"
        if _looks_like_auth_error(warning):
            warning = f"{warning}. Run: getnote auth login"
        return GetNoteResult(
            status="failed",
            note_id=note_id,
            task_id=task_id,
            warnings=[warning],
        )

    @staticmethod
    def _run_json(command: list[str], timeout: int) -> dict[str, Any]:
        try:
            process = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "_error": f"GetNote command timed out after {timeout}s"}
        try:
            payload = json.loads(process.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"success": False, "_error": "GetNote returned non-JSON output"}
        payload["_returncode"] = process.returncode
        if process.returncode and not _payload_error(payload):
            payload["_error"] = f"GetNote command failed with exit code {process.returncode}"
        return payload


def _result_from_payload(payload: dict[str, Any]) -> GetNoteResult:
    return GetNoteResult(
        status="success" if _note_original_content(payload) else "failed",
        text=_note_original_content(payload) or None,
        title=_note_title(payload),
        note_id=_note_id(payload),
        task_id=_task_id(payload),
    )


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def _note(payload: dict[str, Any]) -> dict[str, Any]:
    value = _data(payload).get("note")
    return value if isinstance(value, dict) else {}


def _note_original_content(payload: dict[str, Any]) -> str:
    note = _note(payload)
    # Current CLI calls the original webpage field ``web_content``. Earlier
    # server responses nested the same original text under web_page.content.
    # Supporting both response shapes does not change the routing policy.
    direct = note.get("web_content")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    page = note.get("web_page")
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
