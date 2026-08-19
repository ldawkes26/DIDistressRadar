# Distress Early-Warning Tool, Phase 1: Companies House Scanner

Working prototype covering the free, UK-native part of the architecture
document (Section 3, Tier 1). Pulls company profile, officer, charges,
and insolvency data from the Companies House API for a watchlist of
companies and flags a first set of distress indicators.

Runs automatically on a schedule via GitHub Actions and publishes
results to a static public dashboard (GitHub Pages). Output is
company-level only: no officer or director names are collected or
displayed anywhere in this pipeline, see the "No individual data"
section below.

## What this phase covers

From the Section 2 taxonomy:

- Accounts overdue / confirmation statement overdue (filing default)
- Company status change (administration, liquidation, receivership,
  voluntary arrangement, insolvency proceedings)
- Officer resignation clustering (2+ resignations within 90 days,
  both window and cluster size are configurable in `indicators.py`)
- New registered charges within the last 90 days
- Open insolvency cases

## What this phase does NOT cover yet (later phases)

- Auditor resignation / qualified or going-concern audit opinions:
  these require parsing the text of filed accounts documents, not
  just filing metadata. Companies House returns a document link per
  filing; extracting and reading that text (PDF or iXBRL) is phase 2.
- Insolvency notices from The Gazette (separate API/data source)
- Regulatory disclosures from RNS / the FCA National Storage Mechanism
  (profit warnings, trading updates, listing suspensions, PDMR dealing)
- Financial ratio analysis and debt maturity (would need a separate
  financial data source, not in scope for this build)
- News monitoring
- The dashboard/alerting layer and audit trail described in the
  architecture document Section 4, layers 5 and 6. This phase writes
  a flat CSV; a persistent store and review workflow come later.

## Setup

1. **Get a Companies House API key** (free):
   - Register at https://developer.company-information.service.gov.uk/manage-applications
   - Create an application, generate a key

2. **Install dependencies** (Python 3.9+):
   ```
   pip install -r requirements.txt
   ```

3. **Set your API key as an environment variable** (do not hardcode it
   anywhere, and never commit it to a repository):
   ```
   export CH_API_KEY="your_key_here"      # macOS/Linux
   setx CH_API_KEY "your_key_here"        # Windows (new terminal needed after)
   ```

4. **Build your watchlist**: edit `watchlist.csv` with the company
   numbers you want to monitor (find these via the Companies House
   website search, or the `search_companies` method in `ch_client.py`).
   The sample row is a placeholder, replace it before running.

## Run locally

```
python main.py --watchlist watchlist.csv --output alerts.csv --json-output site/data/alerts.json
```

Console output shows progress per company. Results are written to two
places with identical content: `alerts.csv` (for local review) and
`site/data/alerts.json` (consumed by the dashboard). Columns/fields:
`company_number`, `indicator`, `detail`, `evidence_url`, `confidence`,
`detected_at`. Every row links back to the Companies House record it
came from, consistent with the "citation, not conclusion" principle in
the architecture document.

## Run automatically via GitHub Actions, with a public dashboard

The workflow at `.github/workflows/scan-and-publish.yml` runs the scan
daily (07:00 UTC by default, edit the cron line to change it) and
publishes `site/` to GitHub Pages. Two things to set up once, after
pushing this repo to GitHub:

1. **Add your API key as a repository secret**, not a file:
   Settings → Secrets and variables → Actions → New repository secret,
   name it `CH_API_KEY`, paste the key value. The workflow reads it
   from there, it is never written to any file in the repo.

2. **Enable GitHub Pages from GitHub Actions**:
   Settings → Pages → Source → select "GitHub Actions". No further
   configuration needed, the workflow's `deploy-pages` step handles
   the rest.

You can also trigger a run manually any time from the Actions tab
(the workflow has `workflow_dispatch` enabled), useful for testing
before waiting for the first scheduled run.

The workflow also uploads `alerts.csv` as a workflow artifact (90-day
retention) as a convenience for analyst review, in addition to
publishing the same data to the dashboard.

## No individual data

This build deliberately does not collect or display officer or
director names anywhere, including in the officer-resignation-cluster
indicator, which reports only a count and date range. This matters
more than it did for a private/internal-only version of this tool
since the dashboard is public: see `indicators.py` for where this is
enforced in code. If you extend the detectors, keep that boundary,
company-level signals only, no individual-level output.

Two separate points remain worth keeping in mind even with names
removed, neither of which this code can resolve for you:
- The underlying Companies House data itself (officers, PSC register)
  is more granular than most jurisdictions' equivalents. This code
  only reads it to compute a company-level signal and discards names
  before writing output, it does not persist or display the
  underlying individual-level records.
- A public "distress flag" against a named company, even sourced
  entirely from public filings, is a stronger public statement than a
  private analyst-review alert. That was your call to make and you've
  made it, flagging it here only so it's written down alongside the
  rest of this document's assumptions.

## Files

- `ch_client.py` — API client, handles auth and rate limiting (throttled
  to stay under the published 600 requests/5 min Companies House limit)
- `indicators.py` — detection logic, one function per indicator, no
  individual-level data collected or output
- `main.py` — orchestrates the watchlist scan, writes CSV and dashboard JSON
- `watchlist.csv` — your list of companies to monitor
- `requirements.txt` — dependencies
- `site/index.html`, `site/style.css`, `site/app.js` — static dashboard,
  reads `site/data/alerts.json` at load time
- `.github/workflows/scan-and-publish.yml` — scheduled scan + Pages deploy
- `.gitignore` — keeps `alerts.csv` and the generated `site/data/alerts.json`
  out of version control (regenerated each run, not source code)

## Before operational use

This is a first-phase prototype, not yet reviewed against A&M's IT
security or legal/compliance requirements. Two points from the
architecture document apply directly to this code:

- **Individual data handling (Section 7):** the officers endpoint
  returns identifiable individuals. This build only persists what is
  needed for the resignation-clustering indicator (name, resignation
  date) and does not build any standing individual-level profile. If
  you extend this, keep that boundary.
- **Human review (Section 4, layer 6):** this script produces a flagged
  alert feed only. Nothing here escalates automatically. Route
  `alerts.csv` to analyst review before any further action.

## Suggested next build step

Given the phase-2 list above, the highest-value next addition is
probably The Gazette insolvency notice feed, since it is free, UK-native,
and covers a distress category (administration/CVA/winding-up) this
phase only partially catches via company status. Auditor-opinion and
going-concern text extraction is the more technically involved next
step (requires document parsing), so I'd sequence that after Gazette
integration unless you'd prioritize differently.
