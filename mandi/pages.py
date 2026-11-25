"""Static Marathi price pages: an index plus one page per district.

Security: plain HTML and one stylesheet, no JavaScript, no forms, no outside
resources, and a Content-Security-Policy that forbids them anyway. Every value
that came from the API or from config passes through `esc` on its way in.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import shutil
from pathlib import Path

from mandi import marathi
from mandi.api import norm
from mandi.rows import Price

MARKER = ".mandi-site"
CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; "
       "form-action 'none'")
STYLE = """\
:root { color-scheme: light dark; --ink: #1f2a24; --muted: #5b6b62; --line: #d7dcd8;
  --new: #fff4d6; --warn: #8a4b00; --bg: #fbfaf7; }
@media (prefers-color-scheme: dark) { :root { --ink: #e9ece9; --muted: #a8b3ad;
  --line: #3a423e; --new: #3b3320; --warn: #f0b45c; --bg: #151916; } }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.55 system-ui, "Noto Sans Devanagari", sans-serif;
  color: var(--ink); background: var(--bg); }
header, main, footer { max-width: 56rem; margin: 0 auto; padding: 0.75rem 1rem; }
h1 { font-size: 1.4rem; margin: 0.25rem 0; }
h2 { font-size: 1.1rem; margin: 1.25rem 0 0.25rem; }
header { padding-bottom: 0; }
a { color: inherit; }
.updated, .en { color: var(--muted); font-size: 0.9rem; }
.updated { margin-bottom: 0.25rem; }
.unit { font-weight: 600; margin: 0.25rem 0; }
.caution { color: var(--muted); font-size: 0.9rem; margin: 0 0 0.5rem; }
.en { margin: 0 0 0.5rem; }
.whats-new { background: var(--new); padding: 0.5rem 0.75rem; border-radius: 6px; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.95rem; }
th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.new td { background: var(--new); }
th:first-child, td:first-child { position: sticky; left: 0; background: var(--bg);
  box-shadow: 1px 0 0 var(--line); }
tr.new td:first-child { background: var(--new); }
td.kind { white-space: normal; min-width: 8rem; }
.mark { font-size: 0.8rem; font-weight: 600; margin-left: 0.4rem; }
.jump { display: block; color: var(--warn); font-size: 0.8rem; white-space: normal; }
td.crop[rowspan] { vertical-align: top; }
ul.districts { list-style: none; padding: 0; margin-top: 0.5rem; columns: 2; column-gap: 1rem; }
@media (min-width: 40rem) { ul.districts { columns: 3; } }
ul.districts li { padding: 0.25rem 0; break-inside: avoid; }
ul.districts a { display: inline-block; padding: 0.35rem 0; font-size: 1.05rem; }
ul.districts small { display: block; color: var(--muted); }
abbr[title] { text-decoration: underline dotted; cursor: help; }
button.term { font: inherit; color: inherit; background: none; border: 0; margin: 0;
  padding: 0.2rem 0; text-decoration: underline dotted; text-underline-offset: 3px;
  cursor: pointer; }
th button.term { font-weight: 600; }
[popover].tip { max-width: min(22rem, 90vw); padding: 0.9rem 1rem; border: 1px solid var(--line);
  border-radius: 10px; background: var(--bg); color: var(--ink); font-size: 1rem;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25); }
[popover].tip::backdrop { background: rgba(0, 0, 0, 0.2); }
[popover].tip p { margin: 0 0 0.6rem; }
[popover].tip button { font: inherit; padding: 0.4rem 0.9rem; min-height: 44px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--new); color: var(--ink); }
.key { background: var(--new); padding: 0.6rem 0.75rem; border-radius: 6px; font-size: 0.95rem; }
.key span { display: block; }
.key small { color: var(--muted); }
details.glossary { margin-top: 1.5rem; }
details.glossary summary { cursor: pointer; font-weight: 600; }
details.glossary dt { font-weight: 600; margin-top: 0.5rem; }
details.glossary dd { margin: 0 0 0 1rem; }
"""


# What a farmer looks for first stays on a phone screen: crop, modal price, change.
COLUMNS = ("col_crop", "col_modal", "col_change", "col_variety", "col_date", "col_min", "col_max")


def esc(value) -> str:
    return html.escape(str(value), quote=True)


class Districts:
    """Find a district's config entry from any spelling the data uses."""

    def __init__(self, cfg: dict):
        self.items = list(cfg["districts"])
        self._by_name = {}
        for d in self.items:
            for name in [d["name"]] + list(d.get("aliases") or []):
                self._by_name[norm(name)] = d

    def find(self, name: str) -> dict | None:
        return self._by_name.get(norm(name))


def tip_id(kind: str, key: str) -> str:
    """A safe HTML id for an explanation box, e.g. tip-term-non-faq."""
    return "tip-" + re.sub(r"[^a-z0-9]+", "-", f"{kind}-{key}".lower()).strip("-")


def _tip(display: str, tip: str | None, target: str | None = None) -> str:
    """A word that explains itself.

    A tap or a click opens the explanation box (the browser's built-in
    popover, no JavaScript). The text lives once per page, in the box, not
    on every button, which keeps pages small on mobile data.
    """
    if not tip:
        return esc(display)
    if target:
        return (f'<button type="button" class="term" popovertarget="{esc(target)}">'
                f"{esc(display)}</button>")
    return f'<abbr title="{esc(tip)}">{esc(display)}</abbr>'


def _kind(price: Price, names: marathi.Names) -> str:
    """Variety / grade, with a tooltip on terms like FAQ and Non-FAQ."""
    parts = []
    for value in names.kind_values(price.variety, price.grade):
        entry = names.term(value)
        if entry:
            term, info = entry
            shown = names.variety(value) if names.variety(value) != " ".join(value.split()) else term
            parts.append(_tip(shown, f"{info['name']}: {info['meaning']}", tip_id("term", term)))
        else:
            parts.append(esc(names.variety(value)))
    return " / ".join(parts)


def _glossary(cfg: dict, names: marathi.Names) -> str:
    """The same explanations as the tooltips, readable on a phone (tap to open)."""
    text, columns = cfg["text_mr"], (cfg.get("glossary_mr") or {}).get("columns") or {}
    items = [(text[key], help_text) for key, help_text in columns.items() if key in text]
    items += [(f"{term}: {info['name']}", info["meaning"]) for term, info in names.terms()]
    if not items:
        return ""
    body = "".join(f"<dt>{esc(t)}</dt><dd>{esc(d)}</dd>" for t, d in items)
    return (f'<details class="glossary"><summary>{esc(text["glossary_title"])}</summary>'
            f"<dl>{body}</dl></details>")


def _boxes(cfg: dict, names: marathi.Names) -> str:
    """The explanation boxes the tappable words open, one per term and heading."""
    text = cfg["text_mr"]
    columns = (cfg.get("glossary_mr") or {}).get("columns") or {}
    items = [(tip_id("col", key), text[key], help_text)
             for key, help_text in columns.items() if key in text]
    items += [(tip_id("term", term), f"{term}: {info['name']}", info["meaning"])
              for term, info in names.terms()]
    return "".join(
        f'<div id="{esc(box_id)}" class="tip" popover>'
        f"<p><strong>{esc(title)}</strong></p><p>{esc(body)}</p>"
        f'<button type="button" popovertarget="{esc(box_id)}" popovertargetaction="hide">'
        f'{esc(text["close"])}</button></div>'
        for box_id, title, body in items)


def _key(cfg: dict) -> str:
    """An always-visible key: works on every phone, with no tap needed."""
    entries = (cfg.get("glossary_mr") or {}).get("key") or []
    if not entries:
        return ""
    lines = "".join(f"<span>{esc(e)}</span>" for e in entries)
    return f'<p class="key">{lines}<small>{esc(cfg["text_mr"]["tap_hint"])}</small></p>'


def _page(title: str, root: str, updated: str, main: str, text: dict) -> str:
    nav = f'<a href="{esc(root)}">{esc(text["all_districts"])}</a>' if root else ""
    return f"""<!doctype html>
<html lang="mr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">
<meta name="referrer" content="no-referrer">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{esc(root)}style.css">
</head>
<body>
<header>{nav}<h1>{esc(title)}</h1><p class="updated">{esc(updated)}</p>
<p class="unit">{esc(text["unit_note"])}</p><p class="caution">{esc(text["caution"])}</p></header>
<main>
{main}
</main>
</body>
</html>
"""


def _market_table(rows: list[tuple[Price, Price | None]], names: marathi.Names, text: dict,
                  changed: set, jumps: set, column_help: dict) -> str:
    head = "".join(
        f'<th{" class=\"num\"" if k in ("col_min", "col_modal", "col_max") else ""}>'
        f"{_tip(text[k], column_help.get(k), tip_id('col', k) if k in column_help else None)}</th>"
        for k in COLUMNS)
    body = []
    # Rows arrive sorted by crop. Each crop's name is written once, spanning its
    # variety and grade lines, so the same crop does not read as a duplicate.
    group_size: dict[str, int] = {}
    for price, _ in rows:
        group_size[price.commodity] = group_size.get(price.commodity, 0) + 1
    seen: set[str] = set()
    for price, prev in rows:
        is_new = price.key in changed
        mark = f'<span class="mark">{esc(text["new_mark"])}</span>' if is_new else ""
        delta = price.modal - prev.modal if prev else None
        jump = (f'<span class="jump">⚠ {esc(text["jump_mark"])}</span>'
                if price.series in jumps else "")
        crop_cell = ""
        if price.commodity not in seen:
            seen.add(price.commodity)
            span = group_size[price.commodity]
            crop_cell = (f'<td class="crop"{f" rowspan={chr(34)}{span}{chr(34)}" if span > 1 else ""}>'
                         f"{esc(names.commodity(price.commodity))}</td>")
        body.append(
            f'<tr{" class=\"new\"" if is_new else ""}>{crop_cell}'
            f'<td class="num"><strong>{esc(marathi.number(price.modal))}</strong></td>'
            f"<td>{esc(marathi.change(delta))}{jump}</td>"
            f'<td class="kind">{_kind(price, names)}{mark}</td>'
            f"<td>{esc(marathi.short_date(price.date))}</td>"
            f'<td class="num">{esc(marathi.number(price.min))}</td>'
            f'<td class="num">{esc(marathi.number(price.max))}</td></tr>')
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>")


def build_site(site_dir: Path, cfg: dict, names: marathi.Names,
               latest: dict[tuple, tuple[Price, Price | None]], changed: set, jumps: set,
               now: dt.datetime) -> dict:
    """Write the whole site from scratch. Returns counts and unknown districts."""
    text = cfg["text_mr"]
    districts = Districts(cfg)
    if site_dir.exists():
        if any(site_dir.iterdir()) and not (site_dir / MARKER).exists():
            raise RuntimeError(f"refusing to clear {site_dir}: the pipeline did not create it")
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)
    (site_dir / MARKER).write_text("built by mandi-prices; safe to delete\n", encoding="utf-8")
    (site_dir / "style.css").write_text(STYLE, encoding="utf-8")

    grouped: dict[str, dict[str, list]] = {d["slug"]: {} for d in districts.items}
    grouped["other"] = {}
    unknown: set[str] = set()
    for price, prev in latest.values():
        d = districts.find(price.district)
        slug = d["slug"] if d else "other"
        if not d:
            unknown.add(price.district)
        grouped[slug].setdefault(price.market, []).append((price, prev))

    updated = text["updated"].format(when=marathi.when(now, cfg.get("day_parts_mr")))
    column_help = (cfg.get("glossary_mr") or {}).get("columns") or {}
    glossary = _glossary(cfg, names)
    boxes = _boxes(cfg, names)
    key = _key(cfg)
    days = cfg["fetch"]["keep_days"]
    entries = [(d["slug"], d["mr"], text["district_title"].format(district=d["mr"]))
               for d in districts.items]
    if grouped["other"]:
        entries.append(("other", text["other_title"], text["other_title"]))

    index_items, pages = [], 0
    for slug, short, title in entries:
        markets = grouped[slug]
        rows_total = sum(len(v) for v in markets.values())
        new_total = sum(1 for v in markets.values() for p, _ in v if p.key in changed)
        parts = []
        if new_total:
            parts.append(f'<p class="whats-new">{esc(text["whats_new"].format(count=marathi.number(new_total)))}</p>')
        if not markets:
            parts.append(f"<p>{esc(text['no_prices'].format(days=marathi.digits(days)))}</p>")
        else:
            parts.append(key)
        for market in sorted(markets, key=lambda m: (names.market(m), m)):
            # Same crop together (raw name too, so two spellings that share a
            # Marathi name never interleave), then variety, then grade.
            rows = sorted(markets[market], key=lambda r: (names.commodity(r[0].commodity),
                                                          r[0].commodity, r[0].variety, r[0].grade))
            shown = names.market(market)
            parts.append(f"<section><h2>{esc(shown)}</h2>")
            if norm(shown) != norm(market):
                parts.append(f'<p class="en">{esc(" ".join(market.split()))}</p>')
            parts.append(_market_table(rows, names, text, changed, jumps, column_help) + "</section>")
        if markets:
            parts.append(glossary)
            parts.append(boxes)
        page_dir = site_dir / slug
        page_dir.mkdir()
        (page_dir / "index.html").write_text(
            _page(title, "../", updated, "\n".join(parts), text), encoding="utf-8")
        pages += 1
        counts = text["index_counts"].format(markets=marathi.number(len(markets)),
                                             prices=marathi.number(rows_total))
        mark = f' <span class="mark">{esc(text["new_mark"])} {esc(marathi.number(new_total))}</span>' if new_total else ""
        index_items.append(f'<li><a href="{esc(slug)}/">{esc(short)}</a>{mark}<small>{esc(counts)}</small></li>')

    total_new = sum(1 for p, _ in latest.values() if p.key in changed)
    intro = (f'<p class="whats-new">{esc(text["whats_new"].format(count=marathi.number(total_new)))}</p>'
             if total_new else "")
    (site_dir / "index.html").write_text(
        _page(text["site_title"], "", updated,
              intro + '<ul class="districts">' + "".join(index_items) + "</ul>", text),
        encoding="utf-8")
    return {"pages": pages, "unknown_districts": sorted(unknown)}
