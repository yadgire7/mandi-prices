"""Load config.yaml and refuse to run on a config that would misbehave."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from mandi.api import norm

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TEXT_KEYS = (
    "site_title", "district_title", "other_title", "updated", "unit_note", "caution",
    "no_prices", "whats_new", "col_crop", "col_variety", "col_date", "col_min",
    "col_modal", "col_max", "col_change", "new_mark", "jump_mark", "all_districts", "index_counts",
    "glossary_title", "post_glossary", "tap_hint", "close",
    "post_header", "post_summary", "post_no_change", "post_crop_none", "post_links",
    "post_footer", "post_channel",
)
RESERVED_SLUGS = {"other", "style"}


class ConfigError(ValueError):
    pass


def load_config(path: Path | str) -> dict:
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    for setting, target in (("market_names_file", "market_names_mr"),
                            ("produce_names_file", "produce_names_mr")):
        if isinstance(cfg, dict) and cfg.get(setting):
            names_path = (path.parent / cfg[setting]).resolve()
            if names_path.parent != path.parent.resolve():
                raise ConfigError(f"{setting} must sit next to config.yaml")
            cfg[target] = yaml.safe_load(names_path.read_text(encoding="utf-8")) or {}
    validate_config(cfg)
    return cfg


def validate_config(cfg) -> None:
    if not isinstance(cfg, dict):
        raise ConfigError("config must be a mapping")
    problems: list[str] = []
    crops = cfg.get("crops") or {}

    for crop in cfg.get("headline_crops") or []:
        if crop not in crops:
            problems.append(f"headline crop {crop!r} is not defined under crops:")
    for mandi in cfg.get("mandis") or []:
        for crop in mandi.get("crops") or []:
            if crop not in crops:
                problems.append(f"mandi {mandi.get('key')!r} uses undefined crop {crop!r}")

    sched = cfg.get("schedule") or {}
    every = sched.get("every_hours")
    if not isinstance(every, int) or every < 1 or 24 % every:
        problems.append("schedule.every_hours must be a whole number that divides 24")
    if not HHMM.match(str(sched.get("first_run_ist", ""))):
        problems.append("schedule.first_run_ist must look like 07:00")

    api = cfg.get("api") or {}
    for key in ("resource", "state_filter", "date_filter", "date_format"):
        if not api.get(key):
            problems.append(f"api.{key} is missing")

    fetch = cfg.get("fetch") or {}
    days, keep = fetch.get("days"), fetch.get("keep_days")
    if not isinstance(days, int) or not 1 <= days <= 7:
        problems.append("fetch.days must be between 1 and 7")
    if not isinstance(keep, int) or not isinstance(days, int) or keep < days:
        problems.append("fetch.keep_days must be at least fetch.days")

    checks = cfg.get("checks") or {}
    for key in ("max_price", "jump_factor", "max_text"):
        if not isinstance(checks.get(key), (int, float)) or checks[key] <= 0:
            problems.append(f"checks.{key} must be a positive number")

    base = str((cfg.get("site") or {}).get("base_url") or "")
    if base and not re.match(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$", base):
        problems.append("site.base_url must be empty or a plain https:// address")

    slugs, names = set(), set()
    for d in cfg.get("districts") or []:
        slug = str(d.get("slug", ""))
        if not SLUG.match(slug) or slug in RESERVED_SLUGS:
            problems.append(f"district slug {slug!r} must be lower-case letters, digits and dashes")
        if slug in slugs:
            problems.append(f"district slug {slug!r} is used twice")
        slugs.add(slug)
        for name in [d.get("name")] + list(d.get("aliases") or []):
            if norm(name) in names:
                problems.append(f"district name {name!r} is listed twice")
            names.add(norm(name))
        if not d.get("mr"):
            problems.append(f"district {d.get('name')!r} has no Marathi name")
    if not slugs:
        problems.append("no districts are listed")

    parts = cfg.get("day_parts_mr") or []
    hours = [p.get("from") for p in parts if isinstance(p, dict)]
    if (not parts or len(hours) != len(parts) or not all(isinstance(h, int) and 0 <= h <= 23 for h in hours)
            or hours != sorted(set(hours)) or not all(p.get("mr") for p in parts)):
        problems.append("day_parts_mr must list parts with increasing 'from' hours (0-23) and a Marathi 'mr'")

    names = cfg.get("market_names_mr") or {}
    if names and not isinstance(names.get("places"), dict):
        problems.append("the market names file needs a 'places' mapping")
    produce = cfg.get("produce_names_mr") or {}
    if produce and not all(isinstance(produce.get(k), dict) for k in ("commodities", "varieties")):
        problems.append("the produce names file needs 'commodities' and 'varieties' mappings")

    text = cfg.get("text_mr") or {}
    for key in TEXT_KEYS:
        if key not in text:
            problems.append(f"text_mr.{key} is missing")

    if problems:
        raise ConfigError("; ".join(problems))
