"""The only ASR implementation used by Social Media Toolkit.

The protocol mirrors the public ``cloud-transcript`` Skill: temporary audio is
prepared with ffmpeg, sent to Volcengine's big-model flash transcription API,
and deleted with the temporary directory. There is deliberately no local ASR
or alternate cloud-provider fallback.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import requests

from social_media_toolkit.platforms.core import HEADERS, SocialPost


VOLCENGINE_ASR_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
VOLCENGINE_ASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
VOLCENGINE_ASR_SECRET_NAME = "VOLCENGINE_ASR_API_KEY"
VOLCENGINE_ASR_DOCS_URL = "https://www.volcengine.com/docs/6561/1631584?lang=zh"
VOLCENGINE_ASR_PRODUCT_URL = "https://www.volcengine.com/product/asr"
MAX_TEMP_MEDIA_BYTES = 2 * 1024 * 1024 * 1024


class VolcengineASRError(RuntimeError):
    """A terminal cloud-ASR error. Callers must not fall back to local ASR."""


class VolcengineASR:
    provider_name = "volcengine_bigmodel"
    secret_name = VOLCENGINE_ASR_SECRET_NAME

    def configured(self) -> bool:
        return bool(_load_api_key())

    def transcribe(self, post: SocialPost) -> str:
        api_key = _load_api_key()
        if not api_key:
            raise VolcengineASRError(
                f"Missing {VOLCENGINE_ASR_SECRET_NAME}. Configure Volcengine cloud ASR first: "
                f"{VOLCENGINE_ASR_DOCS_URL}"
            )

        media_url = (post.media or {}).get("audio_url") or post.video_url
        if not media_url:
            raise VolcengineASRError("The platform returned no media URL that can be sent to cloud ASR")

        try:
            with tempfile.TemporaryDirectory(prefix="socialkit-asr-") as tmpdir:
                temp_dir = Path(tmpdir)
                source_path = _download_media(
                    media_url,
                    temp_dir,
                    referer=post.page_url or post.resolved_url,
                )
                audio_path = _prepare_audio(source_path, temp_dir)
                response = _call_volcengine(audio_path, api_key)
        except VolcengineASRError:
            raise
        except Exception as exc:
            raise VolcengineASRError(f"Volcengine cloud ASR failed: {exc}") from exc

        transcript = _transcript_text(response)
        if not transcript:
            raise VolcengineASRError("Volcengine cloud ASR returned no transcript text")
        return transcript


def _load_api_key() -> str | None:
    value = os.environ.get(VOLCENGINE_ASR_SECRET_NAME)
    if value and value.strip():
        return value.strip()

    # Agent Switch is optional for public users, but is the secure local source
    # of truth when installed. The inherited FD prevents the value from being
    # routed through stdout, stderr, command arguments, or shell history.
    executable = shutil.which("agent-switch")
    if not executable:
        return None
    read_fd, write_fd = os.pipe()
    try:
        result = subprocess.run(
            [executable, "secret", "get", "--fd", str(write_fd), VOLCENGINE_ASR_SECRET_NAME],
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        os.close(write_fd)
        write_fd = -1
        secret = os.read(read_fd, 1024 * 1024).decode("utf-8").strip()
        return secret if result.returncode == 0 and secret else None
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def _download_media(url: str, temp_dir: Path, *, referer: str | None) -> Path:
    suffix = Path(urlparse(url).path).suffix.lower()
    if not suffix or len(suffix) > 8:
        suffix = ".media"
    destination = temp_dir / f"source{suffix}"
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer

    try:
        response = requests.get(url, headers=headers, timeout=120, stream=True, allow_redirects=True)
        response.raise_for_status()
        expected = _as_int(response.headers.get("Content-Length"))
        if expected > MAX_TEMP_MEDIA_BYTES:
            raise VolcengineASRError("Remote media exceeds the 2 GB temporary-ASR limit")
        written = 0
        with destination.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_TEMP_MEDIA_BYTES:
                    raise VolcengineASRError("Remote media exceeds the 2 GB temporary-ASR limit")
                file.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if "response" in locals():
            response.close()

    if not destination.exists() or destination.stat().st_size == 0:
        raise VolcengineASRError("Downloaded media is empty")
    return destination


def _prepare_audio(source_path: Path, temp_dir: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise VolcengineASRError("ffmpeg is required to prepare temporary audio for Volcengine ASR")
    audio_path = temp_dir / "speech.mp3"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(audio_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        detail = (completed.stderr or "unknown ffmpeg error")[-1000:]
        raise VolcengineASRError(f"ffmpeg audio preparation failed: {detail}")
    return audio_path


def _call_volcengine(audio_path: Path, api_key: str) -> dict:
    payload = {
        "user": {"uid": "social-media-toolkit"},
        "audio": {
            "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
            "format": "mp3",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
        },
    }
    request = Request(
        VOLCENGINE_ASR_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": VOLCENGINE_ASR_RESOURCE_ID,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=600) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise VolcengineASRError(f"Volcengine cloud ASR HTTP {exc.code}: {body[:1000]}") from exc
    except URLError as exc:
        raise VolcengineASRError(f"Volcengine cloud ASR network error: {exc.reason}") from exc

    if status < 200 or status >= 300:
        raise VolcengineASRError(f"Volcengine cloud ASR HTTP {status}: {body[:1000]}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise VolcengineASRError("Volcengine cloud ASR returned non-JSON data") from exc
    if not isinstance(result, dict) or not isinstance(result.get("result"), dict):
        raise VolcengineASRError("Volcengine cloud ASR response is missing result")
    return result


def _transcript_text(payload: dict) -> str:
    result = payload.get("result") or {}
    text = str(result.get("text") or "").strip()
    if text:
        return text
    return "\n".join(
        str(item.get("text") or "").strip()
        for item in result.get("utterances") or []
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    )


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
