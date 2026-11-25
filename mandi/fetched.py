"""Raw prices fetched on one machine and processed on another.

data.gov.in refuses connections from GitHub's servers (they are outside
India), so a machine in India fetches the raw records and pushes them to the
`data` branch as one gzipped JSON file. The GitHub workflow reads that file
instead of calling the API. If the fetch failed, the file carries the error,
so the run email can still say why.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mandi.api import ApiError, canonical, parse_date

VERSION = 1
MAX_RECORDS = 50_000   # a full Maharashtra day is about 1,600; far more means something is wrong
MAX_BYTES = 20_000_000


class StaleDataError(ApiError):
    """The fetched data is too old to use: the fetching machine missed its runs."""

    def __init__(self, message: str, reason: str):
        super().__init__(message, None)
        self.reason = reason


@dataclass
class Fetched:
    fetched_at: dt.datetime
    records: list[dict]
    error: str | None = None
    status: int | None = None


def write(path: Path, fetched_at: dt.datetime, records: list[dict] | None = None,
          error: ApiError | None = None) -> None:
    data = {"version": VERSION, "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "records": records or [],
            "error": None if error is None else {"message": str(error)[:2000],
                                                 "status": getattr(error, "status", None)}}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".fetched-", suffix=".gz")
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read(path: Path) -> Fetched:
    """Load and sanity-check the file. The records themselves are checked later, like API rows."""
    with gzip.open(path, "rb") as fh:
        blob = fh.read(MAX_BYTES + 1)
    if len(blob) > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_BYTES} bytes unpacked")
    data = json.loads(blob)
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(f"{path.name} is not a version {VERSION} fetched-data file")
    fetched_at = dt.datetime.fromisoformat(str(data["fetched_at"]))
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at has no time zone")
    records = data.get("records") or []
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError(f"records must be a list of at most {MAX_RECORDS}")
    error = data.get("error")
    if error is not None and not isinstance(error, dict):
        raise ValueError("error must be an object or null")
    status = (error or {}).get("status")
    return Fetched(fetched_at, records,
                   None if error is None else str(error.get("message", ""))[:2000],
                   status if isinstance(status, int) else None)


def missing_source(path: Path):
    """A source for when nothing has been pushed yet: fails with a plain reason."""
    def load(today: dt.date) -> list[dict]:
        raise StaleDataError(f"{path} does not exist",
                             "NO DATA: nothing has been fetched and pushed to the `data` branch yet. "
                             "On the Mac, run scripts/fetcher.sh run (and scripts/fetcher.sh install "
                             "for the schedule).")
    return load


def age_hours(fetched: Fetched, now: dt.datetime) -> float:
    return (now - fetched.fetched_at).total_seconds() / 3600


def source(fetched: Fetched, cfg: dict, now: dt.datetime):
    """A pipeline source over the fetched file: raises like the API would."""
    limits = cfg.get("fetcher") or {}
    fail_after = float(limits.get("fail_after_hours", 12))

    def load(today: dt.date) -> list[dict]:
        age = age_hours(fetched, now)
        if age > fail_after:
            when = fetched.fetched_at.astimezone(now.tzinfo).strftime("%d %b %H:%M")
            raise StaleDataError(
                f"newest fetched data is from {when} ({age:.0f} hours old)",
                f"NO FRESH DATA: the fetching machine last sent prices at {when}, "
                f"{age:.0f} hours ago. Is the Mac awake and online? Check with "
                "scripts/fetcher.sh status, or run scripts/fetcher.sh run.")
        if age < -0.25:
            raise ApiError(f"fetched_at {fetched.fetched_at.isoformat()} is in the future", None)
        if fetched.error is not None:
            raise ApiError(f"(reported by the fetching machine) {fetched.error}", fetched.status)
        window = {today - dt.timedelta(days=i) for i in range(int(cfg["fetch"]["days"]))}
        rows = canonical([r for r in fetched.records if isinstance(r, dict)])
        return [r for r in rows if parse_date(r.get("arrival_date")) in window]

    return load
