# Mandi prices for Shetkari Shala

Government AGMARKNET mandi prices for every Maharashtra district, as Marathi
web pages and a short WhatsApp post, for the
[शेतकरी शाळा](https://www.youtube.com/@shetkarishalaMH) YouTube channel.
Every step is a fixed rule in code: no AI, and no price is typed by hand.
The admin reviews each post and pastes it into the WhatsApp Channel.

**Status: Phase 1 done (the pipeline runs locally).** Next is Phase 2: a week
of shadow runs on a laptop. The full plan is in the "Shetkari Shala: Mandi
Price Automation Plan" doc. Phase 0 findings are in
[docs/phase0-findings.md](docs/phase0-findings.md).

## What one run does

1. Fetches Maharashtra prices for today and yesterday (t and t-1) from
   data.gov.in's variety-wise dataset. Older prices are never shown.
2. Checks every row: text length and characters, prices above zero and under a
   cap, min ≤ modal ≤ max, and real, recent dates. Rejected rows are listed in
   the email.
3. Compares the result with the last run's snapshot (`state/prices.json`) to
   find new and corrected prices, and flags any price that jumped 3× up or down.
4. Rebuilds the site: an index plus one Marathi page per district (35, because
   AGMARKNET reports Mumbai City and Suburban as one district).
5. Writes the WhatsApp post if anything is new or changed, on the first run
   after 06:00 IST, or when the admin asks with `--now`. Runs are named by part
   of day (सकाळ, दुपार, संध्याकाळ, रात्री), and changes are marked 🟢▲ up and 🔴▼ down.
6. Saves the snapshot and sends one email: OK, WARN or FAIL, with the post
   inside. If the run crashes, it still sends a FAIL email.

## Commands

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

```bash
./.venv/bin/python fetch_mandi_prices.py
```

A real run: calls the API, builds `site/`, updates `state/prices.json` and
sends the email.

```bash
./.venv/bin/python fetch_mandi_prices.py --now --note "नवीन व्हिडिओ आला आहे"
```

The admin's run: always produces a post, with the note above the prices.

```bash
./.venv/bin/python fetch_mandi_prices.py --dry-run --from-file data/phase0/2026-11-25_1127/rows_variety.csv --at 2026-11-25T06:00
```

A dry run: reads saved rows instead of the API, keeps its own site and
snapshot under `out/dry-run/`, and saves the email instead of sending it.

```bash
./.venv/bin/python -m unittest discover -s tests
```

Runs the 55 tests, with no network or key needed.

Every run saves its email as `out/email-<time>.eml` and its post as
`out/post-<time>.txt`. Exit code 1 means the run failed.

## Fetch the data by hand

The same request the pipeline makes, for one date (`dd/mm/yyyy`):

```bash
curl -sG "https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24" --data-urlencode "api-key=$DATA_GOV_IN_API_KEY" --data-urlencode "format=json" --data-urlencode "filters[State]=Maharashtra" --data-urlencode "filters[Arrival_Date]=$(date +%d/%m/%Y)" --data-urlencode "limit=1000" --data-urlencode "offset=0"
```

Use `$(date -v-1d +%d/%m/%Y)` for yesterday on a Mac (`$(date -d yesterday +%d/%m/%Y)` on Linux).
If `total` is above 1000, repeat with `offset=1000`, `offset=2000` and so on.
Load the key first, without printing it:

```bash
export DATA_GOV_IN_API_KEY="$(sed -n 's/^DATA_GOV_IN_API_KEY=//p' .env)"
```

## Settings

Everything that might change lives in `config.yaml`:

- `schedule.every_hours` (6) and `schedule.first_run_ist` (06:00), and `day_parts_mr`
- `headline_crops`: the crops in each post (cotton, soybean, onion, wheat, tur)
- `mandis`: the tracked mandis whose headline crops appear in the post
- `districts`: one page each, with AGMARKNET's spelling, aliases and Marathi name
- `checks`: the price cap, the jump factor and the longest name accepted
- `site.base_url`: the GitHub Pages address, set in Phase 3
- `names_mr`, `glossary_mr` and `text_mr`: every Marathi word on the pages and
  in the post, including the tooltips for FAQ, Non-FAQ and the column headings
- `names_produce_mr.yaml`: the produce glossary, Marathi names for all 113
  commodities and 39 varieties reported in Maharashtra
- `names_markets_mr.yaml`: Devanagari names for all 429 AGMARKNET markets in
  Maharashtra, with APMC written as कृषी उत्पन्न बाजार समिती

**All Marathi text is a draft and needs the admin's review before launch.**

## Secrets

The pipeline needs two secrets: the data.gov.in API key, and a password for
the mailbox that sends the run emails. On a laptop they live in `.env`
(copy `.env.example`, then `chmod 600 .env`). Git ignores `.env`, so it is
never committed. In Phase 3 they move to GitHub Secrets.

| Variable | What it is |
| --- | --- |
| `DATA_GOV_IN_API_KEY` | data.gov.in API key |
| `SMTP_HOST`, `SMTP_PORT` | Mail server; port 465 (TLS) or 587 (STARTTLS) |
| `SMTP_USER`, `SMTP_PASSWORD` | The sending mailbox and its app password |
| `MAIL_FROM`, `MAIL_TO` | Sender and the admin's address |

Never paste a secret into chat, a commit, a URL or an issue. The pipeline
scrubs both secrets from every email and error message it writes.

## Security in the code

- The pages are static HTML with one stylesheet: no JavaScript, forms,
  trackers or outside resources, and a Content-Security-Policy that blocks
  them. Every value from the API or config is HTML-escaped; a test feeds in
  hostile market names and fails if any tag or event handler gets through.
- The site folder is only ever cleared if the pipeline created it.
- API requests use HTTPS only, with certificate checks, timeouts and retries.
  The key never appears in logs, errors or emails.
- The admin's note is stripped of control characters and capped at 1,000
  characters.

## Files

| Path | What it is |
| --- | --- |
| `fetch_mandi_prices.py` | The command that runs the pipeline once |
| `mandi/` | The pipeline: API client, checks, snapshot, pages, post, email |
| `config.yaml` | Every setting and the Marathi labels |
| `names_markets_mr.yaml` | Devanagari names for every market |
| `names_produce_mr.yaml` | The produce glossary: Marathi names for every commodity and variety |
| `probe_api.py` | The Phase 0 probe |
| `tests/` | Tests for the pipeline and the probe |
| `docs/phase0-findings.md` | What Phase 0 found |
| `state/` | The snapshot each run leaves for the next (committed in Phase 3) |
| `site/`, `out/`, `data/` | Built pages, emails, probe output (all ignored by git) |
