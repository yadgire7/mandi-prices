"""Weather alerts in the WhatsApp post: reading alerts.json and the post block."""
import copy
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mandi import weather_alerts as wa  # noqa: E402
from test_pipeline import CFG, IST, Run, base_records  # noqa: E402

NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=IST)
SLUGS = {d["slug"] for d in CFG["districts"]}


def body(districts, generated=NOW - dt.timedelta(hours=2, minutes=30), **extra):
    return json.dumps({"version": 1, "generated_at": generated.isoformat(), "window_hours": 24,
                       "districts": districts, **extra}).encode()


def fetcher(status=200, payload=b"", error=None):
    def fetch(url):
        if error:
            raise error
        return status, payload
    return fetch


class ParseTests(unittest.TestCase):
    def test_counts_per_kind_and_district(self):
        alerts = wa.parse(body({"latur": {"rain": ["ausa", "udgir", "ausa"]},
                                "beed": {"rain": ["parli"], "storm": ["beed"]}}), SLUGS)
        self.assertEqual(alerts.counts["rain"], {"latur": 2, "beed": 1})
        self.assertEqual(alerts.counts["storm"], {"beed": 1})

    def test_unknown_districts_and_kinds_are_ignored_not_shown(self):
        alerts = wa.parse(body({"goa": {"rain": ["x"]}, "latur": {"snow": ["ausa"]}}), SLUGS)
        self.assertEqual(alerts.ignored, ["goa"])
        self.assertTrue(all(not v for v in alerts.counts.values()))

    def test_malformed_files_are_refused(self):
        bad = [b"not json", b"[]", body({}, version=2),
               json.dumps({"version": 1, "generated_at": "2026-11-26T09:30:00", "districts": {}}).encode(),
               body({"../x": {"rain": ["a"]}}), body({"latur": {"rain": "ausa"}}),
               body({"latur": {"rain": [1, 2]}}), b" " * (wa.MAX_BYTES + 1)]
        for payload in bad:
            with self.subTest(payload[:40]), self.assertRaises(ValueError):
                wa.parse(payload, SLUGS)


class LoadTests(unittest.TestCase):
    def test_fresh_alerts_are_used(self):
        alerts, note = wa.load(CFG, NOW, fetch=fetcher(payload=body({"latur": {"rain": ["ausa"]}})))
        self.assertIsNotNone(alerts)
        self.assertIn("rain in 1 districts", note)

    def test_old_unreachable_or_broken_alerts_are_left_out(self):
        cases = {"hours old": fetcher(payload=body({}, generated=NOW - dt.timedelta(hours=7))),
                 "HTTP 404": fetcher(status=404),
                 "could not read": fetcher(error=OSError("timed out")),
                 "JSONDecodeError": fetcher(payload=b"<html>")}
        for words, fetch in cases.items():
            alerts, note = wa.load(CFG, NOW, fetch=fetch)
            with self.subTest(words):
                self.assertIsNone(alerts)
                self.assertIn(words, note)

    def test_switched_off_when_url_is_empty(self):
        cfg = copy.deepcopy(CFG)
        cfg["weather_alerts"]["url"] = ""
        self.assertIsNone(wa.load(cfg, NOW, fetch=fetcher(error=AssertionError("no call")))[0])


class PostBlockTests(unittest.TestCase):
    text = CFG["text_mr"]

    def alerts(self, districts):
        return wa.parse(body(districts), SLUGS)

    def test_block_lists_districts_with_taluka_counts_most_serious_first(self):
        lines = wa.post_lines(self.alerts({"latur": {"rain": ["a", "b", "c"]},
                                           "beed": {"rain": ["a"], "storm": ["b"]}}), CFG)
        self.assertEqual(lines[0], f"*{self.text['post_weather_title']}*")
        self.assertEqual(lines[1], f"{self.text['post_weather_storm']}: बीड (१)")
        self.assertEqual(lines[2], f"{self.text['post_weather_rain']}: लातूर (३), बीड (१)")
        self.assertEqual(lines[-1], self.text["post_weather_link"].format(
            url=CFG["weather_alerts"]["page_url"]))

    def test_long_lists_are_cut_with_a_count(self):
        many = {d["slug"]: {"rain": ["x"]} for d in CFG["districts"][:12]}
        line = wa.post_lines(self.alerts(many), CFG)[1]
        self.assertEqual(line.count("("), CFG["weather_alerts"]["max_districts"])
        self.assertIn(self.text["post_weather_more"].format(n="४"), line)

    def test_quiet_weather_says_so_and_missing_alerts_add_nothing(self):
        lines = wa.post_lines(self.alerts({}), CFG)
        self.assertEqual(lines[1], self.text["post_weather_none"])
        self.assertEqual(wa.post_lines(None, CFG), [])

    def test_no_text_from_the_file_reaches_the_post(self):
        alerts = self.alerts({"latur": {"rain": ["<b>BUY NOW http://spam.example</b>"]}})
        joined = "\n".join(wa.post_lines(alerts, CFG))
        self.assertNotIn("spam", joined)
        self.assertNotIn("BUY", joined)

    def test_block_sits_after_the_prices_and_before_the_links(self):
        alerts = self.alerts({"latur": {"rain": ["ausa"]}})
        result = Run(self)(base_records(), at="12:00", weather=alerts)
        post = result.post
        self.assertLess(post.index("*तूर*"), post.index(self.text["post_weather_title"]))
        self.assertLess(post.index(self.text["post_weather_title"]), post.index(self.text["post_footer"]))
        self.assertNotIn(self.text["post_weather_title"], Run(self)(base_records(), at="12:00").post)



class MessageChoiceTests(unittest.TestCase):
    """The admin can paste weather only, prices only, or both."""
    text = CFG["text_mr"]

    def alerts(self):
        return wa.parse(body({"latur": {"rain": ["ausa", "udgir"]}}), SLUGS)

    def test_three_versions_when_prices_and_alerts_are_ready(self):
        posts = Run(self)(base_records(), at="12:00", weather=self.alerts(), note="नवीन व्हिडिओ").posts
        self.assertEqual(list(posts), ["weather", "prices", "both"])
        title, crop = self.text["post_weather_title"], "*तूर*"
        self.assertIn(title, posts["weather"])
        self.assertNotIn(crop, posts["weather"])
        self.assertTrue(posts["weather"].startswith(self.text["post_weather_header"].split("{")[0]))
        self.assertIn(crop, posts["prices"])
        self.assertNotIn(title, posts["prices"])
        self.assertIn(crop, posts["both"])
        self.assertIn(title, posts["both"])
        for kind, text in posts.items():
            with self.subTest(kind):
                self.assertIn("नवीन व्हिडिओ", text)
                self.assertTrue(text.rstrip().endswith(self.text["post_channel"]))

    def test_no_alerts_means_prices_only(self):
        posts = Run(self)(base_records(), at="12:00").posts
        self.assertEqual(list(posts), ["prices"])

    def test_weather_is_offered_even_when_prices_did_not_change(self):
        run = Run(self)
        run(base_records(), at="07:00")                       # morning post already made
        result = run(base_records(), at="13:00", weather=self.alerts())
        self.assertEqual(list(result.posts), ["weather"])
        self.assertIsNone(result.post)

    def test_cli_saves_each_version_to_its_own_file(self):
        import tempfile
        from unittest import mock
        import fetch_mandi_prices
        from mandi import fetched
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fetched.write(tmp / "p.json.gz", NOW - dt.timedelta(minutes=10), base_records())
            with mock.patch.object(wa, "load", return_value=(self.alerts(), "note")), \
                    mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                fetch_mandi_prices.main(["--from-fetched", str(tmp / "p.json.gz"), "--no-email",
                                         "--at", "2026-11-26T12:00", "--now", "--out", str(tmp / "out"),
                                         "--site", str(tmp / "site"), "--state", str(tmp / "s.json")],
                                        environ={})
            names = sorted(f.name.split("-")[-1] for f in (tmp / "out").glob("post-*.txt"))
        self.assertEqual(names, ["both.txt", "prices.txt", "weather.txt"])


if __name__ == "__main__":
    unittest.main()
