# Distress Early-Warning Tool: Companies House Scanner + Full-Register Bulk Pass

Two-tier pipeline:

1. **Detailed pass** (`main.py`, daily): the original per-company REST
   checks, profile, officers, charges, insolvency, against your curated
   `watchlist.csv` plus a rotating batch from the bulk-flagged backlog.
2. **Bulk pass** (`bulk_scan.py`, monthly): downloads Companies House's
   free full-register snapshot (~5 million companies) and computes
   company-status and filing-overdue flags across the whole register.
   This is the only realistic way to cover the full register, the REST
   API's rate limit makes a per-company crawl of 5 million companies
   take on the order of months, and Companies House explicitly
   discourages that kind of bulk crawling through the API.

Every alert, from either pass, remains company-level only: no officer
or director names are collected or displayed anywhere in this
pipeline, see "No individual data" below.

## Why there's a rotating batch, not "scan everything daily"

The bulk pass can realistically flag tens of thousands of companies
(a few percent of 5 million is still a lot). The detailed REST checks
are still rate-limited, so processing that whole backlog in one run
isn't possible either. `main.py` instead works through it in batches
(`--max-flagged-per-run`, default 500) using a cursor file
(`data/detail_scan_cursor.json`) that rotates forward each run, so
the backlog gets covered over time rather than the job failing or
running for days. Your watchlist companies are never subject to this
cap, they're checked in full every run.

Until a flagged company's turn comes up in the detailed queue, the
dashboard still shows its bulk-level alert (status/overdue flags),
just not the officer-cluster/charges/insolvency detail yet. That's
intentional, not a bug: something is better than nothing while it
waits its turn.

## What the bulk pass does and doesn't cover

| Indicator | Bulk pass (all 5M companies) | Detailed pass (watchlist + rotating batch) |
|---|---|---|
| Company status (administration, liquidation, etc.) | Yes | Yes (fresher, live) |
| Accounts / confirmation statement overdue | Yes (approximate, from snapshot dates) | Yes (live, authoritative) |
| Officer resignation clustering | No, not in the bulk snapshot | Yes |
| New registered charges | No, not in the bulk snapshot | Yes |
| Insolvency case detail | No, not in the bulk snapshot | Yes |

Companies House does publish a separate officers bulk file, but it
is **not self-serve**: you have to individually request it from
Companies House and they email you a private download link. That's a
manual, named process, not something this pipeline can pull on a
schedule, so officer data stays confined to the detailed pass only.

## What I could not verify from this environment, flagged explicitly

I built `bulk_scan.py` against Companies House's documented file
format, but I have no network access here and could not download and
test it against a live snapshot. Specific things to check on your
first real run:

- **CompanyStatus string matching.** The script matches status values
  by case-insensitive substring (`liquidat`, `administrat`, `receiv`,
  `voluntary arrangement`, `insolvency`) rather than an exact list,
  since I couldn't confirm the precise current string values against
  live data. Check a sample of flagged rows after the first bulk run
  and tighten `WATCH_STATUS_SUBSTRINGS` in `bulk_scan.py` if it's
  over- or under-matching.
- **Confirmation statement column name.** This field has been renamed
  at least once over the life of this file (`Returns.NextDueDate` vs
  `ConfStmtNextDueDate`). The script checks both, but confirm against
  your actual downloaded header row.
- **Date format.** Assumed `DD/MM/YYYY` based on documented samples.
  If a meaningful fraction of dates fail to parse, check this against
  your real file.
- **Column name whitespace.** This file has a known, longstanding
  quirk where some header names carry a leading space. The script
  strips whitespace from headers to guard against this, but hasn't
  been tested against a live download.
- **Download size and timing.** The snapshot zip has historically been
  several hundred MB (multi-GB unzipped, ~5 million rows). I can't
  confirm current size or how long the download/parse takes on a
  GitHub-hosted runner from here, monitor the first bulk run's timing
  and adjust if it's running close to GitHub Actions' job time limit.

None of this is a reason not to run it, but treat the first real bulk
run as a genuine test, not a formality, the same way you did for the
Companies House REST setup earlier.

## Setup

1. **Companies House API key**: unchanged from before, see the
   original setup steps, `CH_API_KEY` as a repository secret.
2. **Install dependencies**: `pip install -r requirements.txt`
3. **Watchlist**: edit `watchlist.csv` as before, this is your always-
   checked curated list, independent of the bulk backlog.

## Run locally

Detailed pass only (uses whatever bulk data already exists, if any):
```
python main.py
```

Bulk pass (downloads the full register snapshot, several hundred MB,
takes some minutes):
```
python bulk_scan.py
```

Both write into `data/` and `site/data/`, see Files below.

## Run automatically via GitHub Actions, with a public dashboard

`.github/workflows/scan-and-publish.yml` now runs on two schedules:
- **Daily, 07:00 UTC**: detailed pass only (`main.py`)
- **Monthly, 1st of month, 03:00 UTC**: bulk pass (`bulk_scan.py`),
  then the detailed pass on top of the freshly updated backlog

You can also trigger a run manually from the Actions tab; there's a
`run_bulk` checkbox on the manual trigger if you want to force a bulk
run outside its monthly schedule (useful for testing).

Same one-time setup as before: `CH_API_KEY` as a repository secret,
Pages source set to "GitHub Actions" in Settings > Pages.

One change from before: **the workflow now commits files back to the
repo** (`data/flagged_companies.csv`, `data/bulk_alerts.json`,
`data/detail_scan_cursor.json`), since these need to persist between
runs, unlike `alerts.csv` and `site/data/alerts.json` which are still
fully regenerated each time and stay out of git. This needed the
workflow's `contents` permission changed from `read` to `write`,
already reflected in the workflow file.

## No individual data

Unchanged from before: no officer or director names are ever
collected or written anywhere in this pipeline. The bulk snapshot
itself contains no individual data at all (it's company-level fields
only), so the full-register pass doesn't introduce any new exposure
on that front. See `indicators.py` for where the detailed pass
enforces this.

## Files

- `ch_client.py` — REST API client, rate-limited
- `indicators.py` — detailed-pass detection logic, no individual data
- `main.py` — orchestrates watchlist + rotating bulk-backlog batch,
  merges in bulk-level alerts, writes CSV and dashboard JSON
- `bulk_scan.py` — full-register monthly bulk pass
- `watchlist.csv` — your always-checked curated list
- `data/flagged_companies.csv` — bulk-flagged companies (generated,
  committed to the repo so it persists between runs)
- `data/bulk_alerts.json` — bulk-level alerts (generated, committed)
- `data/detail_scan_cursor.json` — rotation position in the backlog
  (generated, committed)
- `requirements.txt` — dependencies
- `site/index.html`, `site/style.css`, `site/app.js` — static dashboard
- `.github/workflows/scan-and-publish.yml` — daily + monthly schedules,
  Pages deploy
- `.gitignore` — keeps `alerts.csv` and `site/data/alerts.json` out of
  version control (fully regenerated every run); does NOT exclude the
  `data/` files above, those are meant to persist in the repo
