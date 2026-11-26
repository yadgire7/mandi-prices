"""Weather alerts for the WhatsApp post, read from the weather site.

The weather site (yadgire7/havaman) publishes alerts.json every 4 hours:
district slug -> alert kind -> taluka slugs, for the next 24 hours. This
module downloads it, checks it, and turns it into counts per district.

Nothing from the file is copied into the post: only the counts, our own
district names from config.yaml and our own words. A broken, tampered,
missing or old file means no weather block, never a failed price run.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

from mandi import marathi
from mandi.api import urllib_fetch

KINDS = ("storm", "heavy_rain", "rain", "heat", "wind")   # most serious first
MAX_BYTES = 1_000_000
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class Alerts:
    generated_at: dt.datetime
    counts: dict[str, dict[str, int]] = field(default_factory=dict)  # kind -> district -> talukas
    ignored: list[str] = field(default_factory=list)                   # district slugs we do not know


def parse(body: bytes, district_slugs: set[str]) -> Alerts:
    """Check alerts.json strictly; raise ValueError on anything unexpected."""
    if len(body) > MAX_BYTES:
        raise ValueError("alerts.json is too large")
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("alerts.json is not version 1")
    generated = dt.datetime.fromisoformat(str(data.get("generated_at")))
    if generated.tzinfo is None:
        raise ValueError("generated_at has no time zone")
    districts = data.get("districts")
    if not isinstance(districts, dict):
        raise ValueError("districts must be an object")
    out = Alerts(generated, {k: {} for k in KINDS})
    for slug, kinds in districts.items():
        if not isinstance(slug, str) or not SLUG.match(slug) or not isinstance(kinds, dict):
            raise ValueError("a district entry is malformed")
        if slug not in district_slugs:
            out.ignored.append(slug[:40])
            continue
        for kind, talukas in kinds.items():
            if kind not in KINDS:
                continue                       # a newer alert kind we do not show yet
            if not isinstance(talukas, list) or not all(isinstance(t, str) for t in talukas):
                raise ValueError(f"{slug}.{kind} must be a list of taluka names")
            if talukas:
                out.counts[kind][slug] = min(len(set(talukas)), 99)
    return out


def load(cfg: dict, now: dt.datetime, fetch=urllib_fetch) -> tuple[Alerts | None, str]:
    """(alerts, note for the email). alerts is None when they must not be used."""
    settings = cfg.get("weather_alerts") or {}
    url = settings.get("url")
    if not url:
        return None, "Weather alerts are switched off (weather_alerts.url is empty)."
    slugs = {d["slug"] for d in cfg.get("districts") or []}
    try:
        status, body = fetch(url)
        if status != 200:
            raise ValueError(f"HTTP {status}")
        alerts = parse(body, slugs)
    except (OSError, ValueError) as err:
        return None, f"Weather alerts left out of the post: could not read {url} ({type(err).__name__}: {str(err)[:150]})."
    age = (now - alerts.generated_at).total_seconds() / 3600
    if age > float(settings.get("max_age_hours", 6)) or age < -0.5:
        return None, (f"Weather alerts left out of the post: they are from "
                      f"{alerts.generated_at:%d %b %H:%M}, {age:.1f} hours old.")
    note = f"Weather alerts from {alerts.generated_at:%H:%M} IST: " + (
        ", ".join(f"{k} in {len(v)} districts" for k, v in alerts.counts.items() if v) or "none")
    if alerts.ignored:
        note += f"; unknown districts ignored: {', '.join(alerts.ignored[:5])}"
    return alerts, note


def post_lines(alerts: Alerts | None, cfg: dict) -> list[str]:
    """The weather block for the post: a title, one line per alert kind, the link."""
    if alerts is None:
        return []
    text = cfg["text_mr"]
    settings = cfg.get("weather_alerts") or {}
    names = {d["slug"]: d["mr"] for d in cfg.get("districts") or []}
    limit = int(settings.get("max_districts", 8))
    lines = [f"*{text['post_weather_title']}*"]
    for kind in KINDS:
        per = alerts.counts.get(kind) or {}
        if not per:
            continue
        ranked = sorted(per.items(), key=lambda kv: (-kv[1], names[kv[0]]))
        shown = [f"{names[slug]} ({marathi.number(n)})" for slug, n in ranked[:limit]]
        if len(ranked) > limit:
            shown.append(text["post_weather_more"].format(n=marathi.number(len(ranked) - limit)))
        lines.append(f"{text['post_weather_' + kind]}: {', '.join(shown)}")
    if len(lines) == 1:
        lines.append(text["post_weather_none"])
    if settings.get("page_url"):
        lines.append(text["post_weather_link"].format(url=settings["page_url"]))
    return lines
