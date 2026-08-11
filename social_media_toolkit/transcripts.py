"""Timestamped transcript normalization and durable artifact rendering.

The public toolkit keeps plain canonical text and timed transcripts as two
different contracts.  Timed transcripts are evidence artifacts: every segment
must retain a source-relative interval so callers can return to the original
video for review or clipping.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


TIMED_TRANSCRIPT_SCHEMA_VERSION = "1.0"
SUPPORTED_TRANSCRIPT_OUTPUTS = ("md", "srt", "json")


def normalize_transcript_outputs(outputs: Sequence[str] | str) -> list[str]:
    if isinstance(outputs, str):
        requested = [item.strip().lower() for item in outputs.split(",") if item.strip()]
    else:
        requested = [str(item).strip().lower() for item in outputs if str(item).strip()]
    if not requested:
        raise ValueError("Timed transcript outputs cannot be empty")
    normalized: list[str] = []
    for item in requested:
        if item not in SUPPORTED_TRANSCRIPT_OUTPUTS:
            supported = ",".join(SUPPORTED_TRANSCRIPT_OUTPUTS)
            raise ValueError(f"Unsupported timed transcript output: {item}. Expected: {supported}")
        if item not in normalized:
            normalized.append(item)
    return normalized


def normalize_segments(segments: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ordered, valid, adjacent-exact-deduplicated transcript segments."""
    normalized: list[dict[str, Any]] = []
    for raw in segments:
        if not isinstance(raw, dict):
            continue
        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()
        if not text:
            continue
        start_ms = _as_nonnegative_int(raw.get("start_ms"))
        end_ms = _as_nonnegative_int(raw.get("end_ms"))
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        item = {"start_ms": start_ms, "end_ms": end_ms, "text": text}
        speaker = str(raw.get("speaker") or "").strip()
        if speaker:
            item["speaker"] = speaker
        normalized.append(item)

    normalized.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["text"]))
    deduped: list[dict[str, Any]] = []
    for item in normalized:
        if (
            deduped
            and deduped[-1]["text"] == item["text"]
            and deduped[-1].get("speaker") == item.get("speaker")
            and item["start_ms"] <= deduped[-1]["end_ms"] + 1000
        ):
            deduped[-1]["end_ms"] = max(deduped[-1]["end_ms"], item["end_ms"])
            continue
        deduped.append(item)
    return deduped


def transcript_text(segments: Sequence[dict[str, Any]]) -> str:
    return "\n".join(str(item.get("text") or "").strip() for item in segments if item.get("text"))


def build_timed_transcript_document(
    *,
    platform: str,
    post_id: str,
    title: str,
    source_url: str,
    original_url: str,
    duration_ms: int,
    provider: str,
    route: str,
    timing_precision: str,
    segments: Sequence[dict[str, Any]],
    words: Sequence[dict[str, Any]] = (),
    speaker_diarization: dict[str, Any] | None = None,
    asr_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_segments(segments)
    if not normalized:
        raise ValueError("Timed transcript contains no usable timestamped segments")
    duration_ms = max(
        _as_nonnegative_int(duration_ms),
        max(item["end_ms"] for item in normalized),
    )
    document = {
        "schema_version": TIMED_TRANSCRIPT_SCHEMA_VERSION,
        "source": {
            "platform": platform,
            "post_id": post_id,
            "url": source_url,
            "original_url": original_url,
            "title": title,
            "duration_ms": duration_ms,
        },
        "provider": provider,
        "route": route,
        "timing_precision": timing_precision,
        "segment_count": len(normalized),
        "word_count": len(words),
        "text": transcript_text(normalized),
        "segments": normalized,
        "words": list(words),
    }
    if speaker_diarization is not None:
        document["speaker_diarization"] = dict(speaker_diarization)
    if asr_config is not None:
        document["asr_config"] = dict(asr_config)
    return document


def write_timed_transcript_artifacts(
    document: dict[str, Any],
    *,
    output_dir: str,
    outputs: Sequence[str] | str = SUPPORTED_TRANSCRIPT_OUTPUTS,
) -> list[dict[str, Any]]:
    requested = normalize_transcript_outputs(outputs)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source = document.get("source") or {}
    platform = _safe_component(str(source.get("platform") or "source"))
    post_id = _safe_component(str(source.get("post_id") or "unknown"))
    stem = f"{platform}-{post_id}-transcript"
    artifacts: list[dict[str, Any]] = []

    for output in requested:
        suffix = ".timeline.json" if output == "json" else f".{output}"
        path = destination / f"{stem}{suffix}"
        if output == "md":
            payload = render_timed_markdown(document)
        elif output == "srt":
            payload = render_srt(document.get("segments") or [])
        else:
            payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        _atomic_write_text(path, payload)
        encoded = payload.encode("utf-8")
        artifacts.append(
            {
                "kind": output,
                "path": str(path),
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    return artifacts


def render_timed_markdown(document: dict[str, Any]) -> str:
    source = document.get("source") or {}
    title = str(source.get("title") or source.get("post_id") or "Timed transcript")
    lines = [
        f"# {title}｜带时间轴逐字稿\n\n",
        f"- 原始链接：{source.get('url') or ''}\n",
        f"- YouTube video_id：`{source.get('post_id') or ''}`\n",
        f"- 文字来源：`{document.get('provider') or ''}`\n",
        f"- 路由：`{document.get('route') or ''}`\n",
        f"- 时间精度：`{document.get('timing_precision') or ''}`\n",
        f"- 视频时长：{short_time(_as_nonnegative_int(source.get('duration_ms')))}\n",
        f"- 生成时间：{datetime.now(tz=timezone.utc).isoformat().replace('+00:00', 'Z')}\n",
        "- 说明：时间码对应上述原始视频；文字未经人工校对。\n",
        "\n## 分段逐字稿\n\n",
    ]
    for segment in document.get("segments") or []:
        speaker = str(segment.get("speaker") or "").strip()
        prefix = f"{speaker}｜" if speaker else ""
        lines.append(
            f"[{short_time(segment['start_ms'])} - {short_time(segment['end_ms'])}] "
            f"{prefix}{segment['text']}\n"
        )
    return "".join(lines)


def render_srt(segments: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, segment in enumerate(normalize_segments(segments), start=1):
        lines.extend(
            [
                str(index),
                f"{srt_time(segment['start_ms'])} --> {srt_time(segment['end_ms'])}",
                segment["text"],
                "",
            ]
        )
    return "\n".join(lines)


def short_time(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds)) // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def srt_time(milliseconds: int) -> str:
    milliseconds = max(0, int(milliseconds))
    total_seconds, remainder = divmod(milliseconds, 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02},{remainder:03}"


def _atomic_write_text(path: Path, payload: str) -> None:
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        # Write exact UTF-8 bytes so checksums and artifacts stay identical on
        # Windows, macOS, and Linux instead of inheriting platform newlines.
        staged.write_bytes(payload.encode("utf-8"))
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def _safe_component(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-._")
    return value[:96] or "unknown"


def _as_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
