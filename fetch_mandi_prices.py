#!/usr/bin/env python3
"""Run the Shetkari Shala mandi price pipeline once.

    python fetch_mandi_prices.py                    # a real run: API, pages, email
    python fetch_mandi_prices.py --now --note "..."  # the admin's run: always posts
    python fetch_mandi_prices.py --dry-run --from-file rows.csv --at 2026-11-25T07:00
    python fetch_mandi_prices.py --shadow           # a trial run: real, but marked "do not post"
    python fetch_mandi_prices.py --test-email       # checks the mail settings, nothing else
    python fetch_mandi_prices.py --fetch-only out/fetched.json.gz   # the laptop: fetch, save, stop
    python fetch_mandi_prices.py --from-fetched fetched.json.gz     # GitHub: process pushed data

A dry run reads saved data instead of the API, keeps its own snapshot under
out/dry-run/, and saves the email to a file instead of sending it.

Every run ends with one email (or, in a dry run, one saved email), even if
something crashes on the way, and adds one line to runs.tsv in the out folder.
Exit code 1 means the run failed or the email could not be sent.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

from mandi import fetched, notify, pipeline
from mandi.api import ApiError, Client, canonical, load_api_key, parse_date
from mandi.config import load_config

HERE = Path(__file__).resolve().parent
LOG_COLUMNS = ("run_ist", "status", "fetched", "accepted", "rejected", "today", "yesterday",
               "districts_today", "tracked_empty", "new", "corrected", "jumps", "post", "email")


def read_dotenv(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() and not name.strip().startswith("#"):
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def file_source(path: Path, cfg: dict):
    """Saved rows (probe CSV or API JSON), limited to the days a real run would fetch."""
    def load(today: dt.date) -> list[dict]:
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8", newline="") as fh:
                raw = list(csv.DictReader(fh))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("records", []) if isinstance(data, dict) else data
        window = {today - dt.timedelta(days=i) for i in range(int(cfg["fetch"]["days"]))}
        return [r for r in canonical(raw) if parse_date(r.get("arrival_date")) in window]
    return load


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--config", type=Path, default=HERE / "config.yaml")
    p.add_argument("--dry-run", action="store_true", help="no API calls and no email")
    p.add_argument("--from-file", type=Path, help="read saved rows instead of the API")
    p.add_argument("--now", action="store_true", help="always produce a post (the admin's run)")
    p.add_argument("--note", default="", help="the admin's message, placed above the prices")
    p.add_argument("--at", help="pretend the run happens at this IST time, e.g. 2026-11-25T07:00")
    p.add_argument("--no-email", action="store_true", help="save the email instead of sending it")
    p.add_argument("--shadow", action="store_true",
                   help="a trial run: the email is marked SHADOW and says not to post")
    p.add_argument("--fetch-only", type=Path, metavar="FILE",
                   help="only fetch from data.gov.in and save the raw data (or the error) to FILE")
    p.add_argument("--from-fetched", type=Path, metavar="FILE",
                   help="a real run from data another machine fetched, with checks on its age")
    p.add_argument("--test-email", action="store_true",
                   help="send one test email to check the mail settings; no API call, no pages")
    p.add_argument("--site", type=Path, help="where to build the pages")
    p.add_argument("--state", type=Path, help="the snapshot file")
    p.add_argument("--out", type=Path, default=HERE / "out", help="emails and posts are saved here")
    args = p.parse_args(argv)
    if args.from_file and args.from_fetched:
        p.error("use --from-file or --from-fetched, not both")
    if args.dry_run and not args.from_file:
        p.error("--dry-run needs --from-file, so that it makes no API calls")
    base = args.out / "dry-run" if args.dry_run else HERE
    args.site = args.site or base / "site"
    args.state = args.state or base / "state" / "prices.json"
    return args


def append_log(path: Path, row: dict) -> None:
    """One tab-separated line per run, for reading coverage over the shadow week."""
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("\t".join(LOG_COLUMNS) + "\n")
        fh.write("\t".join(str(row.get(c, "")) for c in LOG_COLUMNS) + "\n")


def send_test_email(env: dict) -> int:
    now = dt.datetime.now(pipeline.IST)
    report = notify.Report(status="TEST", when=now.strftime("%d %b %Y %H:%M IST"), summary=[
        "A test email from the mandi price pipeline. If you can read this, the mail settings work.",
        "No prices were fetched and nothing was posted."])
    try:
        notify.send(report, env)
    except Exception as err:
        print(notify.scrub(f"Test email NOT sent: {type(err).__name__}: {err}", env), file=sys.stderr)
        return 1
    print(f"Test email sent to {env.get('MAIL_TO')}.")
    return 0


def fetch_only(args, env: dict) -> int:
    """Fetch today's window and save it for another machine to process. No pages, no email."""
    now = dt.datetime.fromisoformat(args.at).replace(tzinfo=pipeline.IST) if args.at \
        else dt.datetime.now(pipeline.IST)
    cfg = load_config(args.config)
    key, _ = load_api_key(env)
    if not key:
        print("no API key: set DATA_GOV_IN_API_KEY or add it to .env", file=sys.stderr)
        return 1
    try:
        records = pipeline.api_source(Client(key), cfg)(now.date())
    except ApiError as err:
        fetched.write(args.fetch_only, now, error=err)
        print(notify.scrub(f"Fetch FAILED, error saved for the run email: {err}", env), file=sys.stderr)
        return 1
    fetched.write(args.fetch_only, now, records)
    print(f"Fetched {len(records)} records at {now:%Y-%m-%d %H:%M} IST into {args.fetch_only}")
    return 0


def main(argv=None, environ=os.environ) -> int:
    args = parse_args(argv)
    env = {**read_dotenv(HERE / ".env"), **environ}
    if args.test_email:
        return send_test_email(env)
    if args.fetch_only:
        return fetch_only(args, env)
    if args.at:
        now = dt.datetime.fromisoformat(args.at).replace(tzinfo=pipeline.IST)
    else:
        now = dt.datetime.now(pipeline.IST)
    report = notify.Report(status="FAIL", when=now.strftime("%d %b %Y %H:%M IST"),
                           log_url=notify.log_url(env), tag="SHADOW" if args.shadow else "")
    stats: dict = {}
    try:
        cfg = load_config(args.config)
        if args.from_file:
            source = file_source(args.from_file, cfg)
        elif args.from_fetched and not args.from_fetched.exists():
            source = fetched.missing_source(args.from_fetched)
        elif args.from_fetched:
            data = fetched.read(args.from_fetched)
            age = fetched.age_hours(data, now)
            if (cfg["fetcher"]["warn_after_hours"] < age <= cfg["fetcher"]["fail_after_hours"]
                    and data.error is None):
                report.warnings.append(
                    f"The fetching machine missed its last run: these prices were fetched "
                    f"{age:.1f} hours ago ({data.fetched_at.astimezone(pipeline.IST):%d %b %H:%M} IST). "
                    "Is the Mac awake and online?")
            source = fetched.source(data, cfg, now)
        else:
            key, notes = load_api_key(env)
            for note in notes:
                report.warnings.append(note)
            if not key:
                raise RuntimeError("no API key: set DATA_GOV_IN_API_KEY or add it to .env")
            source = pipeline.api_source(Client(key), cfg)
        result = pipeline.run(cfg, source, pipeline.Options(
            now=now, site_dir=args.site, state_path=args.state,
            force_post=args.now, note=args.note))
        report.status = "WARN" if result.status == "OK" and report.warnings else result.status
        report.post, report.summary = result.post, result.summary
        report.warnings += result.warnings
        report.info = result.info
        stats = result.stats
    except Exception as err:  # the email must still go out
        report.status = "FAIL"
        report.summary = ["The run stopped before finishing. Nothing was posted."]
        report.warnings.append(notify.scrub(f"{type(err).__name__}: {err}", env))
        print(notify.scrub(traceback.format_exc(), env), file=sys.stderr)

    if args.shadow:
        report.summary.insert(0, "SHADOW RUN (trial week): check this email, but do not post it to the channel.")
    stamp = now.strftime("%Y%m%d-%H%M")
    saved = notify.save(report, args.out / f"email-{stamp}.eml", env)
    if report.post:
        (args.out / f"post-{stamp}.txt").write_text(report.post, encoding="utf-8")
    print(notify.scrub(notify.subject(report), env))
    print(notify.scrub(notify.body(report), env))

    email, code = "saved", 1 if report.status == "FAIL" else 0
    if args.dry_run or args.no_email:
        print(f"Email not sent; saved to {saved}")
    else:
        try:
            notify.send(report, env)
            email = "sent"
            print("Email sent.")
        except notify.NotConfigured as err:
            email, code = "not configured", 1
            print(f"Email NOT sent: {err}. Saved to {saved}", file=sys.stderr)
        except Exception as err:
            email, code = "send failed", 1
            print(notify.scrub(f"Email NOT sent: {type(err).__name__}: {err}", env), file=sys.stderr)
    log_dir = args.out / "dry-run" if args.dry_run else args.out
    append_log(log_dir / "runs.tsv", {**stats, "run_ist": now.strftime("%Y-%m-%d %H:%M"),
                                      "status": report.status, "email": email})
    return code


if __name__ == "__main__":
    sys.exit(main())
