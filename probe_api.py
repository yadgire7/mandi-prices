#!/usr/bin/env python3
"""Phase 0 probe for the Shetkari Shala daily mandi price post.

Answers four questions with real data from data.gov.in, before any of the
real script is written:

1. Which field names do the two AGMARKNET resources use?
2. Which filter parameter narrows a request to Maharashtra, and to one date?
3. Does the "current daily price" resource hold one day or several, and does
   the variety-wise resource return each of the last few days?
4. For every configured mandi-crop pair: which exact market and commodity
   names match, and which varieties are reported, with their latest prices?

Usage:
    export DATA_GOV_IN_API_KEY=...      # or put it in .env (chmod 600)
    python probe_api.py                 # writes data/phase0/<run>/report.md
    python probe_api.py --days 7

It only makes read-only GET requests to a public government API, pausing
between pages. The API key is never printed or saved.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from mandi.api import (  # noqa: E402  shared, tested client and helpers
    API_BASE, DATE_FORMATS, FIELDS, PAGE_LIMIT, RESOURCES, RETRIES, ApiError, Client,
    date_format, field_map, load_api_key as _load_api_key, norm, parse_date, redact,
    to_int, urllib_fetch,
)

HERE = Path(__file__).resolve().parent


def load_api_key(env=os.environ, dotenv: Path = HERE / ".env"):
    return _load_api_key(env=env, dotenv=dotenv)


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    crops = config.get("crops") or {}
    for mandi in config.get("mandis") or []:
        for crop in mandi.get("crops") or []:
            if crop not in crops:
                raise ValueError(f"mandi {mandi['key']!r} uses crop {crop!r}, "
                                 "which is not defined under crops:")
    return config


# --------------------------------------------------------------------------
# Discovery


def find_filter(client: Client, resource_id: str, field: str, value: str,
                unfiltered_total: int | None, check) -> tuple[str | None, list[str]]:
    """Try filter spellings until one narrows the results to `value`.

    A spelling works when every returned record passes `check` and the total
    is smaller than without the filter (so an ignored filter cannot pass).
    """
    attempts = []
    for param in (f"filters[{field}]", f"filters[{field}.keyword]"):
        try:
            page = client.get(resource_id, {param: value, "limit": "50"})
        except ApiError as err:
            attempts.append(f"`{param}`: request failed ({err})")
            continue
        recs = page.get("records") or []
        total = to_int(page.get("total"))
        narrowed = unfiltered_total is None or total is None or total < unfiltered_total
        if recs and narrowed and all(check(r) for r in recs):
            attempts.append(f"`{param}`: works ({total} records)")
            return param, attempts
        attempts.append(f"`{param}`: did not filter ({len(recs)} records, total {total})")
    return None, attempts


def match_pairs(rows: list[dict], config: dict) -> list[dict]:
    """For each configured mandi: the names that matched, and per crop the
    varieties reported with their latest price."""
    by_market: dict[str, list[dict]] = defaultdict(list)
    markets_by_district: dict[str, set[str]] = defaultdict(set)
    all_markets: set[str] = set()
    for row in rows:
        by_market[norm(row["market"])].append(row)
        markets_by_district[norm(row.get("district"))].add(row["market"])
        all_markets.add(row["market"])

    results = []
    for mandi in config["mandis"]:
        wanted = {norm(m) for m in mandi["market"]}
        mrows = [r for key in wanted for r in by_market.get(key, [])]
        matched_names = sorted({r["market"] for r in mrows})
        commodities = Counter(r["commodity"] for r in mrows)
        entry = {
            "key": mandi["key"],
            "district": mandi.get("district"),
            "market_names": matched_names,
            "market_suggestions": [],
            "commodities": commodities.most_common(),
            "crops": [],
        }
        if not mrows:
            pool = markets_by_district.get(norm(mandi.get("district"))) or all_markets
            close = difflib.get_close_matches(mandi["market"][0], sorted(pool), n=5, cutoff=0.4)
            entry["market_suggestions"] = close or sorted(pool)[:10]
        for crop in mandi.get("crops") or []:
            names = {norm(c) for c in config["crops"][crop]}
            crows = [r for r in mrows if norm(r["commodity"]) in names]
            varieties = defaultdict(list)
            for r in crows:
                varieties[(r.get("variety") or "", r.get("grade") or "")].append(r)
            vlist = []
            for (variety, grade), vrows in sorted(varieties.items()):
                dated = [(parse_date(r.get("arrival_date")), r) for r in vrows]
                dated = [(d, r) for d, r in dated if d]
                latest_date, latest = max(dated, key=lambda x: x[0]) if dated else (None, vrows[-1])
                vlist.append({
                    "variety": variety, "grade": grade, "rows": len(vrows),
                    "days": len({d for d, _ in dated}),
                    "latest_date": latest_date.isoformat() if latest_date else None,
                    "min": latest.get("min_price"), "modal": latest.get("modal_price"),
                    "max": latest.get("max_price"),
                })
            crop_entry = {
                "crop": crop,
                "commodity_names": sorted({r["commodity"] for r in crows}),
                "varieties": vlist,
                "suggestions": [],
            }
            if mrows and not crows:
                crop_entry["suggestions"] = difflib.get_close_matches(
                    config["crops"][crop][0], list(commodities), n=5, cutoff=0.4)
            entry["crops"].append(crop_entry)
        results.append(entry)
    return results


def canonical(rows: list[dict], fields: dict[str, str]) -> list[dict]:
    """Rename each record's fields to our lower-case names."""
    return [{name: r.get(src) for name, src in fields.items()} for r in rows]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------
# The probe


def run_probe(client: Client, config: dict, out_dir: Path, today: dt.date,
              days: int = 7) -> dict:
    state = config.get("state", "Maharashtra")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict = {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
                     "today": today.isoformat(), "resources": {}, "problems": []}
    all_rows: list[dict] = []
    current_keys: Counter = Counter()

    for name, rid in RESOURCES.items():
        info: dict = {"id": rid}
        summary["resources"][name] = info
        try:
            sample_page = client.get(rid, {"limit": "1"})
        except ApiError as err:
            info["error"] = str(err)
            summary["problems"].append(f"{name}: {err}")
            continue
        sample = (sample_page.get("records") or [{}])[0]
        fields = field_map(sample)
        info["fields_raw"] = list(sample)
        info["fields_missing"] = [f for f in FIELDS if f not in fields]
        info["total_all_india"] = to_int(sample_page.get("total"))
        if "state" not in fields or "market" not in fields or "commodity" not in fields:
            summary["problems"].append(f"{name}: state, market or commodity field not found")
            continue

        state_param, attempts = find_filter(
            client, rid, fields["state"], state, info["total_all_india"],
            lambda r, k=fields["state"]: r.get(k) == state)
        info["state_filter"] = state_param
        info["state_filter_attempts"] = attempts
        if not state_param:
            summary["problems"].append(f"{name}: no filter narrowed results to {state}")
            continue

        if name == "current":
            raw, total = client.records(rid, {state_param: state})
            rows = canonical(raw, fields)
            info["rows"] = len(rows)
            info["total_reported"] = total
            info["dates"] = sorted(Counter(r.get("arrival_date") for r in rows).items(),
                                   key=lambda kv: parse_date(kv[0]) or dt.date.min)
            current_keys.update((norm(r["market"]), norm(r["commodity"])) for r in rows)
        else:
            date_key = fields.get("arrival_date")
            fmt = date_format(str(sample.get(date_key) or "")) if date_key else None
            info["date_format"] = fmt
            if not fmt:
                summary["problems"].append(f"{name}: could not read the arrival date format")
                continue
            sample_date = str(sample[date_key]).strip()
            date_param, dattempts = find_filter(
                client, rid, date_key, sample_date, info["total_all_india"],
                lambda r, k=date_key, v=sample_date: str(r.get(k)).strip() == v)
            info["date_filter"] = date_param
            info["date_filter_attempts"] = dattempts
            if not date_param:
                summary["problems"].append(f"{name}: no filter narrowed results to one date")
                continue
            rows = []
            per_day = []
            for back in range(days):
                day = today - dt.timedelta(days=back)
                raw, _ = client.records(rid, {state_param: state,
                                              date_param: day.strftime(fmt)})
                per_day.append((day.isoformat(), day.strftime("%a"), len(raw)))
                rows.extend(canonical(raw, fields))
            info["rows"] = len(rows)
            info["per_day"] = per_day

        write_csv(out_dir / f"rows_{name}.csv", rows)
        all_rows.extend(rows)

    summary["pairs"] = match_pairs([r for r in all_rows if r.get("market") and r.get("commodity")],
                                   config)
    for entry in summary["pairs"]:
        for crop in entry["crops"]:
            crop["in_current"] = sum(current_keys[(norm(m), norm(c))]
                                     for m in entry["market_names"]
                                     for c in crop["commodity_names"])
    summary["requests"] = client.requests
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    return summary


def render_report(s: dict) -> str:
    out = [f"# Phase 0 probe report", "",
           f"Run {s['run_at']}, looking back from {s['today']}, {s.get('requests', 0)} API requests.", ""]
    if s["problems"]:
        out += ["## Problems", ""] + [f"- {p}" for p in s["problems"]] + [""]

    out += ["## Resources", ""]
    for name, info in s["resources"].items():
        out += [f"### {name} (`{info['id']}`)", ""]
        if "error" in info:
            out += [f"Request failed: {info['error']}", ""]
            continue
        out.append(f"- Fields: {', '.join(info.get('fields_raw', []))}")
        if info.get("fields_missing"):
            out.append(f"- Missing fields we need: {', '.join(info['fields_missing'])}")
        out.append(f"- All-India total: {info.get('total_all_india')}")
        for a in info.get("state_filter_attempts", []):
            out.append(f"- State filter {a}")
        for a in info.get("date_filter_attempts", []):
            out.append(f"- Date filter {a}")
        if "dates" in info:
            out.append(f"- Maharashtra rows: {info.get('rows')} across {len(info['dates'])} arrival date(s):")
            out += [f"    - {d}: {n} rows" for d, n in info["dates"]]
            if len(info["dates"]) <= 1:
                out.append("- **One-day snapshot:** use the variety-wise resource for recent days.")
        if "per_day" in info:
            out.append(f"- Maharashtra rows per day (date format `{info.get('date_format')}`):")
            out += [f"    - {d} ({wd}): {n} rows" for d, wd, n in info["per_day"]]
        out.append("")

    out += ["## Mandi-crop pairs", "",
            "| Mandi | Crop | Market name | Commodity name | Variety / grade | Latest | Modal (min-max) | Days | In current |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    gaps = []
    for e in s.get("pairs", []):
        market = ", ".join(e["market_names"]) or "**not found**"
        if not e["market_names"]:
            gaps.append(f"- **{e['key']}**: no market matched. Closest names: "
                        + ", ".join(f"`{m}`" for m in e["market_suggestions"]))
        if not e["crops"]:
            out.append(f"| {e['key']} | (none chosen) | {market} | | | | | | |")
        for c in e["crops"]:
            if not c["varieties"]:
                out.append(f"| {e['key']} | {c['crop']} | {market} | **not found** | | | | | |")
                if e["market_names"]:
                    gaps.append(f"- **{e['key']} / {c['crop']}**: not reported at this market in the window. "
                                f"Closest commodity names: {', '.join(c['suggestions']) or 'none'}")
                continue
            for v in c["varieties"]:
                out.append(
                    f"| {e['key']} | {c['crop']} | {market} | {', '.join(c['commodity_names'])} | "
                    f"{v['variety']} / {v['grade']} | {v['latest_date']} | "
                    f"{v['modal']} ({v['min']}-{v['max']}) | {v['days']} | {c.get('in_current', 0)} |")
    out.append("")
    if gaps:
        out += ["## Gaps to fix in config.yaml", ""] + gaps + [""]

    out += ["## What each market reports", "",
            "Top commodities by row count, to pick crops for Pune and Mumbai and to spot misspellings.", ""]
    for e in s.get("pairs", []):
        if e["commodities"]:
            top = ", ".join(f"{c} ({n})" for c, n in e["commodities"][:15])
            out.append(f"- **{e['key']}**: {top}")
    out += ["", "## Next steps", "",
            "1. Fix any gaps above in `config.yaml`, and delete candidate names that never matched.",
            "2. Pin one variety and grade per mandi-crop pair from the table.",
            "3. Note which resource Phase 1 should read, from the dates sections above.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--out", type=Path, default=HERE / "data" / "phase0")
    parser.add_argument("--days", type=int, default=7, help="days to look back (default 7)")
    args = parser.parse_args(argv)

    key, notes = load_api_key()
    for note in notes:
        print(f"warning: {note}", file=sys.stderr)
    if not key:
        print("No API key. Set DATA_GOV_IN_API_KEY, or put it in .env "
              "(see README.md).", file=sys.stderr)
        return 2

    config = load_config(args.config)
    run_dir = args.out / dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    print(f"Probing data.gov.in; this makes a few dozen requests with pauses...")
    summary = run_probe(Client(key), config, run_dir, dt.date.today(), args.days)
    print(f"Report: {run_dir / 'report.md'}")
    for problem in summary["problems"]:
        print(f"problem: {problem}", file=sys.stderr)
    got_rows = any(info.get("rows") for info in summary["resources"].values())
    return 0 if got_rows else 1


if __name__ == "__main__":
    sys.exit(main())
