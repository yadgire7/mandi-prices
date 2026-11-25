"""Read-only client for the data.gov.in API, shared by the probe and the pipeline.

The API key is only ever placed in the request URL sent to data.gov.in over
HTTPS. Every URL that reaches a log, an error or an email goes through
`redact` first.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.data.gov.in/resource/"
RESOURCES = {
    "current": "9ef84268-d588-465a-a308-a864a43d0070",
    "variety": "35985678-0d79-46b4-9ed6-6f13308a1d24",
}
USER_AGENT = "mandi-prices/0.1"
PAGE_LIMIT = 1000
TIMEOUT_S = 30
RETRIES = 2
PAUSE_S = 1.0
MAX_ROWS = 50_000
DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
FIELDS = ("state", "district", "market", "commodity", "variety", "grade",
          "arrival_date", "min_price", "max_price", "modal_price")


class ApiError(Exception):
    """The API could not answer, after retries where retrying could help."""


def redact(url: str) -> str:
    """Return the URL with the api-key value hidden, for logs and errors."""
    parts = urllib.parse.urlsplit(url)
    query = [(k, "REDACTED" if k == "api-key" else v)
             for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(query, safe="[]")))


def urllib_fetch(url: str) -> tuple[int, bytes]:
    """GET over HTTPS. urllib verifies certificates by default."""
    if not url.startswith("https://"):
        raise ValueError("only https URLs are fetched")
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


class Client:
    def __init__(self, api_key: str, fetch=urllib_fetch, sleep=time.sleep,
                 pause: float = PAUSE_S):
        if not api_key:
            raise ValueError("an API key is required")
        self.api_key = api_key
        self.fetch = fetch
        self.sleep = sleep
        self.pause = pause
        self.requests = 0

    def get(self, resource_id: str, params: dict) -> dict:
        query = {"api-key": self.api_key, "format": "json", **params}
        url = API_BASE + resource_id + "?" + urllib.parse.urlencode(query, safe="[]")
        problem = "no attempt made"
        for attempt in range(RETRIES + 1):
            if attempt:
                self.sleep(2 ** attempt)
            self.requests += 1
            try:
                status, body = self.fetch(url)
            except OSError as err:  # URLError, timeouts and resets are all OSError
                problem = f"network error: {err.__class__.__name__}"
                continue
            if status == 200:
                try:
                    data = json.loads(body)
                except ValueError:
                    problem = "HTTP 200 but the body was not JSON"
                    continue
                if isinstance(data, dict):
                    return data
                problem = "HTTP 200 but the JSON was not an object"
                continue
            snippet = body[:200].decode("utf-8", "replace").strip()
            problem = f"HTTP {status}: {snippet}"
            if 400 <= status < 500 and status != 429:
                break  # a client error will not fix itself on retry
        raise ApiError(f"{problem} [{redact(url)}]".replace(self.api_key, "REDACTED"))

    def records(self, resource_id: str, filters: dict) -> tuple[list[dict], int | None]:
        """Every record matching the filters, page by page."""
        rows: list[dict] = []
        offset = 0
        total = None
        while True:
            page = self.get(resource_id, {**filters, "limit": str(PAGE_LIMIT),
                                          "offset": str(offset)})
            batch = page.get("records") or []
            if not isinstance(batch, list):
                raise ApiError("'records' was not a list")
            total = to_int(page.get("total"))
            rows.extend(r for r in batch if isinstance(r, dict))
            offset += len(batch)
            if not batch or len(rows) >= MAX_ROWS:
                return rows, total
            if total is not None:
                if offset >= total:
                    return rows, total
            elif len(batch) < PAGE_LIMIT:
                return rows, total
            self.sleep(self.pause)


def to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def norm(value) -> str:
    """Case- and whitespace-insensitive form, so 'APMC  Latur ' == 'apmc latur'."""
    return " ".join(str(value or "").split()).casefold()


def date_format(sample: str) -> str | None:
    for fmt in DATE_FORMATS:
        try:
            dt.datetime.strptime(sample.strip(), fmt)
            return fmt
        except ValueError:
            continue
    return None


def parse_date(value) -> dt.date | None:
    text = str(value or "")
    fmt = date_format(text)
    return dt.datetime.strptime(text.strip(), fmt).date() if fmt else None


def field_map(record: dict) -> dict[str, str]:
    """Map our field names to the resource's own spelling (State vs state)."""
    by_lower = {k.lower(): k for k in record}
    return {name: by_lower[name] for name in FIELDS if name in by_lower}


def canonical(rows: list[dict]) -> list[dict]:
    """Rename every record's fields to our lower-case names."""
    out = []
    for r in rows:
        fields = field_map(r)
        out.append({name: r.get(src) for name, src in fields.items()})
    return out


def load_api_key(env=os.environ, dotenv: Path | None = None) -> tuple[str | None, list[str]]:
    """The key from DATA_GOV_IN_API_KEY, or from a .env file next to the code."""
    dotenv = dotenv or Path(__file__).resolve().parents[1] / ".env"
    notes = []
    key = (env.get("DATA_GOV_IN_API_KEY") or "").strip()
    if key:
        return key, notes
    if dotenv.exists():
        if dotenv.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            notes.append(f"{dotenv.name} can be read by other users; run: chmod 600 {dotenv.name}")
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "DATA_GOV_IN_API_KEY":
                key = value.strip().strip('"').strip("'")
    return (key or None), notes
