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
- `data/flagged_companies.csv` — bulk pre-filter candidates (generated, committed)
- `data/known_alerts.json` — cumulative confirmed alerts (generated, committed)
- `data/detail_scan_cursor.json` — rotation position (generated, committed)
- `requirements.txt` — dependencies
- `site/index.html`, `site/style.css`, `site/app.js` — static dashboard
- `.github/workflows/scan-and-publish.yml` — daily + monthly schedules, Pages deploy
- `.gitignore` — keeps `alerts.csv` and `site/data/alerts.json` out of
  version control (fully regenerated every run); does NOT exclude the
  `data/` files above, those are meant to persist in the repo
