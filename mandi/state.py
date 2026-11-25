"""The snapshot each run leaves for the next: what changed, and price history.

The file is plain JSON with one price per line, so each run's commit shows a
small, readable diff.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from mandi.rows import Price

VERSION = 1


@dataclass
class Snapshot:
    prices: dict[tuple, Price] = field(default_factory=dict)
    last_post_date: str | None = None
    exists: bool = False


def load(path: Path) -> Snapshot:
    if not path.exists():
        return Snapshot()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(f"{path} is not a version {VERSION} snapshot")
    prices = {}
    for item in data.get("prices") or []:
        price = Price.from_list(item)
        prices[price.key] = price
    return Snapshot(prices, data.get("last_post_date"), True)


def save(path: Path, prices: dict[tuple, Price], last_post_date: str | None,
         updated_at: dt.datetime) -> None:
    ordered = sorted(prices.values(), key=lambda p: p.key)
    lines = ",\n".join("    " + json.dumps(p.to_list(), ensure_ascii=False) for p in ordered)
    text = ("{\n"
            f'  "version": {VERSION},\n'
            f'  "updated_at": {json.dumps(updated_at.isoformat(timespec="seconds"))},\n'
            f'  "last_post_date": {json.dumps(last_post_date)},\n'
            '  "prices": [\n' + lines + ("\n" if lines else "") + "  ]\n}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".snapshot-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)  # public price data; mkstemp creates files as 0600
    os.replace(tmp, path)  # atomic: a crash never leaves half a snapshot


def compare(previous: dict[tuple, Price], fetched: list[Price]) -> tuple[list[Price], list[tuple[Price, Price]]]:
    """Prices not seen before, and prices whose numbers were corrected."""
    new, changed = [], []
    for price in fetched:
        old = previous.get(price.key)
        if old is None:
            new.append(price)
        elif old.numbers != price.numbers:
            changed.append((old, price))
    return new, changed


def merge(previous: dict[tuple, Price], fetched: list[Price], today: dt.date,
          keep_days: int) -> dict[tuple, Price]:
    oldest = today - dt.timedelta(days=keep_days - 1)  # keep_days counts today
    merged = {k: p for k, p in previous.items() if p.date >= oldest}
    for price in fetched:
        merged[price.key] = price
    return merged


def latest_by_series(prices: dict[tuple, Price]) -> dict[tuple, tuple[Price, Price | None]]:
    """For each series, its newest price and the one before it."""
    by_series: dict[tuple, list[Price]] = {}
    for price in prices.values():
        by_series.setdefault(price.series, []).append(price)
    out = {}
    for series, items in by_series.items():
        items.sort(key=lambda p: p.date, reverse=True)
        out[series] = (items[0], items[1] if len(items) > 1 else None)
    return out


def is_jump(latest: Price, previous: Price | None, factor: float) -> bool:
    if previous is None:
        return False
    return latest.modal >= factor * previous.modal or latest.modal * factor <= previous.modal
