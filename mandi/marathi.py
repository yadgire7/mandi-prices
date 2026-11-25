"""Devanagari numbers, dates and the Marathi names from config.

Numbers are converted by code, not translated, so they are always exact.
"""
from __future__ import annotations

import datetime as dt
import re

from mandi.api import norm

# "APMC Akole", "Pune(Moshi)", "APMC Kalamb (Dharashiv)", "Dahanu APMC"
MARKET_PATTERN = re.compile(
    r"^(?P<apmc>APMC\s+)?(?P<place>[^()]+?)\s*(?:\((?P<sub>[^()]+)\))?(?P<apmc_end>\s+APMC)?$",
    re.IGNORECASE)

DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
MONTHS = ("जाने", "फेब्रु", "मार्च", "एप्रि", "मे", "जून", "जुलै", "ऑग", "सप्टें",
          "ऑक्टो", "नोव्हें", "डिसें")


def digits(value) -> str:
    return str(value).translate(DIGITS)


def group_indian(n: int) -> str:
    """1234567 -> '12,34,567' (lakh and crore grouping)."""
    text = str(abs(int(n)))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        text = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + text


def rupees(n: int) -> str:
    return "₹" + digits(group_indian(n))


def number(n: int) -> str:
    return digits(group_indian(n))


def short_date(d: dt.date) -> str:
    return f"{digits(d.day)} {MONTHS[d.month - 1]}"


def day_part(moment: dt.datetime, parts: list[dict]) -> tuple[dt.date, str]:
    """The date and part-of-day word for a run, e.g. (25 Nov, "सकाळ").

    A run gets the label of the last part that has started. If the first part
    starts after midnight, earlier runs belong to the previous day's last part.
    """
    started = [p for p in parts if p["from"] <= moment.hour]
    if started:
        return moment.date(), started[-1]["mr"]
    return moment.date() - dt.timedelta(days=1), parts[-1]["mr"]


def clock(moment: dt.datetime) -> str:
    """12-hour time in Devanagari digits: 00:00 is "१२:००", 18:30 is "६:३०"."""
    return digits(f"{moment.hour % 12 or 12}:{moment.minute:02d}")


def when(moment: dt.datetime, parts: list[dict] | None = None) -> str:
    """E.g. "२५ नोव्हें २०२६, दुपार १२:००"; without parts, a 24-hour time."""
    if parts:
        day, label = day_part(moment, parts)
        return f"{digits(day.day)} {MONTHS[day.month - 1]} {digits(day.year)}, {label} {clock(moment)}"
    return (f"{digits(moment.day)} {MONTHS[moment.month - 1]} {digits(moment.year)}, "
            f"{digits(moment.strftime('%H:%M'))}")


def change(delta: int | None) -> str:
    if delta is None:
        return ""
    if delta > 0:
        return "🟢▲" + rupees(delta)
    if delta < 0:
        return "🔴▼" + rupees(-delta)
    return "±०"


class Names:
    """Marathi names from config, falling back to the source's own spelling."""

    def __init__(self, cfg: dict):
        mr = cfg.get("names_mr") or {}
        self._crops = dict(mr.get("crops") or {})
        produce = cfg.get("produce_names_mr") or {}
        self._tables = {
            kind: {norm(k): v for k, v in {**(mr.get(kind) or {}), **(produce.get(kind) or {})}.items()}
            for kind in ("commodities", "varieties")
        }
        markets = cfg.get("market_names_mr") or {}
        self._places = {norm(k): v for k, v in (markets.get("places") or {}).items()}
        self._full = {norm(k): v for k, v in (markets.get("full") or {}).items()}
        self._apmc = markets.get("apmc_suffix") or ""
        self._hidden = {norm(v) for v in cfg.get("hide_varieties") or []}
        glossary = cfg.get("glossary_mr") or {}
        self._terms = {norm(k): (k, v) for k, v in (glossary.get("terms") or {}).items()}

    def _get(self, kind: str, value: str) -> str:
        return self._tables[kind].get(norm(value)) or " ".join(str(value).split())

    def crop(self, key: str) -> str:
        return self._crops.get(key, key)

    def commodity(self, value: str) -> str:
        return self._get("commodities", value)

    def variety(self, value: str) -> str:
        return self._get("varieties", value) if value else ""

    def kind_values(self, variety: str, grade: str) -> list[str]:
        """The variety and grade worth showing: "Other" and blanks are left out."""
        return [v for v in (variety, grade) if v and norm(v) not in self._hidden]

    def has_name(self, kind: str, value: str) -> bool:
        """Whether a commodity or variety has a Marathi name in the glossary."""
        return norm(value) in self._tables[kind]

    def market_devanagari(self, value: str) -> str | None:
        """The market's Devanagari name, or None if no name is known for it."""
        name = " ".join(str(value).split())
        if norm(name) in self._full:
            return self._full[norm(name)]
        match = MARKET_PATTERN.match(name)
        if not match:
            return None
        place = self._places.get(norm(match["place"]))
        sub = self._places.get(norm(match["sub"])) if match["sub"] else ""
        if not place or sub is None:
            return None
        text = place + (f" ({sub})" if sub else "")
        if (match["apmc"] or match["apmc_end"]) and self._apmc:
            text += " " + self._apmc
        return text

    def market(self, value: str) -> str:
        return self.market_devanagari(value) or " ".join(str(value).split())

    def term(self, value: str) -> tuple[str, dict] | None:
        """A glossary entry for a grade or variety like FAQ, as (term, entry)."""
        return self._terms.get(norm(value))

    def terms(self) -> list[tuple[str, dict]]:
        return list(self._terms.values())
