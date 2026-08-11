from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .service import SocialMediaToolkit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="socialkit",
        description="Inspect, extract, and explicitly download public social-media content.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Return normalized metadata without downloading media")
    inspect_parser.add_argument("url")

    text_parser = subparsers.add_parser(
        "text",
        help="Get canonical text, or write a YouTube transcript with source timecodes",
    )
    text_parser.add_argument("url")
    text_parser.add_argument(
        "--timed",
        action="store_true",
        help="Preserve YouTube subtitle/ASR timing and write durable transcript artifacts",
    )
    text_parser.add_argument(
        "--output",
        dest="text_output_dir",
        help="Required with --timed; destination for Markdown, SRT, and timeline JSON",
    )
    text_parser.add_argument(
        "--outputs",
        default="md,srt,json",
        help="Comma-separated timed artifacts: md,srt,json (default: all three)",
    )
    text_parser.add_argument(
        "--force-asr",
        action="store_true",
        help="With --timed, bypass native YouTube subtitles and always run Volcengine ASR",
    )
    text_parser.add_argument(
        "--speaker-info",
        action="store_true",
        help="With --timed --force-asr, request anonymous speaker diarization",
    )
    text_parser.add_argument(
        "--asr-context-file",
        help="With --timed --force-asr, read one public-metadata context JSON object",
    )

    comments_parser = subparsers.add_parser("comments", help="Fetch supported public comments")
    comments_parser.add_argument("url")
    comments_parser.add_argument("--sort", dest="sort_by", choices=("likes", "recent"), default="likes")
    comments_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Requested sample size from 1 to 100; the public source may return fewer comments",
    )

    download_parser = subparsers.add_parser("download", help="Explicitly download selected media")
    download_parser.add_argument("url")
    download_parser.add_argument("--output", required=True, dest="output_dir")
    download_parser.add_argument("--include", default="video,cover,images")

    capture_parser = subparsers.add_parser("capture", help="Create one PostBundle with optional enrichments")
    capture_parser.add_argument("url")
    capture_parser.add_argument("--comments", action="store_true")
    capture_parser.add_argument("--comment-sort", choices=("likes", "recent"), default="likes")
    capture_parser.add_argument(
        "--comment-limit",
        type=int,
        default=10,
        help="Maximum returned comment sample size from 1 to 100; the source may return fewer",
    )
    capture_parser.add_argument("--output", dest="output_dir")
    capture_parser.add_argument("--media", default="video,cover,images")
    capture_parser.add_argument("--no-text", action="store_true")

    subparsers.add_parser("doctor", help="Check optional local dependencies and secret names")
    return parser


def run_command(args: argparse.Namespace, toolkit: SocialMediaToolkit) -> dict[str, Any]:
    if args.command == "inspect":
        return toolkit.inspect(args.url)
    if args.command == "text":
        if (args.force_asr or args.speaker_info or args.asr_context_file) and not args.timed:
            raise ValueError("--force-asr, --speaker-info, and --asr-context-file require --timed")
        if (args.speaker_info or args.asr_context_file) and not args.force_asr:
            raise ValueError("--speaker-info and --asr-context-file require --force-asr")
        asr_context = _read_json_object(args.asr_context_file) if args.asr_context_file else None
        return toolkit.get_text(
            args.url,
            timed=args.timed,
            output_dir=args.text_output_dir,
            outputs=args.outputs,
            force_asr=args.force_asr,
            speaker_info=args.speaker_info,
            asr_context=asr_context,
        )
    if args.command == "comments":
        return toolkit.get_comments(args.url, sort_by=args.sort_by, limit=args.limit)
    if args.command == "download":
        return toolkit.download(args.url, output_dir=args.output_dir, include=args.include)
    if args.command == "capture":
        return toolkit.capture(
            args.url,
            include_text=not args.no_text,
            include_comments=args.comments,
            comment_sort=args.comment_sort,
            comment_limit=args.comment_limit,
            output_dir=args.output_dir,
            media=args.media,
        )
    if args.command == "doctor":
        return toolkit.doctor()
    raise ValueError(f"Unknown command: {args.command}")


def _read_json_object(path: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"ASR context file does not exist: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ASR context file is invalid JSON: {source}: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise ValueError("ASR context file must contain one non-empty JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_command(args, SocialMediaToolkit())
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") in {"error", "failed"} else 0


if __name__ == "__main__":
    sys.exit(main())
