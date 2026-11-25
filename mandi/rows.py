"""Turn raw API records into checked prices, and say why any were rejected.

Nothing from the API is trusted: every text field is length-limited and
stripped of control characters, every price must be a positive number under
the configured cap with min <= modal <= max, and every date must be real,
recent and not in the future.
"""
from __future__ import annotations

import datetime as dt
import math
import unicodedata
from dataclasses import dataclass

from mandi.api import norm


@dataclass(frozen=True)
class Price:
    district: str
    market: str
    commodity: str
    variety: str
    grade: str
    date: dt.date
    min: int
    modal: int
    max: int

    @property
    def series(self) -> tuple:
        """One price line over time: the same market, crop, variety and grade."""
        return (norm(self.district), norm(self.market), norm(self.commodity),
                norm(self.variety), norm(self.grade))

    @property
    def key(self) -> tuple:
        return self.series + (self.date.isoformat(),)

    @property
    def numbers(self) -> tuple[int, int, int]:
        return (self.min, self.modal, self.max)

    def to_list(self) -> list:
        return [self.district, self.market, self.commodity, self.variety, self.grade,
                self.date.isoformat(), self.min, self.modal, self.max]

    @classmethod
    def from_list(cls, item: list) -> "Price":
        d, m, c, v, g, date, lo, mid, hi = item
        return cls(d, m, c, v, g, dt.date.fromisoformat(date), int(lo), int(mid), int(hi))


def clean_text(value, max_len: int) -> str | None:
    """Collapse whitespace; refuse control or format characters and long text."""
    text = " ".join(str(value if value is not None else "").split())
    if len(text) > max_len:
        return None
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        return None
    return text


def to_price(value, cap: float) -> int | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0 or number > cap:
        return None
    return int(round(number))


def validate(records: list[dict], cfg: dict, today: dt.date) -> tuple[list[Price], list[str]]:
    """Checked prices, plus one line per rejected record saying why."""
    checks = cfg["checks"]
    max_len, cap = int(checks["max_text"]), float(checks["max_price"])
    # keep_days counts today: 2 means today and yesterday only.
    oldest = today - dt.timedelta(days=int(cfg["fetch"]["keep_days"]) - 1)
    fmt = cfg["api"]["date_format"]
    state = norm(cfg.get("state", "Maharashtra"))

    good: dict[tuple, Price] = {}
    rejects: list[str] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            rejects.append(f"record {i}: not an object")
            continue
        label = f"{str(rec.get('market'))[:40]!r} / {str(rec.get('commodity'))[:40]!r}"
        texts = {k: clean_text(rec.get(k), max_len)
                 for k in ("state", "district", "market", "commodity", "variety", "grade")}
        bad = [k for k, v in texts.items() if v is None]
        if bad:
            rejects.append(f"{label}: unusable text in {', '.join(bad)}")
            continue
        missing = [k for k in ("district", "market", "commodity") if not texts[k]]
        if missing:
            rejects.append(f"{label}: missing {', '.join(missing)}")
            continue
        if norm(texts["state"]) != state:
            rejects.append(f"{label}: state is {texts['state']!r}, not {cfg.get('state')}")
            continue
        try:
            day = dt.datetime.strptime(str(rec.get("arrival_date", "")).strip(), fmt).date()
        except ValueError:
            rejects.append(f"{label}: bad date {str(rec.get('arrival_date'))[:20]!r}")
            continue
        if day > today:
            rejects.append(f"{label}: date {day} is in the future")
            continue
        if day < oldest:
            rejects.append(f"{label}: date {day} is older than the {cfg['fetch']['keep_days']}-day window")
            continue
        lo, mid, hi = (to_price(rec.get(k), cap) for k in ("min_price", "modal_price", "max_price"))
        if None in (lo, mid, hi):
            rejects.append(f"{label}: a price is missing, zero, not a number or above the cap")
            continue
        if not lo <= mid <= hi:
            rejects.append(f"{label}: modal {mid} is outside min {lo} to max {hi}")
            continue
        price = Price(texts["district"], texts["market"], texts["commodity"],
                      texts["variety"], texts["grade"], day, lo, mid, hi)
        earlier = good.get(price.key)
        if earlier and earlier.numbers != price.numbers:
            rejects.append(f"{label}: listed twice for {day} with different prices; kept the later one")
        good[price.key] = price
    return list(good.values()), rejects
