"""Tests for the Phase 1 pipeline: checks, snapshot, pages, post, email, CLI."""
import copy
import datetime as dt
import email
import email.policy
import html
import io
import json
import re
import sys
import tempfile
import unittest
import unittest.mock
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fetch_mandi_prices  # noqa: E402
from mandi import marathi, notify, pages, pipeline, rows, state  # noqa: E402
from mandi.api import ApiError  # noqa: E402
from mandi.config import ConfigError, load_config, validate_config  # noqa: E402

CFG = load_config(ROOT / "config.yaml")
IST = pipeline.IST
TODAY = dt.date(2026, 11, 25)


def rec(market="APMC Lasalgaon ", commodity="Onion", variety="Red", grade="Local",
        day=TODAY, modal=4250, lo=None, hi=None, district="Nashik", state_="Maharashtra"):
    return {"state": state_, "district": district, "market": market, "commodity": commodity,
            "variety": variety, "grade": grade, "arrival_date": day.strftime("%d/%m/%Y"),
            "min_price": str(lo if lo is not None else modal - 500),
            "max_price": str(hi if hi is not None else modal + 500),
            "modal_price": str(modal)}


def base_records():
    y = TODAY - dt.timedelta(days=1)
    return [
        rec(day=y, modal=4100), rec(modal=4250),
        rec(variety="Unhali", modal=4000),
        rec(market="APMC Akola", district="Akola", commodity="Soyabean", variety="Yellow",
            grade="FAQ", modal=7900),
        rec(market="APMC  Latur", district="Latur", commodity="Red gram/Arhar/Tur(whole)",
            variety="Other", grade="FAQ", modal=10000),
        rec(market="Pune(Moshi)", district="Pune", commodity="Tomato", variety="Local", modal=1050),
    ]


class Run:
    """A temp workspace that runs the pipeline like the CLI does."""

    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def __call__(self, records, at="07:00", cfg=CFG, force=False, note="", day=TODAY):
        hh, mm = (int(x) for x in at.split(":"))
        opts = pipeline.Options(now=dt.datetime.combine(day, dt.time(hh, mm), IST),
                                site_dir=self.dir / "site", state_path=self.dir / "state.json",
                                force_post=force, note=note)
        source = records if callable(records) else (lambda today, r=records: copy.deepcopy(r))
        return pipeline.run(cfg, source, opts)

    def page(self, slug):
        return (self.dir / "site" / slug / "index.html").read_text(encoding="utf-8")


class MarathiTests(unittest.TestCase):
    def test_indian_grouping_and_digits(self):
        self.assertEqual(marathi.rupees(4250), "₹४,२५०")
        self.assertEqual(marathi.rupees(1234567), "₹१२,३४,५६७")
        self.assertEqual(marathi.rupees(999), "₹९९९")
        self.assertEqual(marathi.change(150), "🟢▲₹१५०")
        self.assertEqual(marathi.change(-1500), "🔴▼₹१,५००")
        self.assertEqual(marathi.change(0), "±०")
        self.assertEqual(marathi.change(None), "")
        self.assertEqual(marathi.short_date(dt.date(2026, 11, 24)), "२४ नोव्हें")

    def test_names_fall_back_to_source_spelling(self):
        names = marathi.Names(CFG)
        self.assertEqual(names.market("APMC  Latur "), "लातूर कृषी उत्पन्न बाजार समिती")
        self.assertEqual(names.market("Brand New Mandi"), "Brand New Mandi")
        self.assertEqual(names.commodity("Dragon fruit"), "Dragon fruit")

    def test_market_names_are_built_in_devanagari(self):
        names = marathi.Names(CFG)
        self.assertEqual(names.market("APMC Akole"), "अकोले कृषी उत्पन्न बाजार समिती")
        self.assertEqual(names.market("Pune(Moshi)"), "पुणे (मोशी)")
        self.assertEqual(names.market("APMC Kalamb (Dharashiv)"), "कळंब (धाराशिव) कृषी उत्पन्न बाजार समिती")
        self.assertEqual(names.market("Dahanu APMC"), "डहाणू कृषी उत्पन्न बाजार समिती")
        self.assertIsNone(names.market_devanagari("APMC Atlantis"))

    def test_every_official_maharashtra_market_has_a_devanagari_name(self):
        names = marathi.Names(CFG)
        lines = (ROOT / "tests" / "fixtures" / "maharashtra_markets.txt").read_text(encoding="utf-8")
        markets = [x for x in lines.splitlines() if x.strip() and not x.startswith("#")]
        self.assertEqual(len(markets), 429)
        missing = [m for m in markets if names.market_devanagari(m) is None]
        self.assertEqual(missing, [])
        latin = [m for m in markets if re.search(r"[A-Za-z]{4,}", names.market(m))]
        self.assertEqual(latin, [], "names should be Devanagari, apart from short initials")

    def test_every_reported_produce_and_variety_has_a_marathi_name(self):
        names = marathi.Names(CFG)
        lines = (ROOT / "tests" / "fixtures" / "maharashtra_produce.txt").read_text(encoding="utf-8")
        entries = [line.split("\t", 1) for line in lines.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(entries), 113 + 39)
        missing = [(kind, value) for kind, value in entries
                   if not names.has_name("commodities" if kind == "commodity" else "varieties", value)]
        self.assertEqual(missing, [])
        shown = [names.commodity(v) if k == "commodity" else names.variety(v) for k, v in entries]
        latin = [x for x in shown if re.search(r"[A-Za-z]{4,}", x)]
        self.assertEqual(latin, [])

    def test_admin_given_produce_names(self):
        names = marathi.Names(CFG)
        given = {"Seetapal": "सीताफळ", "Bottle gourd": "दुधी भोपळा", "Chikoos(Sapota)": "चिकू",
                 "Chilly Capsicum": "शिमला मिरची", "Drumstick": "शेवगा", "Green Peas": "वाटाणा",
                 "Guar": "गवार", "Mousambi(Sweet Lime)": "मोसंबी", "Pineapple": "अननस",
                 "Sugarcane": "ऊस", "Mango(Raw-Ripe)": "आंबा", "Water Melon": "कलिंगड"}
        for english, marathi_name in given.items():
            self.assertEqual(names.commodity(english), marathi_name, english)

    def test_runs_are_named_by_part_of_day(self):
        parts = CFG["day_parts_mr"]
        at = lambda day, hh, mm=0: dt.datetime(2026, 11, day, hh, mm, tzinfo=IST)
        self.assertEqual(marathi.when(at(26, 0), parts), "२६ नोव्हें २०२६, सकाळ १२:००")
        self.assertEqual(marathi.when(at(25, 6), parts), "२५ नोव्हें २०२६, सकाळ ६:००")
        self.assertEqual(marathi.when(at(25, 11, 59), parts), "२५ नोव्हें २०२६, सकाळ ११:५९")
        self.assertEqual(marathi.when(at(25, 12), parts), "२५ नोव्हें २०२६, दुपार १२:००")
        self.assertEqual(marathi.when(at(25, 17, 45), parts), "२५ नोव्हें २०२६, दुपार ५:४५")
        self.assertEqual(marathi.when(at(25, 18), parts), "२५ नोव्हें २०२६, संध्याकाळ ६:००")
        self.assertEqual(marathi.when(at(25, 23, 30), parts), "२५ नोव्हें २०२६, संध्याकाळ ११:३०")


class ValidationTests(unittest.TestCase):
    def check(self, record):
        return rows.validate([record], CFG, TODAY)

    def test_good_row_passes(self):
        good, bad = self.check(rec())
        self.assertEqual((len(good), bad), (1, []))
        self.assertEqual(good[0].numbers, (3750, 4250, 4750))

    def test_bad_rows_are_rejected_with_reasons(self):
        cases = {
            "modal above max": rec(modal=5000, lo=4000, hi=4500),
            "zero price": rec(modal=0, lo=0, hi=0),
            "not a number": dict(rec(), modal_price="abc"),
            "infinite": dict(rec(), max_price="inf"),
            "above cap": rec(modal=2_000_000, lo=1_900_000, hi=2_100_000),
            "future date": rec(day=TODAY + dt.timedelta(days=1)),
            "too old": rec(day=TODAY - dt.timedelta(days=30)),
            "bad date": dict(rec(), arrival_date="31/02/2026"),
            "other state": rec(state_="Gujarat"),
            "control char": rec(market="APMC\x00Lasalgaon"),
            "too long": rec(market="X" * 200),
            "missing market": rec(market="  "),
        }
        for label, record in cases.items():
            with self.subTest(label):
                good, bad = self.check(record)
                self.assertEqual(good, [], label)
                self.assertEqual(len(bad), 1, label)

    def test_only_today_and_yesterday_are_kept(self):
        self.assertEqual((CFG["fetch"]["days"], CFG["fetch"]["keep_days"]), (2, 2))
        yesterday, two_ago = TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=2)
        good, bad = rows.validate([rec(), rec(day=yesterday), rec(day=two_ago)], CFG, TODAY)
        self.assertEqual(sorted(p.date for p in good), [yesterday, TODAY])
        self.assertEqual(len(bad), 1)
        old = rows.Price("Nashik", "M", "Onion", "", "", two_ago, 1, 2, 3)
        merged = state.merge({old.key: old}, good, TODAY, CFG["fetch"]["keep_days"])
        self.assertNotIn(old.key, merged)

    def test_conflicting_duplicates_are_reported(self):
        good, bad = rows.validate([rec(modal=4000), rec(modal=4200)], CFG, TODAY)
        self.assertEqual(len(good), 1)
        self.assertEqual(good[0].modal, 4200)
        self.assertIn("listed twice", bad[0])


class SnapshotTests(unittest.TestCase):
    def test_compare_merge_and_round_trip(self):
        old, _ = rows.validate([rec(modal=4000), rec(variety="Unhali", modal=3900)], CFG, TODAY)
        cur, _ = rows.validate([rec(modal=4100), rec(variety="Unhali", modal=3900),
                                rec(variety="White", modal=4700)], CFG, TODAY)
        prev = {p.key: p for p in old}
        new, changed = state.compare(prev, cur)
        self.assertEqual([p.variety for p in new], ["White"])
        self.assertEqual([(a.modal, b.modal) for a, b in changed], [(4000, 4100)])
        ancient = rows.Price("Nashik", "X", "Onion", "", "", TODAY - dt.timedelta(days=20), 1, 2, 3)
        merged = state.merge({**prev, ancient.key: ancient}, cur, TODAY, 7)
        self.assertNotIn(ancient.key, merged)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s" / "prices.json"
            state.save(path, merged, "2026-11-25", dt.datetime(2026, 11, 25, 7, tzinfo=IST))
            loaded = state.load(path)
            self.assertEqual(loaded.prices, merged)
            self.assertEqual(loaded.last_post_date, "2026-11-25")

    def test_jump_rule(self):
        a = rows.Price("N", "M", "C", "", "", TODAY, 1, 1000, 2000)
        self.assertTrue(state.is_jump(a, rows.Price("N", "M", "C", "", "", TODAY, 1, 300, 400), 3))
        self.assertTrue(state.is_jump(a, rows.Price("N", "M", "C", "", "", TODAY, 1, 3000, 4000), 3))
        self.assertFalse(state.is_jump(a, rows.Price("N", "M", "C", "", "", TODAY, 1, 600, 900), 3))
        self.assertFalse(state.is_jump(a, None, 3))


class PipelineTests(unittest.TestCase):
    def test_first_run_saves_baseline_and_posts_once(self):
        run = Run(self)
        result = run(base_records(), at="07:00")
        self.assertEqual(result.status, "WARN")
        self.assertIn("baseline", result.warnings[0])
        self.assertIsNotNone(result.post)
        self.assertNotIn("🆕", result.post)
        # 13:00, same data: nothing new and the morning post is done.
        later = run(base_records(), at="13:00")
        self.assertIsNone(later.post)
        self.assertIn("Nothing new", later.summary[0])

    def test_new_and_changed_prices_make_a_post(self):
        run = Run(self)
        run(base_records(), at="07:00")
        changed = base_records()
        changed[1] = rec(modal=4400)                                     # corrected
        changed.append(rec(market="APMC Akola", district="Akola", commodity="Cotton",
                           variety="Desi", modal=9600))                  # new
        result = run(changed, at="13:00")
        self.assertIsNotNone(result.post)
        self.assertIn("1 new and 1 corrected", result.summary[0])
        self.assertIn("₹४,४००", result.post)
        self.assertIn("▲₹३००", result.post)        # 4400 vs yesterday's 4100
        self.assertIn("*कापूस*", result.post)
        self.assertEqual(result.post.count("🆕"), 2)
        self.assertIn("नवीन", run.page("nashik"))

    def test_morning_post_happens_without_changes(self):
        run = Run(self)
        run(base_records(), at="01:00")             # before 07:00: baseline, no post
        result = run(base_records(), at="07:00")
        self.assertIsNotNone(result.post)
        self.assertIn("first run of the day", result.summary[-1])
        self.assertIsNone(run(base_records(), at="19:00").post)

    def test_admin_run_always_posts_with_clean_note(self):
        run = Run(self)
        run(base_records(), at="07:00")
        result = run(base_records(), at="15:00", force=True,
                     note="नवीन व्हिडिओ\x00 आला\x1b[31m")
        self.assertIn("नवीन व्हिडिओ आला[31m", result.post)
        self.assertNotIn("\x00", result.post)
        self.assertNotIn("\x1b", result.post)

    def test_api_failure_is_a_fail_without_post(self):
        def broken(today):
            raise ApiError("HTTP 503: down [https://api.data.gov.in/...api-key=REDACTED]")
        result = Run(self)(broken)
        self.assertEqual(result.status, "FAIL")
        self.assertIsNone(result.post)

    def test_no_usable_rows_is_a_fail(self):
        result = Run(self)([rec(modal=0, lo=0, hi=0)])
        self.assertEqual(result.status, "FAIL")

    def test_run_reports_coverage_stats(self):
        run = Run(self)
        run(base_records())
        result = run(base_records() + [rec(market="APMC Akola", district="Akola",
                                           commodity="Soyabean", variety="Yellow", grade="FAQ",
                                           modal=8100, day=TODAY - dt.timedelta(days=1))], at="13:00")
        self.assertEqual(result.stats["today"], 5)
        self.assertEqual(result.stats["yesterday"], 2)
        self.assertEqual(result.stats["districts_today"], 4)
        self.assertEqual((result.stats["new"], result.stats["corrected"]), (1, 0))
        self.assertEqual(result.stats["post"], "yes")

    def test_unusual_jump_is_flagged(self):
        run = Run(self)
        run(base_records(), at="07:00")
        records = base_records()
        records[1] = rec(modal=41000, lo=40000, hi=42000)   # ten times yesterday
        result = run(records, at="13:00")
        self.assertTrue(any("Unusual jump" in w for w in result.warnings))
        self.assertIn("⚠", result.post)
        self.assertIn("असामान्य बदल", run.page("nashik"))

    def test_unknown_district_goes_to_other_page_with_warning(self):
        run = Run(self)
        result = run(base_records() + [rec(market="New Mandi", district="Newdistrict")])
        self.assertTrue(any("Unknown district" in w for w in result.warnings))
        self.assertIn("New Mandi", run.page("other"))

    def test_site_folder_not_made_by_pipeline_is_never_deleted(self):
        run = Run(self)
        (run.dir / "site").mkdir()
        (run.dir / "site" / "precious.txt").write_text("keep me")
        with self.assertRaises(RuntimeError):
            run(base_records())
        self.assertTrue((run.dir / "site" / "precious.txt").exists())


class TagCollector(HTMLParser):
    """Parse a page the way a browser would and record every tag and attribute."""

    def __init__(self):
        super().__init__()
        self.tags, self.attrs = set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs += [(tag, name, value) for name, value in attrs]


def parse(text):
    collector = TagCollector()
    collector.feed(text)
    return collector


ALLOWED_TAGS = {"html", "head", "meta", "title", "link", "body", "header", "main", "footer",
                "a", "h1", "h2", "p", "section", "div", "table", "thead", "tbody", "tr",
                "th", "td", "strong", "span", "small", "ul", "li", "abbr", "details",
                "summary", "dl", "dt", "dd", "button"}


class PageSecurityTests(unittest.TestCase):
    HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)>'

    def test_hostile_values_are_escaped_everywhere(self):
        run = Run(self)
        hostile = rec(market=self.HOSTILE, commodity='"><svg onload=alert(1)>',
                      variety="<b>", district="Nashik")
        run(base_records() + [hostile])
        for path in (run.dir / "site").rglob("*.html"):
            page = parse(path.read_text(encoding="utf-8"))
            self.assertLessEqual(page.tags, ALLOWED_TAGS, path)
            for tag, name, value in page.attrs:
                self.assertFalse(name.startswith("on"), (path, tag, name))
                self.assertIn(name, {"lang", "charset", "name", "content", "http-equiv",
                                     "rel", "href", "class", "colspan", "rowspan", "title", "type", "id",
                                     "popover", "popovertarget", "popovertargetaction"},
                              (path, tag, name))
                if name == "href":
                    self.assertFalse(re.match(r"^\s*(javascript|data|https?):", value or "", re.I))
        self.assertIn("&lt;script&gt;", run.page("nashik"))

    def test_every_page_has_csp_and_no_outside_resources(self):
        run = Run(self)
        run(base_records())
        site = run.dir / "site"
        htmls = list(site.rglob("*.html"))
        self.assertEqual(len(htmls), len(CFG["districts"]) + 1)
        for path in htmls:
            text = path.read_text(encoding="utf-8")
            csp = [v for t, n, v in parse(text).attrs if t == "meta" and n == "content"
                   and "default-src" in (v or "")]
            self.assertEqual(len(csp), 1, path)
            self.assertIn("default-src 'none'", csp[0])
            self.assertIn("form-action 'none'", csp[0])
            self.assertNotRegex(text, r'(src|href)="(https?:)?//', path)
            self.assertNotIn("<script", text)
            self.assertNotIn("<form", text)


class GlossaryTests(unittest.TestCase):
    def test_grades_get_tooltips_and_pages_get_a_glossary(self):
        run = Run(self)
        run(base_records())
        page = run.page("akola")
        self.assertIn("Fair Average Quality", page)
        self.assertIn(CFG["glossary_mr"]["columns"]["col_modal"], page)
        self.assertLessEqual(page.count("Fair Average Quality"), 4, "explanations repeated per row")
        self.assertIn("<details", page)
        self.assertIn("प्रचलित भाव", page)
        self.assertIn("शब्दार्थ", page)

    def test_terms_open_a_box_on_tap_and_every_box_exists(self):
        run = Run(self)
        run(base_records())
        page = parse(run.page("akola"))
        targets = {v for t, n, v in page.attrs if n == "popovertarget"}
        boxes = {v for t, n, v in page.attrs if t == "div" and n == "id"}
        popovers = {v for t, n, v in page.attrs if n == "id"} & {
            v for t, n, v in page.attrs if t == "div" and n == "id"}
        self.assertIn("tip-term-faq", targets)
        self.assertIn("tip-col-col-modal", targets)
        self.assertLessEqual(targets, boxes, "a tappable word points at a missing box")
        self.assertTrue(popovers)
        self.assertTrue(all(re.fullmatch(r"tip-[a-z0-9-]+", b) for b in boxes))
        text = run.page("akola")
        self.assertIn('popover>', text)
        self.assertIn(CFG["glossary_mr"]["terms"]["FAQ"]["meaning"], text)
        self.assertIn('popovertargetaction="hide"', text)

    def test_key_is_visible_above_the_tables(self):
        run = Run(self)
        run(base_records())
        text = run.page("akola")
        key_at = text.index('class="key"')
        self.assertLess(key_at, text.index("<table"))
        for entry in CFG["glossary_mr"]["key"]:
            self.assertIn(entry, text)
        self.assertNotIn('class="key"', run.page("gadchiroli"))   # no prices, no key

    def test_post_explains_faq_and_uses_short_mandi_names(self):
        post_text = Run(self)(base_records()).post
        self.assertIn("FAQ = वाजवी सरासरी प्रत", post_text)
        self.assertIn("• लासलगाव (", post_text)
        self.assertNotIn("लासलगाव कृषी उत्पन्न बाजार समिती", post_text)
        self.assertIn("२५ नोव्हें २०२६, सकाळ", post_text)

    def test_page_shows_devanagari_market_name_with_english_below(self):
        run = Run(self)
        run(base_records())
        page = run.page("nashik")
        self.assertIn("<h2>लासलगाव कृषी उत्पन्न बाजार समिती</h2>", page)
        self.assertIn('<p class="en">APMC Lasalgaon</p>', page)


class HeaderTests(unittest.TestCase):
    def test_unit_and_caution_come_before_any_price(self):
        run = Run(self)
        run(base_records())
        for page in (run.page("nashik"), (run.dir / "site" / "index.html").read_text(encoding="utf-8")):
            header_end = page.index("</header>")
            self.assertLess(page.index(CFG["text_mr"]["unit_note"]), header_end)
            self.assertLess(page.index(CFG["text_mr"]["caution"]), header_end)
            self.assertNotIn("<footer", page)
        self.assertLess(run.page("nashik").index("</header>"), run.page("nashik").index("<table"))


class GroupingTests(unittest.TestCase):
    """One crop with several grades must not read as duplicate rows."""

    def records(self):
        return [rec(market="APMC Akola", district="Akola", commodity="Red gram/Arhar/Tur(whole)",
                    variety="Other", grade="FAQ", modal=9850),
                rec(market="APMC Akola", district="Akola", commodity="Red gram/Arhar/Tur(whole)",
                    variety="Other", grade="Non-FAQ", modal=7400),
                rec(market="APMC Akola", district="Akola", commodity="Wheat",
                    variety="Sharbati", grade="FAQ", modal=3950)]

    def test_crop_name_written_once_with_rowspan(self):
        run = Run(self)
        run(self.records())
        page = run.page("akola")
        self.assertEqual(page.count('<td class="crop" rowspan="2">तूर</td>'), 1)
        self.assertEqual(page.count(">तूर<"), 1)
        self.assertIn('<td class="crop">गहू</td>', page)

    def test_other_variety_is_hidden_but_real_varieties_show(self):
        run = Run(self)
        result = run(self.records())
        page = run.page("akola")
        self.assertNotIn("इतर", page)
        self.assertIn("शरबती", page)
        self.assertIn("• अकोला (FAQ): ₹९,८५०", result.post)
        self.assertIn("• अकोला (Non-FAQ): ₹७,४००", result.post)
        self.assertNotIn("इतर", result.post)


class PostTests(unittest.TestCase):
    def test_links_only_when_base_url_set(self):
        cfg = copy.deepcopy(CFG)
        cfg["site"]["base_url"] = ""
        cfg["text_mr"]["post_channel"] = ""
        self.assertNotIn("http", Run(self)(base_records(), cfg=cfg).post)
        cfg["site"]["base_url"] = "https://example.github.io/mandi-prices"
        post_text = Run(self)(base_records(), cfg=cfg).post
        self.assertIn("https://example.github.io/mandi-prices/", post_text)
        self.assertNotIn("mandi-prices//", post_text)

    def test_post_ends_with_link_then_channel_line(self):
        lines = Run(self)(base_records()).post.splitlines()
        link = CFG["text_mr"]["post_links"].format(url=CFG["site"]["base_url"])
        self.assertIn(link, lines)
        self.assertEqual(lines[-1], CFG["text_mr"]["post_channel"])

    def test_headline_crop_without_prices_says_so(self):
        post_text = Run(self)(base_records()).post
        cotton = post_text.split("*कापूस*")[1].split("*")[0]
        self.assertIn(CFG["text_mr"]["post_crop_none"], cotton)


class ConfigTests(unittest.TestCase):
    def broken(self, change):
        cfg = copy.deepcopy(CFG)
        change(cfg)
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_rejects_bad_settings(self):
        self.broken(lambda c: c["schedule"].update(every_hours=5))
        self.broken(lambda c: c["schedule"].update(first_run_ist="7am"))
        self.broken(lambda c: c["site"].update(base_url="http://insecure.example"))
        self.broken(lambda c: c["site"].update(base_url="https://x.io/\"><script>"))
        self.broken(lambda c: c["districts"].append({"name": "X", "slug": "../etc", "mr": "x"}))
        self.broken(lambda c: c["districts"].append({"name": "Pune", "slug": "pune2", "mr": "x"}))
        self.broken(lambda c: c["headline_crops"].append("saffron"))
        self.broken(lambda c: c["text_mr"].pop("post_header"))


class NotifyTests(unittest.TestCase):
    ENV = {"SMTP_HOST": "smtp.example.com", "SMTP_USER": "bot@example.com",
           "SMTP_PASSWORD": "pw-secret-123456", "MAIL_FROM": "bot@example.com",
           "MAIL_TO": "admin@example.com", "DATA_GOV_IN_API_KEY": "key-secret-abcdef"}

    def report(self, **kw):
        return notify.Report(status="WARN", when="25 Nov 2026 07:00 IST", post="पोस्ट\n", **kw)

    def test_secrets_are_scrubbed_from_email(self):
        r = self.report(warnings=["error mentioning key-secret-abcdef and pw-secret-123456"])
        text = bytes(notify.message(r, self.ENV)).decode("utf-8", "replace")
        self.assertNotIn("key-secret-abcdef", text)
        self.assertNotIn("pw-secret-123456", text)

    def test_body_holds_post_between_markers(self):
        body = notify.body(self.report())
        self.assertIn(notify.POST_START + "\nपोस्ट\n" + notify.POST_END, body)

    def test_send_uses_tls_and_logs_in(self):
        calls = []

        class FakeSMTP:
            def __init__(self, host, port, **kw):
                calls.append(("connect", host, port, "context" in kw))
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def starttls(self, context):
                calls.append(("starttls",))
            def login(self, user, pw):
                calls.append(("login", user))
            def send_message(self, msg):
                calls.append(("send", msg["To"]))

        notify.send(self.report(), self.ENV, smtp_ssl=FakeSMTP, smtp=FakeSMTP)
        self.assertEqual(calls[0], ("connect", "smtp.example.com", 465, True))
        notify.send(self.report(), {**self.ENV, "SMTP_PORT": "587"}, smtp_ssl=FakeSMTP, smtp=FakeSMTP)
        self.assertIn(("starttls",), calls)
        self.assertEqual(calls.count(("send", "admin@example.com")), 2)

    def test_send_failure_names_the_stage_but_no_setting(self):
        class Refuses:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, user, pw): raise OSError("auth refused")
        with self.assertRaises(notify.MailError) as caught:
            notify.send(self.report(), self.ENV, smtp_ssl=Refuses, smtp=Refuses)
        text = str(caught.exception)
        self.assertIn("logging in (SSL)", text)
        for value in self.ENV.values():
            self.assertNotIn(value, text)
        with self.assertRaises(notify.MailError):
            notify.send(self.report(), {**self.ENV, "SMTP_PORT": "SSL"})

    def test_missing_settings_raise_not_configured(self):
        with self.assertRaises(notify.NotConfigured):
            notify.send(self.report(), {})


class CliTests(unittest.TestCase):
    def test_dry_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "rows.json"
            data.write_text(json.dumps({"records": [
                {k.title(): v for k, v in r.items()} for r in base_records()]}))
            code = fetch_mandi_prices.main(
                ["--dry-run", "--from-file", str(data), "--at", "2026-11-25T07:00",
                 "--out", str(tmp / "out")], environ={})
            self.assertEqual(code, 0)
            self.assertTrue((tmp / "out" / "dry-run" / "site" / "nashik" / "index.html").exists())
            emails = list((tmp / "out").glob("email-*.eml"))
            self.assertEqual(len(emails), 1)
            self.assertTrue(list((tmp / "out").glob("post-*.txt")))

    def test_crash_still_saves_a_fail_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code = fetch_mandi_prices.main(
                ["--dry-run", "--from-file", str(tmp / "missing.csv"), "--at", "2026-11-25T07:00",
                 "--out", str(tmp / "out")], environ={})
            self.assertEqual(code, 1)
            email = next((tmp / "out").glob("email-*.eml")).read_bytes().decode("utf-8", "replace")
            self.assertIn("[FAIL]", email)

    def test_every_run_adds_a_line_to_the_run_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "rows.json"
            data.write_text(json.dumps({"records": [
                {k.title(): v for k, v in r.items()} for r in base_records()]}))
            for at in ("2026-11-25T07:00", "2026-11-25T13:00"):
                fetch_mandi_prices.main(["--dry-run", "--from-file", str(data), "--at", at,
                                         "--out", str(tmp / "out")], environ={})
            lines = (tmp / "out" / "dry-run" / "runs.tsv").read_text().splitlines()
            self.assertEqual(lines[0].split("\t"), list(fetch_mandi_prices.LOG_COLUMNS))
            self.assertEqual(len(lines), 3)
            row = dict(zip(fetch_mandi_prices.LOG_COLUMNS, lines[2].split("\t")))
            self.assertEqual((row["run_ist"], row["today"], row["yesterday"], row["email"]),
                             ("2026-11-25 13:00", "5", "1", "saved"))
            self.assertFalse((tmp / "out" / "runs.tsv").exists())  # dry runs keep their own log

    def test_shadow_run_is_marked_do_not_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "rows.json"
            data.write_text(json.dumps({"records": [
                {k.title(): v for k, v in r.items()} for r in base_records()]}))
            fetch_mandi_prices.main(["--dry-run", "--shadow", "--from-file", str(data),
                                     "--at", "2026-11-25T07:00", "--out", str(tmp / "out")], environ={})
            msg = email.message_from_bytes(next((tmp / "out").glob("email-*.eml")).read_bytes(),
                                           policy=email.policy.default)
            self.assertTrue(msg["Subject"].startswith("[SHADOW] ["))
            self.assertIn("do not post it to the channel", msg.get_content())

    def test_test_email_needs_mail_settings_and_hides_secrets(self):
        env = {"DATA_GOV_IN_API_KEY": "key-secret-abcdef"}
        with unittest.mock.patch.object(fetch_mandi_prices, "read_dotenv", return_value={}), \
                unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(fetch_mandi_prices.main(["--test-email"], environ=env), 1)
        self.assertIn("mail settings missing", err.getvalue())
        self.assertNotIn("key-secret-abcdef", err.getvalue())

    def test_dry_run_requires_saved_data(self):
        with self.assertRaises(SystemExit):
            fetch_mandi_prices.parse_args(["--dry-run"])


class WorkflowTests(unittest.TestCase):
    """The GitHub workflows: schedule matches config.yaml, and the security rules hold."""

    @staticmethod
    def load(name):
        import yaml
        return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))

    @staticmethod
    def triggers(flow):
        return flow.get("on", flow.get(True))  # YAML 1.1 reads a bare `on` as true

    @staticmethod
    def cron_times(expr):
        minute, hour = expr.split()[:2]
        if "/" in hour:
            span, step = hour.split("/")
            lo, hi = (int(x) for x in span.split("-"))
            hours = range(lo, hi + 1, int(step))
        else:
            hours = [int(h) for h in hour.split(",")]
        return {(h, int(minute)) for h in hours}

    def test_schedule_matches_config(self):
        crons = [c["cron"] for c in self.triggers(self.load("prices.yml"))["schedule"]]
        every = CFG["schedule"]["every_hours"]
        hh, mm = (int(x) for x in CFG["schedule"]["first_run_ist"].split(":"))
        first = dt.datetime(2026, 1, 1, hh, mm, tzinfo=IST)
        expected = set()
        for k in range(24 // every):
            utc = (first + dt.timedelta(hours=k * every)).astimezone(dt.timezone.utc)
            expected.add((utc.hour, utc.minute))
        got = set().union(*(self.cron_times(c) for c in crons))
        self.assertEqual(got, expected, "prices.yml cron no longer matches schedule in config.yaml")

    def test_workflows_follow_the_security_rules(self):
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            flow = self.load(path.name)
            with self.subTest(path.name):
                self.assertNotIn("pull_request_target", self.triggers(flow))
                self.assertIn("permissions", flow, "set token permissions explicitly")
                for job in flow["jobs"].values():
                    for step in job["steps"]:
                        if "uses" in step:
                            self.assertRegex(step["uses"], r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$",
                                             "pin every action to a full commit SHA")
                        if step.get("uses", "").startswith("actions/checkout@"):
                            self.assertIs(step.get("with", {}).get("persist-credentials"), False)
                        # ${{ }} inside a shell script is an injection risk; use env instead
                        self.assertNotRegex(step.get("run", ""), r"\$\{\{")

    def test_secrets_never_reach_pull_requests(self):
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            if "secrets." in path.read_text(encoding="utf-8"):
                with self.subTest(path.name):
                    self.assertLessEqual(set(self.triggers(self.load(path.name))),
                                         {"schedule", "workflow_dispatch"},
                                         "a workflow that uses secrets may not run on pull requests or pushes")
        self.assertEqual(self.load("tests.yml")["permissions"], {"contents": "read"})

    def test_requirements_are_hash_pinned(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^PyYAML==[\d.]+ ")
        self.assertRegex(text, r"--hash=sha256:[0-9a-f]{64}")


if __name__ == "__main__":
    unittest.main()
