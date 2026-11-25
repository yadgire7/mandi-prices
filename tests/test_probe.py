"""Tests for probe_api.py against a fake data.gov.in API (no network)."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mandi.api  # noqa: E402
import probe_api  # noqa: E402

KEY = "TESTKEY-not-real-123"
TODAY = dt.date(2026, 11, 25)  # a Wednesday
CURRENT, VARIETY = probe_api.RESOURCES["current"], probe_api.RESOURCES["variety"]


def current_row(state, district, market, commodity, variety, day, modal):
    return {"state": state, "district": district, "market": market,
            "commodity": commodity, "variety": variety, "grade": "FAQ",
            "arrival_date": day.strftime("%d/%m/%Y"),
            "min_price": str(modal - 500), "max_price": str(modal + 500),
            "modal_price": str(modal)}


def variety_row(*args):
    r = current_row(*args)
    out = {k.title(): v for k, v in r.items()}  # state -> State, arrival_date -> Arrival_Date
    out["Commodity_Code"] = "23"
    return out


def build_data():
    d1 = TODAY - dt.timedelta(days=1)
    current = [
        current_row("Maharashtra", "Nashik", "Lasalgaon", "Onion", "Red", d1, 2800),
        current_row("Maharashtra", "Latur", "APMC  Latur", "Soyabean", "Yellow", d1, 7600),
        current_row("Karnataka", "Bangalore", "Binny Mill", "Onion", "Red", d1, 3000),
    ]
    variety = []
    for back in range(7):
        day = TODAY - dt.timedelta(days=back)
        if day.weekday() == 6:  # Sunday: nothing reported
            continue
        variety.append(variety_row("Maharashtra", "Nashik", "Lasalgaon", "Onion", "Red", day, 2800 + back))
        variety.append(variety_row("Maharashtra", "Nashik", "Lasalgaon", "Onion", "Local", day, 2500))
        variety.append(variety_row("Maharashtra", "Latur", "APMC  Latur", "Soyabean", "Yellow", day, 7600))
        variety.append(variety_row("Maharashtra", "Pune", "Pune(Manjri)", "Tomato", "Hybrid", day, 1200))
        variety.append(variety_row("Gujarat", "Rajkot", "Rajkot", "Onion", "Red", day, 2600))
    return {CURRENT: current, VARIETY: variety}


class FakeApi:
    """Mimics the parts of data.gov.in the probe relies on.

    The current resource only understands filters[<field>.keyword]; the
    variety resource only filters[<Field>]. Unknown filters are ignored,
    as an ignored filter is the case the probe must detect.
    """

    def __init__(self, data, fail_first=0, status=200):
        self.data = data
        self.fail_first = fail_first
        self.status = status
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail_first:
            self.fail_first -= 1
            return 500, b"upstream error"
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        if q.get("api-key") != KEY:
            return 403, b'{"error": "Invalid key"}'
        rid = parts.path.rsplit("/", 1)[-1]
        rows = self.data[rid]
        for k, v in q.items():
            if not k.startswith("filters["):
                continue
            field = k[len("filters["):-1]
            if rid == CURRENT and field.endswith(".keyword"):
                rows = [r for r in rows if r.get(field[:-len(".keyword")]) == v]
            elif rid == VARIETY and not field.endswith(".keyword"):
                rows = [r for r in rows if r.get(field) == v]
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 10))
        page = rows[offset:offset + limit]
        body = {"status": "ok", "total": len(rows), "count": len(page), "records": page}
        return 200, json.dumps(body).encode()


CONFIG = {
    "state": "Maharashtra",
    "crops": {"onion": ["Onion"], "soybean": ["Soyabean"], "tomato": ["Tomato"],
              "tur": ["Red gram/Arhar/Tur(whole)"]},
    "mandis": [
        {"key": "lasalgaon", "district": "Nashik", "market": ["APMC Lasalgaon", "Lasalgaon"],
         "crops": ["onion"]},
        {"key": "latur", "district": "Latur", "market": ["APMC Latur"], "crops": ["soybean", "tur"]},
        {"key": "pune", "district": "Pune", "market": ["APMC Pune"], "crops": ["tomato"]},
    ],
}


def client(fetch):
    return probe_api.Client(KEY, fetch=fetch, sleep=lambda s: None, pause=0)


class HttpTests(unittest.TestCase):
    def test_redact_hides_key(self):
        url = f"https://api.data.gov.in/resource/x?api-key={KEY}&filters[state]=Maharashtra"
        out = probe_api.redact(url)
        self.assertNotIn(KEY, out)
        self.assertIn("REDACTED", out)
        self.assertIn("filters[state]=Maharashtra", out)

    def test_retries_server_error_then_succeeds(self):
        api = FakeApi(build_data(), fail_first=2)
        page = client(api).get(CURRENT, {"limit": "1"})
        self.assertEqual(len(page["records"]), 1)
        self.assertEqual(len(api.urls), 3)

    def test_gives_up_after_retries(self):
        api = FakeApi(build_data(), fail_first=10)
        with self.assertRaises(probe_api.ApiError):
            client(api).get(CURRENT, {})
        self.assertEqual(len(api.urls), probe_api.RETRIES + 1)

    def test_client_error_is_not_retried_and_key_is_not_leaked(self):
        api = FakeApi(build_data())
        bad = probe_api.Client("WRONG-KEY-xyz", fetch=api, sleep=lambda s: None)
        with self.assertRaises(probe_api.ApiError) as ctx:
            bad.get(CURRENT, {})
        self.assertEqual(len(api.urls), 1)
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertNotIn("WRONG-KEY-xyz", str(ctx.exception))

    def test_network_errors_are_retried(self):
        calls = []

        def broken(url):
            calls.append(url)
            raise TimeoutError("timed out")

        with self.assertRaises(probe_api.ApiError) as ctx:
            client(broken).get(CURRENT, {})
        self.assertEqual(len(calls), probe_api.RETRIES + 1)
        self.assertIn("network error", str(ctx.exception))

    def test_records_follows_pages(self):
        old = mandi.api.PAGE_LIMIT
        mandi.api.PAGE_LIMIT = 2
        try:
            api = FakeApi(build_data())
            rows, total = client(api).records(VARIETY, {})
        finally:
            mandi.api.PAGE_LIMIT = old
        self.assertEqual(len(rows), total)
        self.assertEqual(len(api.urls), -(-total // 2))


class ProbeTests(unittest.TestCase):
    def run_probe(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "run"
        summary = probe_api.run_probe(client(FakeApi(build_data())), CONFIG, out, TODAY, days=7)
        return summary, out

    def test_detects_the_working_filters(self):
        s, _ = self.run_probe()
        self.assertEqual(s["problems"], [])
        self.assertEqual(s["resources"]["current"]["state_filter"], "filters[state.keyword]")
        self.assertEqual(s["resources"]["variety"]["state_filter"], "filters[State]")
        self.assertEqual(s["resources"]["variety"]["date_filter"], "filters[Arrival_Date]")

    def test_reports_snapshot_and_days(self):
        s, out = self.run_probe()
        self.assertEqual(len(s["resources"]["current"]["dates"]), 1)
        per_day = s["resources"]["variety"]["per_day"]
        self.assertEqual(len(per_day), 7)
        sunday = [n for d, wd, n in per_day if wd == "Sun"]
        self.assertEqual(sunday, [0])
        report = (out / "report.md").read_text()
        self.assertIn("One-day snapshot", report)

    def test_matches_names_and_varieties(self):
        s, _ = self.run_probe()
        pairs = {p["key"]: p for p in s["pairs"]}
        las = pairs["lasalgaon"]
        self.assertEqual(las["market_names"], ["Lasalgaon"])
        onion = las["crops"][0]
        self.assertEqual([v["variety"] for v in onion["varieties"]], ["Local", "Red"])
        red = onion["varieties"][1]
        self.assertEqual(red["latest_date"], TODAY.isoformat())
        self.assertEqual(red["modal"], "2800")
        self.assertEqual(red["days"], 6)
        self.assertEqual(onion["in_current"], 1)
        # Double space in the source name still matches the config name.
        self.assertEqual(pairs["latur"]["market_names"], ["APMC  Latur"])

    def test_reports_gaps_with_suggestions(self):
        s, out = self.run_probe()
        pairs = {p["key"]: p for p in s["pairs"]}
        self.assertEqual(pairs["pune"]["market_names"], [])
        self.assertIn("Pune(Manjri)", pairs["pune"]["market_suggestions"])
        tur = pairs["latur"]["crops"][1]
        self.assertEqual(tur["varieties"], [])
        report = (out / "report.md").read_text()
        self.assertIn("**pune**: no market matched", report)
        self.assertIn("**latur / tur**: not reported", report)

    def test_key_is_never_written(self):
        _, out = self.run_probe()
        for path in out.iterdir():
            self.assertNotIn(KEY, path.read_text(), path.name)

    def test_api_failure_is_reported_not_raised(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = probe_api.Client("WRONG", fetch=FakeApi(build_data()), sleep=lambda s: None)
        s = probe_api.run_probe(bad, CONFIG, Path(tmp.name), TODAY)
        self.assertEqual(len(s["problems"]), 2)
        self.assertIn("HTTP 403", (Path(tmp.name) / "report.md").read_text())


class SetupTests(unittest.TestCase):
    def test_key_from_env_wins(self):
        key, notes = probe_api.load_api_key(env={"DATA_GOV_IN_API_KEY": " abc "},
                                            dotenv=Path("/nonexistent/.env"))
        self.assertEqual((key, notes), ("abc", []))

    def test_key_from_dotenv_warns_when_readable_by_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text('# comment\nDATA_GOV_IN_API_KEY="xyz"\n')
            os.chmod(dotenv, 0o644)
            key, notes = probe_api.load_api_key(env={}, dotenv=dotenv)
            self.assertEqual(key, "xyz")
            self.assertEqual(len(notes), 1)
            os.chmod(dotenv, 0o600)
            self.assertEqual(probe_api.load_api_key(env={}, dotenv=dotenv)[1], [])

    def test_config_rejects_undefined_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text("crops: {onion: [Onion]}\nmandis:\n  - {key: x, market: [X], crops: [garlic]}\n")
            with self.assertRaises(ValueError):
                probe_api.load_config(path)

    def test_shipped_config_loads(self):
        config = probe_api.load_config(Path(probe_api.HERE) / "config.yaml")
        self.assertEqual(len(config["mandis"]), 12)


if __name__ == "__main__":
    unittest.main()
