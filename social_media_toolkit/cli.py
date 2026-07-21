from __future__ import annotations

import argparse
import json
import sys
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

    comments_parser = subparsers.add_parser("comments", help="Fetch supported public comments")
    comments_parser.add_argument("url")
    comments_parser.add_argument("--sort", dest="sort_by", choices=("likes", "recent"), default="likes")
    comments_parser.add_argument("--limit", type=int, default=10)

    download_parser = subparsers.add_parser("download", help="Explicitly download selected media")
    download_parser.add_argument("url")
    download_parser.add_argument("--output", required=True, dest="output_dir")
    download_parser.add_argument("--include", default="video,cover,images")

    capture_parser = subparsers.add_parser("capture", help="Create one PostBundle with optional enrichments")
    capture_parser.add_argument("url")
    capture_parser.add_argument("--comments", action="store_true")
    capture_parser.add_argument("--comment-sort", choices=("likes", "recent"), default="likes")
    capture_parser.add_argument("--comment-limit", type=int, default=10)
    capture_parser.add_argument("--output", dest="output_dir")
    capture_parser.add_argument("--media", default="video,cover,images")
    capture_parser.add_argument("--no-text", action="store_true")

    subparsers.add_parser("doctor", help="Check optional local dependencies and secret names")
    return parser


def run_command(args: argparse.Namespace, toolkit: SocialMediaToolkit) -> dict[str, Any]:
    if args.command == "inspect":
        return toolkit.inspect(args.url)
    if args.command == "text":
        return toolkit.get_text(
            args.url,
            timed=args.timed,
            output_dir=args.text_output_dir,
            outputs=args.outputs,
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
