# Phase 0 findings

Checked on 25 Nov 2026 from this laptop, before the data.gov.in API key
exists. The probe (`probe_api.py`) answers the rest once the key is in place.

## Settled

**data.gov.in answers from this laptop.** The API replied `HTTP 400
"Authorization field missing"` to a request without a key, which means it
is reachable and not blocking this connection. Its website pages also
loaded (HTTP 200). An earlier HTTP 403 came from a cloud web fetcher, not
from this laptop, so GitHub's servers still need their own test in Phase 3.

**The historical, variety-wise resource exists.** "Variety-wise Daily Market
Prices Data of Commodity", resource `35985678-0d79-46b4-9ed6-6f13308a1d24`.
Its listed fields are State, District, Market, Commodity, Variety, Grade,
Arrival_Date, Min_Price, Max_Price, Modal_Price and Commodity_Code. The
"current daily price" resource (`9ef84268-d588-465a-a308-a864a43d0070`)
does not list its fields on its page; the probe reads them.

**Exact AGMARKNET names, from the portal's public filter list.** The portal
lists 609 commodities and 4,176 markets, 429 of them in Maharashtra. Two
names differ from the design doc, and data.gov.in may still use older
spellings, so `config.yaml` keeps candidates until the probe confirms them.

| Design doc | AGMARKNET 2.0 name |
| --- | --- |
| Nashik | APMC Nasik (spelled "Nasik") |
| Nashik - Lasalgaon | APMC Lasalgaon |
| Ahmednagar | APMC Ahilyanagar |
| Mumbai (Vashi) | APMC Mumbai |
| Latur | APMC  Latur (two spaces in the source) |
| Pune, Akola, Yavatmal, Solapur, Sangli, Jalgaon, Nagpur | APMC + the city name |
| Soybean | Soyabean |
| Tur | Red gram/Arhar/Tur(whole) |
| Chana | Bengal Gram(Gram)(Whole) |
| Jowar | Jowar(Sorghum) |
| Onion, tomato, grapes, cotton, wheat, pomegranate, turmeric, maize, banana, orange | Same as the design doc |

## Changes the plan: the portal scraper

The plan's fallback was to scrape the AGMARKNET portal when the API fails.
What the portal shows:

- `robots.txt` allows public pages and disallows sign-in, data entry and
  admin pages.
- The portal is a JavaScript app backed by its own data service at
  `api.agmarknet.gov.in/v1`. Its filter list (names and ids) is public JSON.
- **The Daily Price and Arrival Report form requires a CAPTCHA** before it
  returns prices.

A CAPTCHA is the site saying it does not want automated access to that
report. The scraper cannot get past it, and should not try. Using the
portal's internal data service to skip the CAPTCHA would sidestep the same
intent. That needs the publisher's permission, which we do not have.

Options for the fallback, for you to choose:

1. **Ask AGMARKNET for official access.** Write to the Directorate of
   Marketing and Inspection, which runs AGMARKNET, and explain the
   farmer-facing use. If they grant API access, that becomes the fallback.
2. **Use the other data.gov.in resource.** If one resource is empty or
   stale, read the other. This does not help when the whole data.gov.in API
   is down.
3. **No automatic fallback.** The run sends a FAIL email, and the moderator
   checks the portal by hand (the design doc's original plan).

Recommendation: 1 and 2, with 3 as the floor until access is granted.

## Probe results, 25 Nov 2026 at 11:27

Full report: `data/phase0/2026-11-25_1127/report.md` (17 requests, 30 s).

**Read the variety-wise resource in Phase 1.** The "current" resource is a
one-day snapshot: it held only 31 Maharashtra rows, all dated 25 Nov. The
variety-wise resource filters cleanly by state (`filters[State]`) and by
date (`filters[Arrival_Date]`, as `dd/mm/yyyy`), and holds every recent day.

| Day | Maharashtra rows |
| --- | --- |
| Wed 25 Nov | 0 |
| Tue 24 Nov | 537 (still filling in) |
| Mon 23 Nov | 1,434 |
| Sun 22 Nov | 716 |
| Sat 21 Nov | 1,185 |
| Fri 20 Nov | 1,389 |
| Thu 19 Nov | 1,649 |

**The data arrives late and fills in over a day or two.** At 11:27 on
25 Nov, the 24th had about a third of a normal day's rows. The 23rd's 1,434
rows match the portal's Market-wise report for that date exactly, so both
come from the same AGMARKNET reporting. A 07:00 post will mostly carry
prices from two days earlier, with some from the day before. The freshness
rule allows that, and the post shows each price's date. A later post time
would catch more of the previous day; that is a decision for you.

**Every configured name matched**, after ignoring case and spaces: the
source writes "APMC Lasalgaon " with a trailing space. `config.yaml` now
keeps only the confirmed names.

**Five pairs had no price on any of the 7 days:**

| Pair | Markets that did report it, 19-25 Nov |
| --- | --- |
| Lasalgaon tomato | 43 markets, e.g. Pune(Manjri), Pune(Moshi), APMC Sangamner (6 days each) |
| Nashik grapes | Pune(Moshi) 4 days, APMC Nagpur 3, APMC Pune 2 |
| Yavatmal cotton | 57 markets, e.g. a Pandharkawada private market in Yavatmal district (5 days) |
| Jalgaon banana | APMC Nasik ("Khandesh" variety) 5 days, Pune(Moshi) 6, APMC Yawal in Jalgaon district 2 |
| Jalgaon cotton | 57 markets, mostly in Nagpur, Wardha and Akola districts |

Each can be dropped, or moved to a market that reports it. Season may
explain some of these, for example grapes, but this one week cannot show it.

**Proposed variety per pair** (the one reported on the most days; where two
tie, the one with more arrivals in the portal's 23 Nov report):

| Mandi | Crop | Proposed variety / grade | Also reported |
| --- | --- | --- | --- |
| Lasalgaon | Onion | Red / Local | Unhali / Local |
| Nashik | Onion | Unhali / Local | |
| Pune | Tomato | Local / Local | |
| Pune | Onion | Local / Local | |
| Akola | Soybean | Yellow / FAQ | |
| Akola | Cotton | Desi / Local | |
| Akola | Tur | Other / FAQ | Other / Non-FAQ |
| Akola | Chana | Other / FAQ | Kabul Small; Non-FAQ grades |
| Akola | Wheat | Other / FAQ | Sharbati / FAQ |
| Latur | Soybean | Yellow / FAQ | |
| Latur | Tur | Other / FAQ | |
| Latur | Chana | Other / FAQ | |
| Yavatmal | Soybean | Yellow / FAQ | |
| Solapur | Pomegranate | Other / Local | |
| Solapur | Jowar | Other / FAQ | |
| Sangli | Turmeric | Rajapuri / Local | |
| Sangli | Jowar | Other / FAQ | Other / Local (about twice the price) |
| Sangli | Maize | Other / FAQ | |
| Ahilyanagar | Onion | Red / Local | Unhali / Local |
| Ahilyanagar | Pomegranate | Other / Local | |
| Ahilyanagar | Wheat | Other / FAQ | |
| Nagpur | Soybean | Other / Local | |
| Nagpur | Tur | Other / FAQ | |
| Nagpur | Orange | Other / Local | |

Onion is the one crop where farmers may want both varieties shown, since
Red and Unhali (stored summer onion) trade at different prices.

**Mumbai (APMC Mumbai)** reports mostly vegetables, pulses and fruit: green
peas, cabbage, brinjal, green chilli, bitter gourd, coconut and more. Its
specific crops are still to choose.

## Still open

- Fallback when data.gov.in fails (see above).
- The five gap pairs: drop, or move to another market.
- Confirm the proposed varieties, and whether onion shows both.
- Mumbai's crops.
- Whether 07:00 stays the post time, given the reporting delay.
