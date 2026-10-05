#!/usr/bin/env python3
"""Poll only due, unresolved current-month SattaKing cells and recent corrections.

The public repository contains source data only. Verified numeric results are posted
through a dedicated authenticated Edge Function before the archive is committed.
No Supabase service-role credential belongs in this repository or workflow.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sattaking"
MANIFEST_PATH = DATA / "manifest.json"
ROUTES_PATH = DATA / "chart-routes.json"
SOURCE_HOME = "https://satta-king-fast.com/"
IST = ZoneInfo("Asia/Kolkata")
USER_AGENT = "Mozilla/5.0 (compatible; HH-SourceRouteSetup/1.0; +https://hariharyana.vercel.app/)"
MAX_PAGE_BYTES = 1_500_000

# These are the abbreviated column labels used by the source's shared regional chart.
COLUMN_ALIASES = {
    "desawar": ["DSWR"],
    "faridabad": ["FRBD"],
    "ghaziabad": ["GZBD"],
    "shree-ganga-nagar": ["SRGN"],
    "shri-ganesh": ["SRGN"],
    "delhi-bazar": ["DLBZ"],
}


class SourceError(RuntimeError):
    pass


def compact_text(fragment: str) -> str:
    raw = re.sub(r"<[^>]*>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def normalize_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def parse_ist_time(value: str) -> dt.time:
    try:
        return dt.datetime.strptime(value.strip().upper(), "%I:%M %p").time()
    except ValueError as exc:
        raise SourceError(f"Invalid SattaKing draw time: {value!r}") from exc


def normalize_cell(value: str) -> str | None:
    value = compact_text(value).upper().replace("\xa0", "").strip()
    if re.fullmatch(r"\d{2}", value):
        return value
    if value == "XX":
        return value
    return None  # blanks, dashes and malformed values never become results


def canonical_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "satta-king-fast.com":
        raise SourceError("Chart route is outside the approved HTTPS source host")
    return parsed.path.rstrip("/").lower() + "/"


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            if response.status != 200:
                raise SourceError(f"Source returned HTTP {response.status}")
            body = response.read(MAX_PAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SourceError(f"Could not fetch source page: {type(exc).__name__}") from exc
    if len(body) > MAX_PAGE_BYTES:
        raise SourceError("Source page exceeded the size limit")
    return body.decode("utf-8", "replace")


def load_inputs() -> tuple[dict[str, Any], dict[str, str]]:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        route_doc = json.loads(ROUTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError("Feed manifest or route map is unavailable/invalid") from exc
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("games"), list):
        raise SourceError("Unsupported feed manifest")
    route_items = route_doc.get("routes")
    if route_doc.get("schemaVersion") != 1 or not isinstance(route_items, list):
        raise SourceError("Unsupported chart route map")
    routes: dict[str, str] = {}
    for item in route_items:
        if not isinstance(item, dict) or not isinstance(item.get("gameId"), str):
            raise SourceError("Invalid chart route entry")
        game_id = item["gameId"]
        if game_id in routes:
            raise SourceError("Duplicate chart route game ID")
        url = str(item.get("url", ""))
        canonical_path(url)  # validates host and scheme
        routes[game_id] = url
    manifest_ids = {str(g.get("id")) for g in manifest["games"] if isinstance(g, dict)}
    if not manifest_ids or manifest_ids != set(routes):
        raise SourceError("Chart route map does not exactly cover the source catalog")
    return manifest, routes


def parse_homepage(html_text: str, routes: dict[str, str]) -> tuple[dt.date | None, dict[tuple[str, dt.date], str | None]]:
    visible = compact_text(html_text)
    updated = re.search(r"\bUpdated:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", visible, re.I)
    page_date: dt.date | None = None
    if updated:
        try:
            page_date = dt.datetime.strptime(updated.group(1), "%B %d, %Y").date()
        except ValueError:
            page_date = None

    game_by_path = {canonical_path(url): game_id for game_id, url in routes.items()}
    values: dict[tuple[str, dt.date], str | None] = {}
    row_re = re.compile(
        r"<tr\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\bgame-result\b)[^>]*>(.*?)</tr>", re.I | re.S
    )
    link_re = re.compile(
        r"<a\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)['\"][^>]*>\s*Record\s+Chart", re.I | re.S
    )
    for block in row_re.findall(html_text):
        link = link_re.search(block)
        if not link:
            continue
        try:
            game_id = game_by_path.get(canonical_path(link.group(1)))
        except SourceError:
            continue
        if not game_id:
            continue
        yesterday = re.search(
            r"<td\b[^>]*\bclass\s*=\s*['\"][^'\"]*\byesterday-number\b[^'\"]*['\"][^>]*>(.*?)</td>",
            block,
            re.I | re.S,
        )
        today = re.search(
            r"<td\b[^>]*\bclass\s*=\s*['\"][^'\"]*\btoday-number\b[^'\"]*['\"][^>]*>(.*?)</td>",
            block,
            re.I | re.S,
        )
        if not (yesterday and today and page_date):
            continue
        values[(game_id, page_date - dt.timedelta(days=1))] = normalize_cell(yesterday.group(1))
        values[(game_id, page_date)] = normalize_cell(today.group(1))
    return page_date, values


def month_file(month_key: str) -> Path:
    return DATA / "months" / f"{month_key}.json"


def load_month(month_key: str) -> dict[str, Any]:
    path = month_file(month_key)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceError(f"Invalid monthly archive: {month_key}") from exc
        if data.get("schemaVersion") != 1 or data.get("month") != month_key or not isinstance(data.get("results"), list):
            raise SourceError(f"Unsupported monthly archive: {month_key}")
        return data
    return {"schemaVersion": 1, "month": month_key, "results": []}


def month_index(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    indexed: dict[tuple[str, str], str] = {}
    for row in data["results"]:
        if not isinstance(row, dict):
            continue
        game_id, date, value = row.get("gameId"), row.get("date"), row.get("value")
        if not (isinstance(game_id, str) and isinstance(date, str) and isinstance(value, str)):
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or date[:7] != data["month"]:
            continue
        if re.fullmatch(r"\d{2}", value) or value == "XX":
            indexed[(game_id, date)] = value
    return indexed


def due(game: dict[str, Any], result_date: dt.date, now: dt.datetime) -> bool:
    if result_date < now.date():
        return True
    if result_date > now.date():
        return False
    try:
        return parse_ist_time(str(game.get("resultTime", ""))) <= now.timetz().replace(tzinfo=None)
    except SourceError:
        return False


def chart_correction_allowed(existing: str | None, date_text: str, recent_dates: set[dt.date]) -> bool:
    return existing not in (None, "XX") and date_text in {day.isoformat() for day in recent_dates}


def plan_chart_jobs(
    games: list[dict[str, Any]],
    indexed: dict[tuple[str, str], str],
    month_key: str,
    today: dt.date,
    now: dt.datetime,
    recent_dates: set[dt.date],
    home_values: dict[tuple[str, dt.date], str | None],
    homepage_valid: bool,
) -> dict[str, set[str]]:
    """Select due current-month unresolved cells and safe recent correction fallbacks."""
    jobs: dict[str, set[str]] = {}
    first_day = today.replace(day=1)
    for game in games:
        game_id = str(game["id"])
        day = first_day
        while day <= today:
            key = (game_id, day.isoformat())
            if due(game, day, now) and indexed.get(key) in (None, "XX"):
                jobs.setdefault(game_id, set()).add(key[1])
            day += dt.timedelta(days=1)

        # The homepage normally covers 118 games. For any recent verified value it
        # did not provide numerically (blank/XX/missing row), fall back to that game's
        # chart so a numeric correction can still be detected. If the homepage itself
        # is stale/unavailable, skip this correction fallback and fail closed.
        if homepage_valid:
            for recent_day in recent_dates:
                if recent_day.strftime("%Y-%m") != month_key or not due(game, recent_day, now):
                    continue
                date_text = recent_day.isoformat()
                key = (game_id, date_text)
                if not chart_correction_allowed(indexed.get(key), date_text, recent_dates):
                    continue
                incoming = home_values.get((game_id, recent_day))
                if incoming not in (None, "XX"):
                    continue
                jobs.setdefault(game_id, set()).add(date_text)
    return jobs


def find_chart_target_column(chart_html: str, game: dict[str, Any]) -> int:
    marker = re.search(r"<div\b[^>]*\bid\s*=\s*['\"]mix-chart['\"][^>]*>", chart_html, re.I)
    if not marker:
        raise SourceError(f"Monthly chart table missing for {game.get('id')}")
    table_end = chart_html.find("</table>", marker.end())
    if table_end < 0:
        raise SourceError(f"Monthly chart table not closed for {game.get('id')}")
    fragment = chart_html[marker.end():table_end]
    header_row = re.search(
        r"<tr\b[^>]*\bclass\s*=\s*['\"][^'\"]*\bdate-name\b[^'\"]*['\"][^>]*>(.*?)</tr>",
        fragment,
        re.I | re.S,
    )
    if not header_row:
        raise SourceError(f"Monthly chart headers missing for {game.get('id')}")
    cells = re.findall(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>", header_row.group(1), re.I | re.S)
    headers = [normalize_name(compact_text(cell)) for cell in cells]
    names = {normalize_name(str(game.get("name", "")))}
    names.update(normalize_name(alias) for alias in COLUMN_ALIASES.get(str(game.get("id")), []))
    matches = [i for i, header in enumerate(headers) if i > 0 and header in names]
    if len(matches) != 1:
        raise SourceError(f"Expected one target column for {game.get('id')}; found {len(matches)}")
    return matches[0]


def parse_month_values(chart_html: str, game: dict[str, Any], month_key: str) -> dict[str, str | None]:
    marker = re.search(r"<div\b[^>]*\bid\s*=\s*['\"]mix-chart['\"][^>]*>", chart_html, re.I)
    if not marker:
        raise SourceError(f"Monthly chart table missing for {game.get('id')}")
    table_end = chart_html.find("</table>", marker.end())
    if table_end < 0:
        raise SourceError(f"Monthly chart table not closed for {game.get('id')}")
    fragment = chart_html[marker.end():table_end]
    target_col = find_chart_target_column(chart_html, game)
    result: dict[str, str | None] = {}
    for block in re.findall(r"<tr\b[^>]*\bclass\s*=\s*['\"][^'\"]*\bday-number\b[^'\"]*['\"][^>]*>(.*?)</tr>", fragment, re.I | re.S):
        day_cell = re.search(r"<td\b[^>]*\btitle\s*=\s*['\"]([^'\"]+)['\"][^>]*>", block, re.I | re.S)
        if not day_cell:
            continue
        try:
            source_date = dt.datetime.strptime(html.unescape(day_cell.group(1)).strip(), "%B %d, %Y").date()
        except ValueError:
            continue
        if source_date.strftime("%Y-%m") != month_key:
            continue
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", block, re.I | re.S)
        if target_col >= len(cells):
            raise SourceError(f"Target column is missing on {source_date} for {game.get('id')}")
        result[source_date.isoformat()] = normalize_cell(cells[target_col])
    return result


def upsert_cell(indexed: dict[tuple[str, str], str], game_id: str, date: str, incoming: str | None, allow_correction: bool) -> tuple[bool, bool]:
    """Return (archive_changed, numeric_value_to_ingest). Never retract a valid value on blank/XX."""
    if incoming is None:
        return False, False
    key = (game_id, date)
    old = indexed.get(key)
    if incoming == "XX":
        if old is None:
            indexed[key] = incoming
            return True, False
        return False, False
    if old is None or old == "XX":
        indexed[key] = incoming
        return True, True
    if old != incoming and allow_correction:
        indexed[key] = incoming
        return True, True
    return False, False


def call_ingest(results: list[dict[str, str]]) -> None:
    if not results:
        return
    url = os.environ.get("SATTAKING_INGEST_URL", "").strip()
    secret = os.environ.get("SATTAKING_INGEST_SECRET", "")
    if not url or len(secret) < 32:
        raise SourceError("SattaKing ingest endpoint/secret is not configured")
    if not url.startswith("https://") or "supabase.co/functions/v1/hh-api/sattaking-ingest" not in url:
        raise SourceError("Unexpected SattaKing ingest endpoint")
    payload = json.dumps({"results": results}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-sattaking-ingest": secret,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = json.loads(response.read(256_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SourceError(f"Ingest endpoint rejected the batch (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SourceError(f"Ingest endpoint failed: {type(exc).__name__}") from exc
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise SourceError("Ingest endpoint did not confirm the result batch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate due current-month cells without writing or submitting")
    args = parser.parse_args()
    manifest, routes = load_inputs()
    games = [g for g in manifest["games"] if isinstance(g, dict) and str(g.get("id")) in routes]
    now = dt.datetime.now(IST)
    month_key = now.strftime("%Y-%m")
    today = now.date()
    recent_dates = {today}
    yesterday = today - dt.timedelta(days=1)
    if yesterday.strftime("%Y-%m") == month_key:
        recent_dates.add(yesterday)

    month_data = load_month(month_key)
    indexed = month_index(month_data)
    game_by_id = {str(g["id"]): g for g in games}
    order_by_id = {str(g["id"]): int(g.get("order", 9999)) for g in games}
    original = dict(indexed)
    ingest: dict[tuple[str, str], dict[str, str]] = {}
    warnings: list[str] = []

    # The source landing page contains today's/yesterday's values for 118 games.
    # It is used only for those two dates; older verified values are not examined.
    home_date: dt.date | None = None
    home_values: dict[tuple[str, dt.date], str | None] = {}
    homepage_valid = False
    try:
        home_html = fetch_text(SOURCE_HOME)
        home_date, home_values = parse_homepage(home_html, routes)
        if home_date != today:
            warnings.append(f"Source homepage date is {home_date}, expected {today}; recent homepage values ignored")
            home_values = {}
            home_date = None
        else:
            homepage_valid = True
    except SourceError as exc:
        warnings.append(f"Source homepage unavailable: {exc}")

    for (game_id, source_date), incoming in home_values.items():
        if source_date.strftime("%Y-%m") != month_key or source_date not in recent_dates:
            continue
        game = game_by_id.get(game_id)
        if not game or not due(game, source_date, now):
            continue
        key = (game_id, source_date.isoformat())
        old = indexed.get(key)
        # Recent verification is limited to today/yesterday. Only numeric-to-numeric
        # changes are treated as corrections; blank/XX responses never erase a result.
        allow_correction = old is not None and old != "XX" and source_date in recent_dates
        changed, numeric = upsert_cell(indexed, game_id, key[1], incoming, allow_correction)
        if numeric:
            ingest[key] = {"game_id": game_id, "date": key[1], "value": indexed[key]}

    # A chart is fetched only for due current-month missing/XX cells, or as a
    # same-month today/yesterday correction fallback when the homepage has no numeric
    # value for an already-verified cell. Older verified values are never re-read.
    chart_jobs = plan_chart_jobs(
        games, indexed, month_key, today, now, recent_dates, home_values, homepage_valid
    )

    def fetch_game_chart(game_id: str) -> tuple[str, dict[str, str | None]]:
        game = game_by_id[game_id]
        route = routes[game_id]
        query = urllib.parse.urlencode({"month": f"{now.month:02d}", "year": str(now.year)})
        url = route + ("&" if "?" in route else "?") + query
        return game_id, parse_month_values(fetch_text(url), game, month_key)

    chart_values: dict[str, dict[str, str | None]] = {}
    if chart_jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_game_chart, game_id): game_id for game_id in chart_jobs}
            for future in concurrent.futures.as_completed(futures):
                game_id = futures[future]
                try:
                    found_id, values = future.result()
                    chart_values[found_id] = values
                except Exception as exc:  # fail closed for this chart; other games may proceed
                    warnings.append(f"Chart check failed for {game_id}: {type(exc).__name__}")

    for game_id, pending_dates in chart_jobs.items():
        values = chart_values.get(game_id, {})
        for date_text in sorted(pending_dates):
            incoming = values.get(date_text)
            key = (game_id, date_text)
            allow_correction = chart_correction_allowed(indexed.get(key), date_text, recent_dates)
            changed, numeric = upsert_cell(indexed, game_id, date_text, incoming, allow_correction=allow_correction)
            if numeric:
                ingest[key] = {"game_id": game_id, "date": date_text, "value": indexed[key]}

    changed_keys = {key for key in set(original) | set(indexed) if original.get(key) != indexed.get(key)}
    if not changed_keys:
        print(f"No archive changes. Checked {len(chart_jobs)} due unresolved game charts; recent values limited to {sorted(d.isoformat() for d in recent_dates)}.")
        for warning in warnings:
            print(f"::warning::{warning}")
        return 0

    if args.dry_run:
        print(f"Dry run: {len(changed_keys)} archive cell(s) would change; {len(ingest)} numeric result(s) would be submitted; {len(chart_jobs)} unresolved/recent charts checked.")
        for warning in warnings:
            print(f"::warning::{warning}")
        return 0

    # Ingest numeric results first. If Supabase rejects the batch, don't persist a newer
    # public archive that could hide the result from a retry on the next scheduled run.
    call_ingest(list(ingest.values()))

    order = {gid: i for i, gid in enumerate(game_by_id)}
    month_data["results"] = [
        {"date": date, "gameId": game_id, "value": value}
        for (game_id, date), value in sorted(
            indexed.items(), key=lambda item: (item[0][1], order.get(item[0][0], 9999), item[0][0])
        )
    ]
    month_file(month_key).parent.mkdir(parents=True, exist_ok=True)
    month_file(month_key).write_text(json.dumps(month_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["months"] = sorted(set(manifest.get("months", [])) | {month_key})
    manifest["updatedAt"] = now.isoformat(timespec="seconds")
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {len(changed_keys)} archive cells; sent {len(ingest)} numeric result(s) to the platform; checked {len(chart_jobs)} unresolved/recent charts.")
    for warning in warnings:
        print(f"::warning::{warning}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SourceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
