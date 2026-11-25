"""One run: fetch, check, compare, build pages, decide on a post, save the snapshot."""
from __future__ import annotations

import datetime as dt
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mandi import marathi, pages, post, rows, state
from mandi.api import ApiError, Client, canonical, norm

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")
MAX_NOTE = 1000


@dataclass
class Options:
    now: dt.datetime
    site_dir: Path
    state_path: Path
    force_post: bool = False
    note: str = ""
    save_state: bool = True


@dataclass
class Result:
    status: str = "OK"
    post: str | None = None
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)  # one line of the run log (runs.tsv)


def clean_note(note: str) -> str:
    """Keep the admin's text, minus control characters, at most 1,000 characters."""
    kept = "".join(ch for ch in str(note or "")
                   if ch == "\n" or not unicodedata.category(ch).startswith("C"))
    return kept.strip()[:MAX_NOTE]


def api_source(client: Client, cfg: dict) -> Callable[[dt.date], list[dict]]:
    """Fetch the configured number of days for the state, one date at a time."""
    api = cfg["api"]

    def fetch(today: dt.date) -> list[dict]:
        records = []
        for back in range(int(cfg["fetch"]["days"])):
            day = today - dt.timedelta(days=back)
            raw, _ = client.records(api["resource"], {api["state_filter"]: cfg["state"],
                                                      api["date_filter"]: day.strftime(api["date_format"])})
            records.extend(canonical(raw))
        return records

    return fetch


def is_first_run_window(now_ist: dt.datetime, first_run: str) -> bool:
    hour, minute = (int(x) for x in first_run.split(":"))
    return now_ist.time() >= dt.time(hour, minute)


def run(cfg: dict, source: Callable[[dt.date], list[dict]], opts: Options) -> Result:
    result = Result()
    now = opts.now.astimezone(IST)
    today = now.date()

    try:
        records = source(today)
    except ApiError as err:
        result.status = "FAIL"
        result.summary.append("No data: data.gov.in did not answer. Nothing was posted.")
        result.warnings.append(str(err))
        return result
    result.info.append(f"Records fetched: {len(records)}")

    prices, rejects = rows.validate(records, cfg, today)
    result.info.append(f"Prices accepted: {len(prices)}")
    yesterday = today - dt.timedelta(days=1)
    result.stats.update(
        fetched=len(records), accepted=len(prices), rejected=len(rejects),
        today=sum(p.date == today for p in prices),
        yesterday=sum(p.date == yesterday for p in prices),
        districts_today=len({norm(p.district) for p in prices if p.date == today}))
    duplicates = len(records) - len(prices) - len(rejects)
    if duplicates > 0:
        result.info.append(f"Exact duplicate records merged: {duplicates}")
    if rejects:
        result.warnings += [f"Rejected: {r}" for r in rejects]
    if not prices:
        result.status = "FAIL"
        result.summary.append("No usable prices this run. Nothing was posted.")
        return result

    snap = state.load(opts.state_path)
    new, changed = state.compare(snap.prices, prices)
    if not snap.exists:
        # First run: save a baseline. Marking all ~1,400 prices as new helps no one.
        result.warnings.append(f"No previous snapshot: saved {len(prices)} prices as the baseline; "
                               "nothing is marked new this run.")
        new, changed = [], []
    merged = state.merge(snap.prices, prices, today, int(cfg["fetch"]["keep_days"]))
    latest = state.latest_by_series(merged)
    changed_keys = {p.key for p in new} | {p.key for _, p in changed}

    factor = float(cfg["checks"]["jump_factor"])
    jumps = {series for series, (p, prev) in latest.items() if state.is_jump(p, prev, factor)}
    for series in sorted(jumps):
        p, prev = latest[series]
        if p.key in changed_keys:
            result.warnings.append(
                f"Unusual jump, please check: {p.market} / {p.commodity} / {p.variety} "
                f"{prev.modal} on {prev.date} -> {p.modal} on {p.date}")
    for old, cur in changed:
        result.info.append(f"Corrected: {cur.market} / {cur.commodity} / {cur.date}: "
                           f"{old.numbers} -> {cur.numbers}")

    names = marathi.Names(cfg)
    unnamed = sorted({p.market for p, _ in latest.values() if names.market_devanagari(p.market) is None})
    if unnamed:
        result.info.append(f"Markets shown in English (no Devanagari name yet, add to "
                           f"names_markets_mr.yaml): {len(unnamed)}: " + "; ".join(unnamed[:10]))
    shown = [p for p, _ in latest.values()]
    for kind, field_name, label in (("commodities", "commodity", "Produce"),
                                    ("varieties", "variety", "Varieties")):
        missing = sorted({getattr(p, field_name) for p in shown if getattr(p, field_name)
                          and not names.has_name(kind, getattr(p, field_name))
                          and not names.term(getattr(p, field_name))})
        if missing:
            result.info.append(f"{label} shown in English (add to names_produce_mr.yaml): "
                               f"{len(missing)}: " + "; ".join(missing[:15]))
    built = pages.build_site(opts.site_dir, cfg, names, latest, changed_keys, jumps, now)
    result.info.append(f"Pages built: {built['pages']}")
    for district in built["unknown_districts"]:
        result.warnings.append(f"Unknown district {district!r}: shown on the 'other' page; add it to config.yaml")

    tracked_empty = 0
    for mandi in cfg.get("mandis") or []:
        markets = {norm(m) for m in mandi["market"]}
        if not any(series[1] in markets for series in latest):
            tracked_empty += 1
            # Info, not a warning: a mandi can be quiet for weeks, and a warning
            # that fires on every run teaches everyone to ignore warnings.
            result.info.append(f"Tracked mandi {mandi['key']} has no prices in the last "
                               f"{cfg['fetch']['keep_days']} days")

    changed_districts = {p.series[0] for p in merged.values() if p.key in changed_keys}
    first = is_first_run_window(now, cfg["schedule"]["first_run_ist"]) and \
        snap.last_post_date != today.isoformat()
    want_post = bool(changed_keys) or first or opts.force_post
    if want_post:
        result.post = post.build_post(cfg, names, latest, changed_keys, len(changed_keys),
                                      len(changed_districts), now, clean_note(opts.note), jumps)
    result.summary.append(
        f"{len(new)} new and {len(changed)} corrected prices from {len(changed_districts)} districts."
        if changed_keys else ("First run: baseline saved." if not snap.exists
                              else "Nothing new since the last run."))
    result.stats.update(new=len(new), corrected=len(changed), jumps=len(jumps & {
        p.series for p in merged.values() if p.key in changed_keys}),
        tracked_empty=tracked_empty, post="yes" if want_post else "no")
    if want_post and not changed_keys:
        result.summary.append("Posting anyway: " + ("first run of the day." if first else "the admin asked for a post."))

    if opts.save_state:
        last_post = today.isoformat() if want_post else snap.last_post_date
        state.save(opts.state_path, merged, last_post, now)
    if result.status != "FAIL" and result.warnings:
        result.status = "WARN"
    return result
