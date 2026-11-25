# Mandi prices for Shetkari Shala

Government AGMARKNET mandi prices for every Maharashtra district, as Marathi
web pages and a short WhatsApp post, for the
[शेतकरी शाळा](https://www.youtube.com/@shetkarishalaMH) YouTube channel.
Every step is a fixed rule in code: no AI, and no price is typed by hand.
The admin reviews each post and pastes it into the WhatsApp Channel.

**Status: running on GitHub, with the Mac fetching.** data.gov.in refuses
connections from GitHub's servers (they are outside India), so the admin's Mac
fetches the prices and GitHub does everything else. Emails are marked SHADOW
(do not post) while the repository variable `SHADOW` is `true`. The full plan
is in the "Shetkari Shala: Mandi Price Automation Plan" doc. Phase 0 findings
are in [docs/phase0-findings.md](docs/phase0-findings.md).

```
Mac (India)  05:45 11:45 17:45 23:45   scripts/fetcher.sh
   fetch from data.gov.in -> push prices.json.gz to the `data` branch
GitHub       06:00 12:00 18:00 00:00   .github/workflows/prices.yml
   read the data branch -> check -> pages -> snapshot (`state` branch) -> email -> publish
```

If the Mac misses a fetch, GitHub still runs and the email says the data is
old (WARN after 2 hours, FAIL after 12). If data.gov.in rejects the key on the
Mac, the error travels with the data and the email says so. To move the fetch
to an always-on machine in India later, run `scripts/fetcher.sh install` there.

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
   of day and time (सकाळ ६:००, दुपार १२:००, संध्याकाळ ६:००), the post links to
   the district pages and the channel, and changes are marked 🟢▲ up and 🔴▼ down.
6. Saves the snapshot and sends one email: OK, WARN or FAIL, with the post
   inside. If the run crashes, it still sends a FAIL email.

## Commands

```bash
python3 -m venv .venv && ./.venv/bin/pip install --require-hashes -r requirements.txt
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
./.venv/bin/python fetch_mandi_prices.py --test-email
```

Sends one test email to check the mail settings. It calls no API and changes nothing.

```bash
scripts/fetcher.sh install
```

Starts the Mac's fetch schedule (05:45, 11:45, 17:45, 23:45). `scripts/fetcher.sh status`
shows the last push, `scripts/fetcher.sh run` fetches and pushes now, and
`scripts/fetcher.sh uninstall` removes it. A fetch missed while the Mac sleeps
happens once on wake; one missed while it is shut down is skipped.

```bash
./.venv/bin/python fetch_mandi_prices.py --fetch-only out/fetched/prices.json.gz
./.venv/bin/python fetch_mandi_prices.py --from-fetched out/fetched/prices.json.gz --no-email
```

The two halves by hand: fetch and save (what the Mac does), then process a
saved file (what GitHub does).

```bash
./.venv/bin/python -m unittest discover -s tests
```

Runs the 79 tests, with no network or key needed.

Every run saves its email as `out/email-<time>.eml` and its post as
`out/post-<time>.txt`, and adds one line to `out/runs.tsv`: prices fetched,
accepted and rejected, how many are for today and yesterday, districts with
today's prices, tracked mandis with none, what changed, and whether the email
went. Exit code 1 means the run failed or the email could not be sent.

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
- `site.base_url`: the GitHub Pages address the post links to (live from Phase 3)
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

### Setting up the mailbox (Gmail)

1. Use a Google account with 2-Step Verification turned on. A separate account
   just for the bot is safer than your own.
2. Open Google Account → Security → App passwords, create one named
   "mandi-prices", and copy the 16 characters.
3. In `.env`, set `SMTP_USER` and `MAIL_FROM` to that Gmail address,
   `SMTP_PASSWORD` to the app password, and `MAIL_TO` to where you read mail.
   `SMTP_HOST=smtp.gmail.com` and `SMTP_PORT=465` are already there.
4. Run `./.venv/bin/python fetch_mandi_prices.py --test-email`.

An app password only lets this program send mail; you can revoke it on the same
page at any time without changing your Google password.

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

## Going live on GitHub (Phase 3)

Two workflows do the work:

- `.github/workflows/prices.yml` runs the pipeline at 06:00, 12:00, 18:00 and
  00:00 IST (the tests fail if its schedule stops matching `config.yaml`), or
  by hand from the Actions tab with "post now" and a note. It keeps the
  snapshot on a separate `state` branch, so `main` only changes through
  reviewed pull requests, and publishes `site/` to GitHub Pages.
- `.github/workflows/tests.yml` runs the tests on every pull request, with a
  read-only token and no secrets.
- `.github/workflows/check-setup.yml`, started by hand, checks from GitHub's
  servers that data.gov.in is reachable, the API key is accepted and the mail
  secrets send a test email: `gh workflow run check-setup.yml -R yadgire7/mandi-prices`.

Setup, in order. Steps marked **you** happen in your GitHub or GoDaddy account.

1. **You:** create the public repository `mandi-prices` and push `main`.
2. **You:** once `.env` is complete and `--test-email` works, upload it with
   `gh secret set -f .env -R yadgire7/mandi-prices` (or add `DATA_GOV_IN_API_KEY`,
   `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`
   under Settings → Secrets and variables → Actions). Then run
   `scripts/check_setup.sh`: it checks `.env`, the API key, the mail settings and
   the GitHub secret names, and prints only OK or MISSING, never a value.
   Secrets cannot be read back, are hidden in logs and never reach pull requests.
3. **You:** Settings → Pages → Source: **GitHub Actions**.
4. ~~**You:** verify `shetkarishala.com` for GitHub Pages~~ Done 25 Nov 2026.
   Keep the `_github-pages-challenge-…` TXT record in GoDaddy's DNS for good:
   GitHub re-checks it.
5. **You:** in GoDaddy DNS add `CNAME bajarbhav → <your GitHub username>.github.io`,
   then in the repo's Settings → Pages set the custom domain to
   `bajarbhav.shetkarishala.com` and tick **Enforce HTTPS**.
6. **You:** Settings → Rules → new branch ruleset on `main`: require a pull
   request and the "Tests" check, block force pushes and deletion, bypass for
   you only. Settings → Actions → General: require approval for workflows from
   all outside contributors.
7. **You:** Settings → Code security: turn on Dependabot alerts and security updates.
8. Run "Mandi prices" once by hand from the Actions tab. The first run saves the
   baseline; check the email, the `state` branch and the page on the domain.
9. While data.gov.in refuses GitHub: keep `scripts/fetcher.sh` installed on the Mac.

GitHub turns off scheduled workflows in a public repository after 60 days
without activity and emails a warning first; re-enabling is one click in the
Actions tab. Watch for that email.

## Files

| Path | What it is |
| --- | --- |
| `fetch_mandi_prices.py` | The command that runs the pipeline once |
| `mandi/` | The pipeline: API client, checks, snapshot, pages, post, email |
| `config.yaml` | Every setting and the Marathi labels |
| `names_markets_mr.yaml` | Devanagari names for every market |
| `names_produce_mr.yaml` | The produce glossary: Marathi names for every commodity and variety |
| `probe_api.py` | The Phase 0 probe |
| `scripts/fetcher.sh` | The Mac's fetch-and-push schedule (macOS launchd) |
| `mandi/fetched.py` | The fetched-data file: written on the Mac, read and age-checked on GitHub |
| `scripts/check_setup.sh` | Checks `.env`, the API key, the mail settings and the GitHub secrets |
| `tests/` | Tests for the pipeline and the probe |
| `docs/phase0-findings.md` | What Phase 0 found |
| `.github/` | The scheduled and test workflows, and Dependabot settings |
| `state/` | The snapshot each run leaves for the next (ignored on `main`; on GitHub it lives on the `state` branch) |
| `site/`, `out/`, `data/` | Built pages, emails, the run log, probe output (all ignored by git) |
