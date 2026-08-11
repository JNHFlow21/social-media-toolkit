#!/usr/bin/env python3
"""Render a privacy-safe repository metrics SVG from GitHub-owned data."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


API = "https://api.github.com"
API_VERSION = "2022-11-28"


def _request_json(path: str, token: str, *, accept: str = "application/vnd.github+json") -> tuple[Any, Any]:
    request = Request(
        f"{API}{path}",
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "User-Agent": "repository-metrics-renderer",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API origin
        return json.load(response), response.headers


def fetch_snapshot(
    repository: str,
    token: str,
    traffic_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required when --snapshot is not supplied")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must use owner/name format")

    encoded = "/".join(quote(part, safe="") for part in repository.split("/", 1))
    repo, _ = _request_json(f"/repos/{encoded}", token)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    traffic_live = True
    try:
        views, _ = _request_json(f"/repos/{encoded}/traffic/views", token)
        clones, _ = _request_json(f"/repos/{encoded}/traffic/clones", token)
        traffic = {
            "unique_visitors_14d": int(views["uniques"]),
            "views_14d": int(views["count"]),
            "unique_cloners_14d": int(clones["uniques"]),
            "clones_14d": int(clones["count"]),
            "clone_series_14d": [
                {
                    "timestamp": str(item["timestamp"]),
                    "count": int(item["count"]),
                }
                for item in clones.get("clones", [])
                if isinstance(item, dict) and "timestamp" in item and "count" in item
            ],
            "traffic_as_of": generated_at,
        }
    except HTTPError as error:
        error.close()
        if error.code != 403 or traffic_fallback is None:
            raise
        required = {
            "unique_visitors_14d",
            "views_14d",
            "unique_cloners_14d",
            "clones_14d",
            "clone_series_14d",
            "traffic_as_of",
        }
        missing = sorted(required - traffic_fallback.keys())
        if missing:
            raise ValueError(f"traffic snapshot is missing: {', '.join(missing)}") from error
        traffic_live = False
        traffic = {
            "unique_visitors_14d": int(traffic_fallback["unique_visitors_14d"]),
            "views_14d": int(traffic_fallback["views_14d"]),
            "unique_cloners_14d": int(traffic_fallback["unique_cloners_14d"]),
            "clones_14d": int(traffic_fallback["clones_14d"]),
            "clone_series_14d": list(traffic_fallback["clone_series_14d"]),
            "traffic_as_of": str(traffic_fallback["traffic_as_of"]),
        }
    _, commit_headers = _request_json(f"/repos/{encoded}/commits?per_page=1", token)

    link = commit_headers.get("Link", "")
    last_page = re.search(r"[?&]page=(\d+)[^>]*>; rel=\"last\"", link)
    commit_count = int(last_page.group(1)) if last_page else 1

    return {
        "repository": repository,
        "created_at": repo["created_at"],
        "generated_at": generated_at,
        "stars": int(repo["stargazers_count"]),
        "forks": int(repo["forks_count"]),
        "commits": commit_count,
        **traffic,
        "traffic_live": traffic_live,
    }


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def render_svg(snapshot: dict[str, Any]) -> str:
    repository = escape(str(snapshot["repository"]))
    generated = _parse_time(str(snapshot["generated_at"]))
    traffic_as_of = _parse_time(str(snapshot.get("traffic_as_of", snapshot["generated_at"])))
    stars = max(0, int(snapshot["stars"]))
    total_clones = max(0, int(snapshot["clones_14d"]))
    clone_series = sorted(
        (
            _parse_time(str(item["timestamp"])),
            max(0, int(item["count"])),
        )
        for item in snapshot.get("clone_series_14d", [])
        if isinstance(item, dict) and "timestamp" in item and "count" in item
    )
    chart_start = clone_series[0][0] if clone_series else traffic_as_of - timedelta(days=13)
    chart_end = clone_series[-1][0] if clone_series else traffic_as_of
    if chart_end <= chart_start:
        chart_end = chart_start + timedelta(days=1)

    chart_x, chart_y, chart_w, chart_h = 64.0, 150.0, 540.0, 292.0
    baseline = chart_y + chart_h
    seconds = max((chart_end - chart_start).total_seconds(), 1.0)
    series_total = sum(count for _, count in clone_series)
    y_max = max(total_clones, series_total, 1)

    def x_for(date: datetime) -> float:
        return chart_x + chart_w * max(0.0, min(1.0, (date - chart_start).total_seconds() / seconds))

    def y_for(value: int) -> float:
        return baseline - chart_h * max(0.0, min(1.0, value / y_max))

    path = [f"M {chart_x:.1f} {y_for(0):.1f}"]
    cumulative = 0
    for date, count in clone_series:
        x = x_for(date)
        path.append(f"L {x:.1f} {y_for(cumulative):.1f}")
        cumulative += count
        path.append(f"L {x:.1f} {y_for(cumulative):.1f}")
    if cumulative < total_clones:
        cumulative = total_clones
    path.append(f"L {chart_x + chart_w:.1f} {y_for(cumulative):.1f}")
    line_path = " ".join(path)
    area_path = f"{line_path} L {chart_x + chart_w:.1f} {baseline:.1f} Z"

    ticks = sorted({0, max(1, math.ceil(y_max / 2)), y_max})
    grid = []
    for value in ticks:
        y = y_for(value)
        grid.append(
            f'<line x1="{chart_x:.1f}" y1="{y:.1f}" x2="{chart_x + chart_w:.1f}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{chart_x - 14:.1f}" y="{y + 5:.1f}" text-anchor="end" class="axis">{value}</text>'
        )

    metrics = (
        ("Stars", stars),
        ("Forks", int(snapshot["forks"])),
        ("Commits", int(snapshot["commits"])),
        ("Unique visitors · 14d", int(snapshot["unique_visitors_14d"])),
        ("Unique cloners · 14d", int(snapshot["unique_cloners_14d"])),
        ("Total clones · 14d", int(snapshot["clones_14d"])),
    )
    cards = []
    for index, (label, value) in enumerate(metrics):
        column, row = index % 2, index // 2
        x, y = 650 + column * 142, 154 + row * 104
        label_parts = label.split(" · ", 1)
        label_svg = f'<text x="15" y="25" class="metric-label">{escape(label_parts[0])}</text>'
        if len(label_parts) == 2:
            label_svg += f'<text x="15" y="40" class="metric-period">{escape(label_parts[1])}</text>'
        cards.append(
            f'<g transform="translate({x} {y})">'
            '<rect width="128" height="84" rx="17" class="card-shadow"/>'
            '<rect width="128" height="84" rx="17" class="card"/>'
            f'{label_svg}'
            f'<text x="15" y="70" class="metric-value">{escape(_compact(value))}</text>'
            '</g>'
        )

    empty_note = ""
    if total_clones == 0:
        empty_note = '<text x="334" y="292" text-anchor="middle" class="empty">No clones in this 14-day window</text>'

    updated = generated.strftime("%Y-%m-%d UTC")
    if snapshot.get("traffic_live", False):
        traffic_note = "GitHub Traffic: rolling 14-day owner view"
    else:
        traffic_note = f"GitHub Traffic owner snapshot: {traffic_as_of.strftime('%Y-%m-%d')}"
    start_label = chart_start.strftime("%Y-%m-%d")
    end_label = chart_end.strftime("%Y-%m-%d")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="560" viewBox="0 0 960 560" role="img" aria-labelledby="title desc">
  <title id="title">{repository} repository metrics</title>
  <desc id="desc">Cumulative clone curve for the rolling 14-day Traffic window with stars, forks, commits, visitors, cloners, and clone totals.</desc>
  <style>
    .background {{ fill: #ffffff; }}
    .outline {{ fill: none; stroke: #171717; stroke-width: 2.4; }}
    .title {{ font: 700 30px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #171717; }}
    .subtitle {{ font: 500 15px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #666666; }}
    .section {{ font: 700 18px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #252525; }}
    .grid {{ stroke: #dedede; stroke-width: 1.2; stroke-dasharray: 5 7; }}
    .axis {{ font: 12px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #777777; }}
    .area {{ fill: #dff7e5; opacity: .76; }}
    .curve-echo {{ fill: none; stroke: #171717; stroke-width: 5.6; opacity: .12; stroke-linejoin: round; }}
    .curve {{ fill: none; stroke: #18a558; stroke-width: 4; stroke-linecap: round; stroke-linejoin: round; }}
    .card-shadow {{ fill: #ececec; transform: translate(3px, 4px); }}
    .card {{ fill: #ffffff; stroke: #222222; stroke-width: 2; }}
    .metric-label {{ font: 650 11px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #555555; }}
    .metric-period {{ font: 600 9.5px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #888888; }}
    .metric-value {{ font: 800 28px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #171717; }}
    .empty {{ font: 600 16px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #888888; }}
    .footer {{ font: 12px ui-rounded, "Comic Sans MS", system-ui, sans-serif; fill: #666666; }}
  </style>
  <rect class="background" width="960" height="560" rx="24"/>
  <rect class="outline" x="12" y="12" width="936" height="536" rx="22"/>
  <text x="54" y="61" class="title">Repository Pulse</text>
  <text x="54" y="88" class="subtitle">{repository} · privacy-safe public activity</text>
  <path d="M 844 45 v34 m-13 -13 13 13 13 -13 M 825 92 h38" fill="none" stroke="#18a558" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="64" y="128" class="section">Total clones over time · rolling 14 days</text>
  {''.join(grid)}
  <line x1="{chart_x:.1f}" y1="{baseline:.1f}" x2="{chart_x + chart_w:.1f}" y2="{baseline:.1f}" class="outline"/>
  <path d="{area_path}" class="area"/>
  <path d="{line_path}" class="curve-echo"/>
  <path d="{line_path}" class="curve"/>
  {empty_note}
  <text x="{chart_x:.1f}" y="{baseline + 24:.1f}" class="axis">{start_label}</text>
  <text x="{chart_x + chart_w:.1f}" y="{baseline + 24:.1f}" text-anchor="end" class="axis">{end_label}</text>
  {''.join(cards)}
  <text x="54" y="520" class="footer">Stars, forks, and commits auto-refresh · {traffic_note} · Updated {updated}</text>
</svg>
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, help="GitHub owner/name")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path, help="Render from a local JSON snapshot instead of the API")
    parser.add_argument(
        "--traffic-snapshot",
        type=Path,
        help="Aggregate owner snapshot used only when GitHub Actions cannot read the Traffic API",
    )
    args = parser.parse_args()

    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
    else:
        traffic_fallback = json.loads(args.traffic_snapshot.read_text()) if args.traffic_snapshot else None
        snapshot = fetch_snapshot(
            args.repository,
            os.environ.get("GITHUB_TOKEN", ""),
            traffic_fallback,
        )
    if snapshot.get("repository") != args.repository:
        raise ValueError("snapshot repository does not match --repository")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_svg(snapshot))
    print(f"rendered {args.output} for {args.repository}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
