# Distress Early-Warning Tool: Companies House Scanner + Full-Register Pre-Filter

Two-tier pipeline:

1. **Bulk pre-filter** (`bulk_scan.py`, monthly): downloads Companies
   House's free full-register snapshot (~5 million companies) and
   narrows it down to a compact candidate list, companies overdue on
   filings by more than 90 days, or showing a distress-adjacent status
   (administration, liquidation, receivership, voluntary arrangement).
   This produces only a company-number list, no alerts, see "Why the
   design changed" below.
2. **Detailed pass** (`main.py`, daily): the authoritative, live REST
   checks (profile, officers, charges, insolvency) against your curated
   `watchlist.csv` (always fully checked) plus a rotating daily batch
   from the bulk pre-filter's candidate list.

Every alert remains company-level only: no officer or director names
are collected or displayed anywhere in this pipeline, see "No
individual data" below.

## Why the design changed after the first real bulk run

The first version of `bulk_scan.py` generated its own alerts directly
from the bulk snapshot (status + "overdue at all") and tried to commit
a full alerts file to the repo. Against live data this produced two
real problems:

- **~330,000 candidates** (roughly 6% of the entire register) using an
  "overdue at all" rule. Minor filing lag is common across the UK
  register and isn't a distinctive distress signal on its own, that
  rule was too broad to be useful.
- The resulting alerts file was **552MB**, over GitHub's 100MB
  per-file commit limit, so it couldn't even be committed.

Fixed by:
- `bulk_scan.py` now only flags a company as a candidate if overdue by
  more than `OVERDUE_THRESHOLD_DAYS` (default 90), not merely overdue.
  This is a judgement call, not a verified-correct number, watch the
  printed candidate count on your first run under this version and
  adjust if it's still too large (or suspiciously small).
- `bulk_scan.py` no longer generates alerts at all, just a compact
  candidate list (company number, name, and short reason codes for
  your own reference). The real alert logic already exists in
  `indicators.py` and runs live against the REST API during the
  detailed pass, duplicating it in the bulk pass was redundant and,
  at this scale, produced an unmanageably large file.
- Since the detailed pass now only checks a rotating batch each day,
  not the whole backlog, `main.py` maintains a small persistent store
  (`data/known_alerts.json`) of the latest confirmed alert per company.
  Only companies checked THIS run get their entry refreshed, everyone
  else keeps their last known result, so the dashboard reflects
  cumulative findings, not just today's batch. Companies that come
  back clean on a re-check have their entry removed, keeping this
  file's size bounded to roughly the number of companies genuinely
  worth attention, not the full candidate backlog.

## What the bulk pre-filter does and doesn't cover

| Indicator | Bulk pre-filter (candidate selection only) | Detailed pass (authoritative) |
|---|---|---|
| Company status (administration, liquidation, etc.) | Used to select candidates | Confirmed live, this is what actually reaches the dashboard |
| Accounts / confirmation statement overdue, 90+ days | Used to select candidates | Confirmed live |
| Officer resignation clustering | Not available in the bulk snapshot | Yes |
| New registered charges | Not available in the bulk snapshot | Yes |
| Insolvency case detail | Not available in the bulk snapshot | Yes |

Companies House does publish a separate officers bulk file, but it's
not self-serve, you have to individually request it and they email a
private download link. Not something this pipeline can pull on a
schedule, so officer data stays confined to the live detailed pass.

## What I still can't fully verify from this environment

- **90-day threshold correctness.** Chosen as a reasonable-sounding
  cutoff, not derived from real candidate-count data, since I don't
  have network access to test it against a live download. Treat the
  printed candidate count on your next bulk run as the real test.
- **CompanyStatus string matching.** Still substring-based
  (`liquidat`, `administrat`, `receiv`, etc.), current live values
  not independently confirmed beyond what the first run's data implied.
- **Confirmation statement column name.** Your first successful bulk
  run's data suggests the current file uses `ConfStmtNextDueDate`
  rather than the older `Returns.NextDueDate`, both are still checked
  as a fallback.
- **Whether 90 days brings the candidate count to a workable size.**
  Unverified until you run this version for real. If it's still too
  large for the batch rotation to cycle through in reasonable time,
  either raise the threshold further or raise `--max-flagged-per-run`.

## Prioritizing by company size

Turnover itself often isn't on the public record at all, UK small and
micro-entity accounts filing exemptions mean most private companies
don't disclose a profit and loss account. As a proxy, `bulk_scan.py`
uses `Accounts.AccountCategory` from the bulk snapshot (GROUP, FULL,
MEDIUM, SMALL, MICRO ENTITY, DORMANT, etc.) to rank candidates and
writes `data/flagged_companies.csv` in largest-company-first order.
The ranking (`ACCOUNT_CATEGORY_PRIORITY` in `bulk_scan.py`) is based on
documented category values, not independently verified against a live
download, check the `account_category` column in your actual output
and adjust the ranking if any category ranks higher or lower than you'd
expect.

**Important nuance on what this actually changes:** since the detailed
pass rotates through the whole candidate list on a fixed cycle, size
priority determines *when within each cycle* a company gets checked
(bigger companies earlier), not how *often* it gets checked overall,
every company still gets one check per full cycle through the backlog,
regardless of size. If you want large companies checked more
frequently than small ones, not just earlier, that's a different,
weighted-rotation design, not what's built here, say so if that's
actually what you need.

## Prioritizing LSE-listed (FTSE All-Share proxy) companies

FTSE Russell's actual index membership wasn't something I could pull
automatically with confidence, their factsheet is only refreshed
within ~2 months of each quarterly review and I couldn't verify their
terms permit scripted downloading. Instead, this uses LSE's own
Issuer List (manually downloaded from
londonstockexchange.com/reports?tab=issuers, no stable automatable
URL was found there either, the page is a JavaScript app) as a proxy:
Main Market listing, filtered to UK-incorporated companies only,
since only those have Companies House numbers. From the July 2026
list this gave 649 companies, close to FTSE All-Share's real ~600-640
constituent count, a reasonable but not exact stand-in. It excludes
253 Main Market companies incorporated in Guernsey, Jersey, BVI,
Bermuda, and similar jurisdictions, common for investment trusts and
REITs, that genuinely can't be checked against Companies House at all.

**This is a manual, periodic process, not automated in the GitHub
Actions workflow**, since the source file has to be downloaded by
hand:

1. Download the current Issuer List from
   londonstockexchange.com/reports?tab=issuers, save it as
   `data/lse_issuer_list.xlsx`
2. Run `python lse_priority.py`, this calls the Companies House
   search API (~650 calls, a few minutes) to match each issuer name to
   a company number, writing `data/lse_priority_companies.csv`
3. Check the `needs_review` rows in that output. Matching works by
   normalizing both names (uppercase, strip PLC/LIMITED/LTD suffixes)
   and requiring an exact match, anything that didn't match exactly
   falls back to the top search result but is flagged, not
   auto-trusted, since a wrong match means checking the wrong company
   entirely. `main.py` only includes `exact` matches by default.
4. Commit the resulting `data/lse_priority_companies.csv`, main.py
   picks it up automatically from there, treated like `watchlist.csv`:
   always fully checked every run, no batching, no cap.

There's no fixed refresh schedule for this, LSE's issuer list doesn't
change daily. Re-run the two steps above whenever you want to refresh
it, quarterly is probably reasonable given how infrequently
constituent lists change in practice.

## Setup

Unchanged: `CH_API_KEY` as a repository secret, `watchlist.csv` for
your always-checked curated list, Pages source set to GitHub Actions.

## Run locally

Detailed pass only (uses whatever candidate list already exists):
```
python main.py
```

Bulk pre-filter (downloads the full register snapshot, several hundred
MB, takes some minutes):
```
python bulk_scan.py
```

## Run automatically via GitHub Actions, with a public dashboard

Same two schedules as before:
- **Daily, 07:00 UTC**: detailed pass only
- **Monthly, 1st of month, 03:00 UTC**: bulk pre-filter, then detailed pass

Manual trigger from the Actions tab has a `run_bulk` checkbox to force
a bulk run outside its monthly schedule.

The workflow commits three small files back to the repo between runs:
`data/flagged_companies.csv` (candidate list), `data/known_alerts.json`
(cumulative confirmed alerts), `data/detail_scan_cursor.json` (rotation
position). None of these should approach GitHub's commit size limit
under this design, unlike the retired `bulk_alerts.json`. Still worth
keeping an eye on `data/known_alerts.json`'s size over time as more of
the backlog gets its first check.

## No individual data

Unchanged: no officer or director names are ever collected or written
anywhere in this pipeline. The bulk snapshot itself contains no
individual data at all. See `indicators.py` for where the detailed
pass enforces this.

## Files

- `ch_client.py` — REST API client, rate-limited
- `indicators.py` — detailed-pass detection logic, no individual data
- `main.py` — orchestrates watchlist + rotating candidate batch,
  maintains the cumulative known-alerts store, writes CSV and dashboard JSON
- `bulk_scan.py` — full-register candidate pre-filter, no alerts generated
- `watchlist.csv` — your always-checked curated list
- `lse_priority.py` — matches LSE Main Market/UK-incorporated issuers
  to Companies House numbers (manual, periodic, see above)
- `data/lse_issuer_list.xlsx` — source file you download by hand (not
  committed unless you choose to, add to .gitignore if you'd rather not)
- `data/lse_priority_companies.csv` — matched output (generated,
  committed), treated like watchlist.csv by main.py
- `data/flagged_companies.csv` — bulk pre-filter candidates (generated, committed)
- `data/known_alerts.json` — cumulative confirmed alerts (generated, committed)
- `data/detail_scan_cursor.json` — rotation position (generated, committed)
- `requirements.txt` — dependencies
- `site/index.html`, `site/style.css`, `site/app.js` — static dashboard
- `.github/workflows/scan-and-publish.yml` — daily + monthly schedules, Pages deploy
- `.gitignore` — keeps `alerts.csv` and `site/data/alerts.json` out of
  version control (fully regenerated every run); does NOT exclude the
  `data/` files above, those are meant to persist in the repo
