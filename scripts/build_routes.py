#!/usr/bin/env python3
"""Refresh the static chart-route map from source links only (no result parsing)."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "sattaking" / "manifest.json"
OUT = ROOT / "data" / "sattaking" / "chart-routes.json"
HOME = "https://satta-king-fast.com/"
SUPPLEMENTS = {
    "delhi-6": "https://satta-king-fast.com/delhi-6/satta-result-chart/d6/",
    "star-gali": "https://satta-king-fast.com/star-gali/satta-result-chart/gs/",
}


def plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", fragment))).strip()


def norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def time24(value: str) -> str:
    return dt.datetime.strptime(value.strip().upper(), "%I:%M %p").strftime("%H:%M")


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    req = urllib.request.Request(
        HOME,
        headers={"User-Agent": "Mozilla/5.0 (compatible; HH-SourceRouteSetup/1.0; +https://hariharyana.vercel.app/)", "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        page = response.read(1_500_001)
    if len(page) > 1_500_000:
        raise SystemExit("Source homepage exceeded the size limit")
    source_html = page.decode("utf-8", "replace")

    by_name_time: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for game in manifest.get("games", []):
        by_name_time[(norm(str(game["name"])), time24(str(game["resultTime"])))].append(game)

    routes: dict[str, str] = {}
    rows = re.findall(
        r"<tr\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\bgame-result\b)[^>]*>(.*?)</tr>",
        source_html,
        re.I | re.S,
    )
    for row in rows:
        name = re.search(r"<h3\b[^>]*\bclass\s*=\s*['\"]game-name['\"][^>]*>(.*?)</h3>", row, re.I | re.S)
        draw_time = re.search(r"<h3\b[^>]*\bclass\s*=\s*['\"]game-time['\"][^>]*>(.*?)</h3>", row, re.I | re.S)
        chart = re.search(
            r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)['\"][^>]*>\s*Record\s+Chart",
            row,
            re.I | re.S,
        )
        if not (name and draw_time and chart):
            continue
        parsed_time = re.search(r"\d{1,2}:\d{2}\s*[AP]M", plain(draw_time.group(1)), re.I)
        if not parsed_time:
            continue
        matches = by_name_time.get((norm(plain(name.group(1))), time24(parsed_time.group(0))), [])
        if len(matches) == 1:
            routes[str(matches[0]["id"])] = html.unescape(chart.group(1))
        elif len(matches) > 1:
            raise SystemExit("Source game name/time maps to more than one catalog game")

    routes.update(SUPPLEMENTS)
    expected = {str(game["id"]) for game in manifest["games"]}
    if set(routes) != expected:
        missing = sorted(expected - set(routes))
        extra = sorted(set(routes) - expected)
        raise SystemExit(f"Source route coverage mismatch: missing={missing}; extra={extra}")
    document = {
        "schemaVersion": 1,
        "source": "SattaKing Fast",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "routes": [
            {"gameId": str(game["id"]), "url": routes[str(game["id"])]}
            for game in manifest["games"]
        ],
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {len(routes)} chart routes (118 source-homepage links and 2 validated supplements); result cells were not parsed.")


if __name__ == "__main__":
    main()
