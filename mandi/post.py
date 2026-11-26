"""The short Marathi WhatsApp post: what changed, the headline crops, the links."""
from __future__ import annotations

import datetime as dt

from mandi import marathi
from mandi.api import norm
from mandi.rows import Price


def build_post(cfg: dict, names: marathi.Names, latest: dict[tuple, tuple[Price, Price | None]],
               changed: set, change_count: int, district_count: int, now: dt.datetime,
               note: str = "", jumps: set = frozenset(), weather: list[str] = ()) -> str:
    text = cfg["text_mr"]
    lines = [text["post_header"].format(when=marathi.when(now, cfg.get("day_parts_mr")))]
    used_terms: dict[str, dict] = {}
    if note:
        lines += ["", note]
    lines.append("")
    if change_count:
        lines.append(text["post_summary"].format(count=marathi.number(change_count),
                                                 districts=marathi.number(district_count)))
    else:
        lines.append(text["post_no_change"])

    tracked = [(mandi, {norm(m) for m in mandi["market"]}) for mandi in cfg.get("mandis") or []]
    for crop in cfg.get("headline_crops") or []:
        wanted = {norm(c) for c in cfg["crops"][crop]}
        crop_lines = []
        for mandi, markets in tracked:
            items = [(p, prev) for series, (p, prev) in latest.items()
                     if series[1] in markets and series[2] in wanted]
            items.sort(key=lambda item: (item[0].variety, item[0].grade))
            for price, prev in items:
                kind_parts = []
                for value in names.kind_values(price.variety, price.grade):
                    entry = names.term(value)
                    if entry and names.variety(value) == " ".join(value.split()):
                        used_terms[entry[0]] = entry[1]      # e.g. FAQ, explained below
                    kind_parts.append(names.variety(value))
                kind = "/".join(kind_parts)
                place = mandi.get("mr") or names.market(price.market)
                delta = price.modal - prev.modal if prev else None
                parts = [f"• {place}" + (f" ({kind})" if kind else "") + ":",
                         marathi.rupees(price.modal),
                         f"({marathi.rupees(price.min)}–{marathi.rupees(price.max)})",
                         marathi.change(delta),
                         "· " + marathi.short_date(price.date),
                         "🆕" if price.key in changed else "",
                         "⚠" if price.series in jumps else ""]
                crop_lines.append(" ".join(p for p in parts if p))
        lines += ["", f"*{names.crop(crop)}*"]
        lines += crop_lines or [f"• {text['post_crop_none']}"]

    if weather:
        lines += ["", *weather]
    lines.append("")
    base = str((cfg.get("site") or {}).get("base_url") or "").rstrip("/")
    if base:
        lines.append(text["post_links"].format(url=base + "/"))
    lines.append(text["post_footer"])
    for term, info in used_terms.items():
        lines.append(text["post_glossary"].format(term=term, name=info["name"]))
    if text.get("post_channel"):
        lines.append(text["post_channel"])
    return "\n".join(lines).strip() + "\n"
