"""The only ASR implementation used by Social Media Toolkit.

Temporary audio is prepared with ffmpeg and routed by media duration:

* up to two hours uses Volcengine's synchronous big-model flash API;
* over two and up to five hours uses the asynchronous standard API through a
  temporary private TOS object and presigned download URL;
* over five hours is rejected before media download or a billable ASR call.

There is deliberately no local ASR, chunking, or alternate cloud-provider
fallback. Local media and the temporary TOS object are deleted before success
is returned.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import requests

from social_media_toolkit.platforms.core import HEADERS, SocialPost
from social_media_toolkit.transcripts import normalize_segments, transcript_text


VOLCENGINE_ASR_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
VOLCENGINE_ASR_STANDARD_SUBMIT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
VOLCENGINE_ASR_STANDARD_QUERY_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
VOLCENGINE_ASR_RESOURCE_ID = "volc.bigasr.auc_turbo"
VOLCENGINE_ASR_STANDARD_RESOURCE_ID = "volc.seedasr.auc"
VOLCENGINE_ASR_SECRET_NAME = "VOLCENGINE_ASR_API_KEY"
VOLCENGINE_ASR_DOCS_URL = "https://www.volcengine.com/docs/6561/1631584?lang=zh"
VOLCENGINE_ASR_PRODUCT_URL = "https://www.volcengine.com/product/asr"
TOS_ACCESS_KEY_SECRET_NAME = "TOS_ACCESS_KEY"
TOS_SECRET_KEY_SECRET_NAME = "TOS_SECRET_KEY"
TOS_CONFIG_PATH_ENV = "SOCIAL_MEDIA_TOOLKIT_CONFIG"
DEFAULT_TOS_CONFIG_PATH = Path.home() / ".config" / "social-media-toolkit" / "config.json"
MAX_TEMP_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
MEDIA_DOWNLOAD_MAX_ATTEMPTS = 3
MAX_FLASH_DURATION_SECONDS = 2 * 60 * 60
MAX_STANDARD_DURATION_SECONDS = 5 * 60 * 60
STANDARD_PROCESSING_CODES = {"20000001", "20000002"}
STANDARD_SUCCESS_CODE = "20000000"
STANDARD_POLL_INTERVAL_SECONDS = 10
STANDARD_MAX_WAIT_SECONDS = 3 * 60 * 60
STANDARD_REQUEST_TIMEOUT_SECONDS = 600
MIN_STANDARD_PRESIGN_SECONDS = 4 * 60 * 60
_VOLCENGINE_API_ENDPOINTS = {
    VOLCENGINE_ASR_ENDPOINT,
    VOLCENGINE_ASR_STANDARD_SUBMIT_ENDPOINT,
    VOLCENGINE_ASR_STANDARD_QUERY_ENDPOINT,
}


class VolcengineASRError(RuntimeError):
    """A terminal cloud-ASR error. Callers must not fall back to local ASR."""


@dataclass
class _TemporaryTOSObject:
    client: Any
    bucket: str
    key: str
    signed_url: str

    def delete(self) -> None:
        try:
            self.client.delete_object(bucket=self.bucket, key=self.key)
        except Exception as exc:
            raise VolcengineASRError("Failed to delete the temporary TOS audio object") from exc


class VolcengineASR:
    provider_name = "volcengine_bigmodel"
    secret_name = VOLCENGINE_ASR_SECRET_NAME

    def configured(self) -> bool:
        return bool(_load_api_key())

    def standard_configured(self) -> bool:
        try:
            _standard_storage_config()
            import tos  # noqa: F401
        except VolcengineASRError:
            return False
        except ImportError:
            return False
        return bool(_load_api_key())

    def transcribe(self, post: SocialPost) -> str:
        response, _duration_ms, _route = self._transcribe_payload(post)
        transcript = _transcript_text(response)
        if not transcript:
            raise VolcengineASRError("Volcengine cloud ASR returned no transcript text")
        return transcript

    def transcribe_timed(
        self,
        post: SocialPost,
        *,
        speaker_info: bool = False,
        context: dict | None = None,
    ) -> dict:
        response, duration_ms, route = self._transcribe_payload(
            post,
            timed=True,
            speaker_info=speaker_info,
            context=context,
        )
        timeline = _timed_transcript(
            response,
            duration_ms=duration_ms,
            require_speaker_info=speaker_info,
        )
        timeline["asr_config"] = {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": True,
            "enable_speaker_info": speaker_info,
            "ssd_version": "200" if speaker_info else None,
            "enable_channel_split": False,
            "language": None,
            "context_provided": bool(context),
            "recognition_mode": "standard" if "standard" in route else "flash",
        }
        timeline["route"] = route
        timeline["temp_media_deleted"] = True
        timeline["temporary_cloud_object_deleted"] = True
        return timeline

    def _transcribe_payload(
        self,
        post: SocialPost,
        *,
        timed: bool = False,
        speaker_info: bool = False,
        context: dict | None = None,
    ) -> tuple[dict, int, str]:
        duration_seconds = max(0, int(post.duration_sec or 0))
        if duration_seconds > MAX_STANDARD_DURATION_SECONDS:
            raise VolcengineASRError("Videos longer than 5 hours are not supported yet")

        api_key = _load_api_key()
        if not api_key:
            raise VolcengineASRError(
                f"Missing {VOLCENGINE_ASR_SECRET_NAME}. Configure Volcengine cloud ASR first: "
                f"{VOLCENGINE_ASR_DOCS_URL}"
            )

        use_standard = duration_seconds > MAX_FLASH_DURATION_SECONDS

        if post.platform == "youtube":
            media_url = post.page_url or post.resolved_url or post.source_url
        else:
            media_url = (post.media or {}).get("audio_url") or post.video_url
        if not media_url:
            raise VolcengineASRError("The platform returned no media URL that can be sent to cloud ASR")

        try:
            with tempfile.TemporaryDirectory(prefix="socialkit-asr-") as tmpdir:
                temp_dir = Path(tmpdir)
                if post.platform == "youtube":
                    source_path = _download_youtube_media(media_url, temp_dir)
                else:
                    source_path = _download_media(
                        media_url,
                        temp_dir,
                        referer=post.page_url or post.resolved_url,
                    )
                audio_path = _prepare_audio(source_path, temp_dir)
                if use_standard:
                    response = _call_volcengine_standard(
                        audio_path,
                        api_key,
                        timed=timed,
                        speaker_info=speaker_info,
                        context=context,
                    )
                else:
                    response = _call_volcengine(
                        audio_path,
                        api_key,
                        timed=timed,
                        speaker_info=speaker_info,
                        context=context,
                    )
        except VolcengineASRError:
            raise
        except Exception as exc:
            raise VolcengineASRError(f"Volcengine cloud ASR failed: {exc}") from exc

        duration_ms = max(0, int(post.duration_sec or 0) * 1000)
        route_kind = "standard" if use_standard else "flash"
        route_suffix = "_timed" if timed else ""
        return response, duration_ms, f"volcengine.bigmodel_{route_kind}{route_suffix}"


def _load_secret(name: str) -> str | None:
    value = os.environ.get(name)
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
            [executable, "secret", "get", "--fd", str(write_fd), name],
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


def _load_api_key() -> str | None:
    return _load_secret(VOLCENGINE_ASR_SECRET_NAME)


def _download_media(url: str, temp_dir: Path, *, referer: str | None) -> Path:
    suffix = Path(urlparse(url).path).suffix.lower()
    if not suffix or len(suffix) > 8:
        suffix = ".media"
    destination = temp_dir / f"source{suffix}"
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer

    last_error: Exception | None = None
    for attempt in range(1, MEDIA_DOWNLOAD_MAX_ATTEMPTS + 1):
        existing = destination.stat().st_size if destination.exists() else 0
        request_headers = dict(headers)
        if existing:
            request_headers["Range"] = f"bytes={existing}-"
        response = None
        try:
            response = requests.get(
                url,
                headers=request_headers,
                timeout=120,
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()
            resume_accepted = bool(existing and response.status_code == 206)
            if not resume_accepted:
                existing = 0
            expected_chunk = _as_int(response.headers.get("Content-Length"))
            expected_total = existing + expected_chunk if expected_chunk else 0
            if expected_total > MAX_TEMP_MEDIA_BYTES:
                raise VolcengineASRError("Remote media exceeds the 2 GB temporary-ASR limit")

            written = existing
            with destination.open("ab" if resume_accepted else "wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_TEMP_MEDIA_BYTES:
                        raise VolcengineASRError("Remote media exceeds the 2 GB temporary-ASR limit")
                    file.write(chunk)
            if expected_total and written < expected_total:
                raise VolcengineASRError(
                    f"Temporary media download ended early ({written}/{expected_total} bytes)"
                )
            if written <= 0:
                raise VolcengineASRError("Downloaded media is empty")
            return destination
        except Exception as exc:
            last_error = exc
            if isinstance(exc, VolcengineASRError) and "2 GB" in str(exc):
                destination.unlink(missing_ok=True)
                raise
            if attempt >= MEDIA_DOWNLOAD_MAX_ATTEMPTS:
                destination.unlink(missing_ok=True)
                raise VolcengineASRError(
                    f"Temporary media download failed after {attempt} attempts: {exc}"
                ) from exc
        finally:
            if response is not None:
                response.close()

    destination.unlink(missing_ok=True)
    raise VolcengineASRError(f"Temporary media download failed: {last_error}")


def _download_youtube_media(url: str, temp_dir: Path) -> Path:
    """Download temporary YouTube audio through yt-dlp's retry/resume path."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise VolcengineASRError("yt-dlp is required to prepare temporary YouTube audio") from exc

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(temp_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "continuedl": True,
        "overwrites": True,
        "max_filesize": MAX_TEMP_MEDIA_BYTES,
    }
    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            prepared = Path(downloader.prepare_filename(info)) if isinstance(info, dict) else None
    except Exception as exc:
        raise VolcengineASRError(f"Temporary YouTube audio download failed: {exc}") from exc

    candidates = [path for path in temp_dir.glob("source.*") if path.is_file()]
    if prepared and prepared.is_file() and prepared not in candidates:
        candidates.append(prepared)
    if not candidates:
        raise VolcengineASRError("yt-dlp returned no temporary YouTube audio file")
    source = max(candidates, key=lambda path: path.stat().st_size)
    size = source.stat().st_size
    if size <= 0:
        raise VolcengineASRError("Downloaded YouTube audio is empty")
    if size > MAX_TEMP_MEDIA_BYTES:
        source.unlink(missing_ok=True)
        raise VolcengineASRError("Remote media exceeds the 2 GB temporary-ASR limit")
    return source


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


def _call_volcengine(
    audio_path: Path,
    api_key: str,
    *,
    timed: bool = False,
    speaker_info: bool = False,
    context: dict | None = None,
) -> dict:
    request_options = _request_options(
        timed=timed,
        speaker_info=speaker_info,
        context=context,
    )

    payload = {
        "user": {"uid": "social-media-toolkit"},
        "audio": {
            "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
            "format": "mp3",
        },
        "request": request_options,
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
        # The request URL is a module-owned HTTPS constant, never caller input.
        with urlopen(request, timeout=600) as response:  # nosec B310
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


def _request_options(
    *,
    timed: bool,
    speaker_info: bool,
    context: dict | None,
) -> dict[str, Any]:
    request_options: dict = {
        "model_name": "bigmodel",
        "enable_itn": True,
        "enable_punc": True,
        # Timed transcripts are evidence artifacts. Preserve fillers,
        # repetitions, and self-corrections instead of smoothing them away.
        "enable_ddc": not timed,
    }
    if timed:
        request_options["show_utterances"] = True
    if speaker_info:
        request_options.update(
            {
                "enable_speaker_info": True,
                "ssd_version": "200",
                # Prepared YouTube audio is intentionally mono. A stereo
                # container does not mean that speakers occupy isolated sides.
                "enable_channel_split": False,
            }
        )
    if context:
        request_options["corpus"] = {
            "context": _serialize_asr_context(context),
        }
    return request_options


def _call_volcengine_standard(
    audio_path: Path,
    api_key: str,
    *,
    timed: bool = False,
    speaker_info: bool = False,
    context: dict | None = None,
) -> dict:
    """Upload one temporary object, submit standard ASR, poll, then delete it."""
    temporary_object = _upload_standard_audio(audio_path)
    try:
        return _submit_and_query_standard(
            temporary_object.signed_url,
            api_key,
            timed=timed,
            speaker_info=speaker_info,
            context=context,
        )
    finally:
        temporary_object.delete()


def _standard_storage_config() -> dict[str, Any]:
    config_path = Path(
        os.environ.get(TOS_CONFIG_PATH_ENV) or DEFAULT_TOS_CONFIG_PATH
    ).expanduser()
    file_config: dict[str, Any] = {}
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VolcengineASRError(f"Invalid Social Media Toolkit config: {config_path}") from exc
        if not isinstance(payload, dict):
            raise VolcengineASRError(f"Social Media Toolkit config must be a JSON object: {config_path}")
        candidate = payload.get("volcengine_tos") or {}
        if not isinstance(candidate, dict):
            raise VolcengineASRError("volcengine_tos config must be a JSON object")
        file_config = candidate

    def setting(env_name: str, config_name: str, default: str = "") -> str:
        return str(os.environ.get(env_name) or file_config.get(config_name) or default).strip()

    access_key = _load_secret(TOS_ACCESS_KEY_SECRET_NAME)
    secret_key = _load_secret(TOS_SECRET_KEY_SECRET_NAME)
    bucket = setting("TOS_BUCKET", "bucket")
    region = setting("TOS_REGION", "region")
    endpoint = setting("TOS_ENDPOINT", "endpoint")
    missing = [
        name
        for name, value in (
            (TOS_ACCESS_KEY_SECRET_NAME, access_key),
            (TOS_SECRET_KEY_SECRET_NAME, secret_key),
            ("TOS_BUCKET", bucket),
            ("TOS_REGION", region),
            ("TOS_ENDPOINT", endpoint),
        )
        if not value
    ]
    if missing:
        raise VolcengineASRError(
            "Volcengine standard ASR requires temporary TOS storage; missing configuration names: "
            + ", ".join(missing)
        )

    prefix = setting("TOS_OBJECT_PREFIX", "object_prefix", "social-media-toolkit/long-asr").strip("/")
    expires_raw = setting("TOS_PRESIGN_EXPIRES", "presign_expires_seconds", str(MIN_STANDARD_PRESIGN_SECONDS))
    try:
        expires = max(MIN_STANDARD_PRESIGN_SECONDS, int(expires_raw))
    except ValueError as exc:
        raise VolcengineASRError("TOS_PRESIGN_EXPIRES must be an integer") from exc
    return {
        "access_key": access_key,
        "secret_key": secret_key,
        "bucket": bucket,
        "region": region,
        "endpoint": endpoint,
        "object_prefix": prefix,
        "presign_expires_seconds": expires,
    }


def _upload_standard_audio(audio_path: Path) -> _TemporaryTOSObject:
    config = _standard_storage_config()
    try:
        import tos
        from tos import enum
    except ImportError as exc:
        raise VolcengineASRError(
            "The 'tos' package is required for Volcengine standard ASR temporary storage"
        ) from exc

    client = tos.TosClientV2(
        ak=config["access_key"],
        sk=config["secret_key"],
        endpoint=config["endpoint"],
        region=config["region"],
        request_timeout=STANDARD_REQUEST_TIMEOUT_SECONDS,
        socket_timeout=STANDARD_REQUEST_TIMEOUT_SECONDS,
    )
    object_name = f"{uuid.uuid4().hex}.mp3"
    prefix = config["object_prefix"]
    key = f"{prefix}/{object_name}" if prefix else object_name
    content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/mpeg"
    uploaded = False
    try:
        client.put_object_from_file(
            bucket=config["bucket"],
            key=key,
            file_path=str(audio_path),
            content_type=content_type,
        )
        uploaded = True
        signed = client.pre_signed_url(
            enum.HttpMethodType.Http_Method_Get,
            config["bucket"],
            key,
            expires=config["presign_expires_seconds"],
        )
        signed_url = str(getattr(signed, "signed_url", "") or "").strip()
        if not signed_url:
            raise VolcengineASRError("TOS did not return a presigned audio URL")
        return _TemporaryTOSObject(client, config["bucket"], key, signed_url)
    except Exception as exc:
        if uploaded:
            try:
                client.delete_object(bucket=config["bucket"], key=key)
            except Exception:
                pass
        if isinstance(exc, VolcengineASRError):
            raise
        raise VolcengineASRError("Failed to upload temporary audio for standard ASR") from exc


def _submit_and_query_standard(
    audio_url: str,
    api_key: str,
    *,
    timed: bool,
    speaker_info: bool,
    context: dict | None,
    poll_interval: int = STANDARD_POLL_INTERVAL_SECONDS,
    max_wait_seconds: int = STANDARD_MAX_WAIT_SECONDS,
) -> dict:
    task_id = str(uuid.uuid4())
    resource_id = str(
        os.environ.get("VOLCENGINE_ASR_STANDARD_RESOURCE_ID")
        or VOLCENGINE_ASR_STANDARD_RESOURCE_ID
    ).strip()
    request_options = _request_options(
        timed=timed,
        speaker_info=speaker_info,
        context=context,
    )
    submit_headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
        "Content-Type": "application/json",
    }
    submit_payload = {
        "user": {"uid": "social-media-toolkit"},
        "audio": {"url": audio_url, "format": "mp3"},
        "request": request_options,
    }
    submit_headers_result, submit_body = _post_standard_json(
        VOLCENGINE_ASR_STANDARD_SUBMIT_ENDPOINT,
        submit_headers,
        submit_payload,
    )
    code, message = _standard_status(submit_headers_result)
    if code != STANDARD_SUCCESS_CODE:
        raise VolcengineASRError(
            f"Volcengine standard ASR submit failed: {code or 'missing_status'} {message}".rstrip()
        )

    query_headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": task_id,
        "Content-Type": "application/json",
    }
    log_id = _header_value(submit_headers_result, "X-Tt-Logid")
    if log_id:
        query_headers["X-Tt-Logid"] = log_id
    deadline = time.monotonic() + max_wait_seconds
    while True:
        response_headers, response_body = _post_standard_json(
            VOLCENGINE_ASR_STANDARD_QUERY_ENDPOINT,
            query_headers,
            {},
        )
        code, message = _standard_status(response_headers)
        if code == STANDARD_SUCCESS_CODE:
            if not isinstance(response_body.get("result"), dict):
                raise VolcengineASRError("Volcengine standard ASR response is missing result")
            return response_body
        if code not in STANDARD_PROCESSING_CODES:
            raise VolcengineASRError(
                f"Volcengine standard ASR query failed: {code or 'missing_status'} {message}".rstrip()
            )
        if time.monotonic() >= deadline:
            raise VolcengineASRError("Timed out waiting for Volcengine standard ASR")
        time.sleep(max(1, poll_interval))


def _post_standard_json(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> tuple[dict[str, str], dict]:
    if endpoint not in _VOLCENGINE_API_ENDPOINTS:
        raise VolcengineASRError("Refusing an unrecognized Volcengine API endpoint")
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        # The allowlist above limits urlopen to official module-owned HTTPS endpoints.
        with urlopen(request, timeout=STANDARD_REQUEST_TIMEOUT_SECONDS) as response:  # nosec B310
            body = response.read().decode("utf-8", errors="replace")
            response_headers = {key: value for key, value in response.headers.items()}
            status = response.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise VolcengineASRError(
            f"Volcengine standard ASR HTTP {exc.code}: {body[:1000]}"
        ) from exc
    except URLError as exc:
        raise VolcengineASRError(
            f"Volcengine standard ASR network error: {exc.reason}"
        ) from exc
    if status < 200 or status >= 300:
        raise VolcengineASRError(f"Volcengine standard ASR HTTP {status}: {body[:1000]}")
    if not body.strip():
        return response_headers, {}
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise VolcengineASRError("Volcengine standard ASR returned non-JSON data") from exc
    if not isinstance(decoded, dict):
        raise VolcengineASRError("Volcengine standard ASR returned invalid JSON")
    return response_headers, decoded


def _header_value(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return str(value or "").strip()
    return ""


def _standard_status(headers: dict[str, str]) -> tuple[str, str]:
    return (
        _header_value(headers, "X-Api-Status-Code"),
        _header_value(headers, "X-Api-Message"),
    )


def _serialize_asr_context(context: dict) -> str:
    if not isinstance(context, dict) or not context:
        raise VolcengineASRError("Volcengine ASR context must be a non-empty JSON object")
    try:
        encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise VolcengineASRError("Volcengine ASR context is not JSON serializable") from exc
    # The official model limit is token-based. This byte guard prevents an
    # accidentally unbounded description while leaving room for Unicode text.
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise VolcengineASRError("Volcengine ASR context exceeds the toolkit safety limit")
    return encoded


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


def _timed_transcript(
    payload: dict,
    *,
    duration_ms: int = 0,
    require_speaker_info: bool = False,
) -> dict:
    result = payload.get("result") or {}
    raw_utterances = result.get("utterances") or []
    segments: list[dict] = []
    for utterance in raw_utterances:
        if not isinstance(utterance, dict):
            continue
        text = str(utterance.get("text") or "").strip()
        if not text:
            continue
        start_ms = _first_int(utterance, ("start_time", "start", "start_ms", "begin_time"), 0)
        end_ms = _first_int(utterance, ("end_time", "end", "end_ms", "finish_time"), start_ms)
        segment = {"start_ms": start_ms, "end_ms": end_ms, "text": text}
        additions = utterance.get("additions") or {}
        raw_speaker = additions.get("speaker") if isinstance(additions, dict) else None
        speaker = _normalize_speaker_label(raw_speaker)
        if speaker:
            segment["speaker"] = speaker
        segments.append(segment)

    warnings: list[str] = []
    precision = "asr_utterance"
    if not segments:
        text = _transcript_text(payload)
        if not text:
            raise VolcengineASRError("Volcengine cloud ASR returned no transcript text")
        response_duration = _first_int(payload.get("audio_info") or {}, ("duration", "duration_ms"), 0)
        end_ms = max(duration_ms, response_duration)
        if end_ms <= 0:
            raise VolcengineASRError("Volcengine cloud ASR returned text without a usable media duration")
        segments = [{"start_ms": 0, "end_ms": end_ms, "text": text}]
        precision = "whole_media"
        warnings.append("Volcengine returned no utterance timing; only a whole-media interval is available")

    segments = normalize_segments(segments)
    if require_speaker_info:
        missing = sum(not segment.get("speaker") for segment in segments)
        if missing:
            raise VolcengineASRError(
                f"Volcengine speaker diarization omitted speaker IDs for {missing} utterance(s)"
            )
    words = _extract_asr_words(result, raw_utterances)
    if words:
        precision = "asr_word"
    duration_ms = max(
        duration_ms,
        _first_int(payload.get("audio_info") or {}, ("duration", "duration_ms"), 0),
        max(segment["end_ms"] for segment in segments),
    )
    speaker_labels = sorted(
        {str(segment["speaker"]) for segment in segments if segment.get("speaker")}
    )
    return {
        "text": transcript_text(segments),
        "duration_ms": duration_ms,
        "timing_precision": precision,
        "segments": segments,
        "words": words,
        "speaker_diarization": {
            "enabled": require_speaker_info,
            "speaker_count": len(speaker_labels),
            "speaker_labels": speaker_labels,
        },
        "warnings": warnings,
    }


def _normalize_speaker_label(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return f"SPEAKER_{int(text):02d}"
    normalized = "".join(character if character.isalnum() else "_" for character in text.upper())
    normalized = normalized.strip("_")
    if not normalized:
        return None
    return normalized if normalized.startswith("SPEAKER_") else f"SPEAKER_{normalized}"


def _extract_asr_words(result: dict, utterances: list) -> list[dict]:
    words: list[dict] = []

    def append(raw_words, utterance_index: int | None = None) -> None:
        if isinstance(raw_words, dict):
            raw_words = raw_words.get("words") or raw_words.get("items") or []
        if not isinstance(raw_words, list):
            return
        for raw in raw_words:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("word") or raw.get("token") or "").strip()
            if not text:
                continue
            start_ms = _first_int(raw, ("start_time", "start", "start_ms", "begin_time"), -1)
            end_ms = _first_int(raw, ("end_time", "end", "end_ms", "finish_time"), -1)
            if start_ms < 0 or end_ms <= start_ms:
                continue
            item = {"text": text, "start_ms": start_ms, "end_ms": end_ms}
            if utterance_index is not None:
                item["utterance_index"] = utterance_index
            words.append(item)

    append(result.get("words") or result.get("word_info") or result.get("word_infos"))
    for index, utterance in enumerate(utterances):
        if not isinstance(utterance, dict):
            continue
        append(
            utterance.get("words") or utterance.get("word_info") or utterance.get("word_infos"),
            index,
        )
    words.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["text"]))
    return words


def _first_int(mapping: dict, names: tuple[str, ...], default: int = 0) -> int:
    for name in names:
        value = mapping.get(name)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
