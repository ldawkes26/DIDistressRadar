"""
Full-register PRE-FILTER using Companies House's free monthly bulk data
product (the "Free Company Data Product"), rather than the REST API.

Important design point, changed after the first real run: this script
does NOT generate alerts. Its only job is to cheaply narrow ~5 million
companies down to a smaller candidate list worth checking with the
authoritative, live REST API (main.py / indicators.py already compute
status and overdue-filing alerts from live data during the detailed
pass, duplicating that logic here would be redundant and, as the first
real run showed, produces a candidate set far too large to be useful
or to store).

First real run against live data flagged roughly 330,000 companies
(~6% of the register) using an "any overdue at all" rule, and the
resulting alerts file was 552MB, over GitHub's 100MB commit limit and,
more importantly, too broad to be a meaningful distress signal (minor
filing lag is common and not very distinctive on its own). This
version:
  - only counts a company as a candidate if it is overdue by more than
    OVERDUE_THRESHOLD_DAYS (default 90), not merely overdue at all
  - writes only a compact CSV of candidate company numbers/names/reason
    codes, no verbose per-alert JSON

Still unverified against live data, flag for your first real run after
this change:
  - Whether 90 days is the right threshold to get a workable-sized
    candidate list. Watch the printed candidate count and adjust
    OVERDUE_THRESHOLD_DAYS if it's still too large (or too small) for
    the batch size in main.py to work through in a reasonable time.
  - CompanyStatus string matching is still substring-based, since exact
    current values weren't verified. Now confirmed against live data:
    the confirmation-statement column was ConfStmtNextDueDate in this
    run's file (per the successful bulk run), not the older
    Returns.NextDueDate name, worth noting for anyone reading this later.
"""

import argparse
import csv
import io
import os
import sys
import zipfile
from datetime import datetime, timezone

import requests

DOWNLOAD_BASE = "https://download.companieshouse.gov.uk"

WATCH_STATUS_SUBSTRINGS = [
    "liquidat",
    "administrat",
    "receiv",
    "voluntary arrangement",
    "insolvency",
]

# Only count a company as a bulk-flagged candidate if overdue by more
# than this many days, not merely overdue at all. Tune based on the
# printed candidate count on your first real run.
OVERDUE_THRESHOLD_DAYS = 90

DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d")

LARGE_CANDIDATE_WARNING_THRESHOLD = 50000


def _parse_date(value):
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def resolve_snapshot_url(explicit_url=None):
    if explicit_url:
        return explicit_url
    today = datetime.now(timezone.utc)
    year, month = today.year, today.month
    candidates = [f"{DOWNLOAD_BASE}/BasicCompanyDataAsOneFile-{year:04d}-{month:02d}-01.zip"]
    prev_month = month - 1 or 12
    prev_year = year if month > 1 else year - 1
    candidates.append(f"{DOWNLOAD_BASE}/BasicCompanyDataAsOneFile-{prev_year:04d}-{prev_month:02d}-01.zip")
    return candidates


def download_and_open_csv(url_or_candidates, dest_zip="bulk_snapshot.zip"):
    urls = url_or_candidates if isinstance(url_or_candidates, list) else [url_or_candidates]
    last_error = None
    for url in urls:
        try:
            print(f"Trying snapshot URL: {url}")
            response = requests.get(url, stream=True, timeout=120)
            response.raise_for_status()
            with open(dest_zip, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            print(f"Downloaded {os.path.getsize(dest_zip)} bytes from {url}")
            return dest_zip
        except requests.RequestException as e:
            last_error = e
            print(f"  Failed: {e}", file=sys.stderr)
    raise RuntimeError(f"Could not download a snapshot from any candidate URL: {last_error}")


def iter_csv_rows(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError("No CSV file found inside the downloaded snapshot zip.")
        with zf.open(csv_names[0]) as raw:
            text_stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            reader = csv.DictReader(text_stream)
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            for row in reader:
                yield {k.strip() if k else k: v for k, v in row.items()}


def status_reason(row):
    status = (row.get("CompanyStatus") or "").strip()
    if status and any(sub in status.lower() for sub in WATCH_STATUS_SUBSTRINGS):
        return f"status:{status}"
    return None


def accounts_overdue_reason(row, as_of_date):
    next_due = _parse_date(row.get("Accounts.NextDueDate"))
    if next_due and (as_of_date - next_due.date()).days > OVERDUE_THRESHOLD_DAYS:
        return f"accounts_overdue:{next_due.date()}"
    return None


def confirmation_statement_overdue_reason(row, as_of_date):
    raw = row.get("ConfStmtNextDueDate") or row.get("Returns.NextDueDate")
    next_due = _parse_date(raw)
    if next_due and (as_of_date - next_due.date()).days > OVERDUE_THRESHOLD_DAYS:
        return f"confstmt_overdue:{next_due.date()}"
    return None


def run(snapshot_url, flagged_csv_path):
    zip_path = download_and_open_csv(resolve_snapshot_url(snapshot_url))
    as_of_date = datetime.now(timezone.utc).date()

    total_rows = 0
    candidates = []  # (company_number, company_name, reasons_str)

    for row in iter_csv_rows(zip_path):
        total_rows += 1
        company_number = (row.get("CompanyNumber") or "").strip()
        if not company_number:
            continue

        reasons = [
            r for r in (
                status_reason(row),
                accounts_overdue_reason(row, as_of_date),
                confirmation_statement_overdue_reason(row, as_of_date),
            ) if r
        ]

        if reasons:
            company_name = (row.get("CompanyName") or "").strip()
            candidates.append((company_number, company_name, ";".join(reasons)))

        if total_rows % 500000 == 0:
            print(f"  processed {total_rows:,} rows, {len(candidates):,} candidates so far")

    os.makedirs(os.path.dirname(flagged_csv_path) or ".", exist_ok=True)
    with open(flagged_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["company_number", "company_name", "bulk_reasons"])
        writer.writerows(candidates)

    print(f"\nDone. {total_rows:,} companies in snapshot, {len(candidates):,} candidates written.")
    print(f"Candidate list: {flagged_csv_path}")
    if len(candidates) > LARGE_CANDIDATE_WARNING_THRESHOLD:
        print(
            f"\nWARNING: {len(candidates):,} candidates exceeds the sanity threshold of "
            f"{LARGE_CANDIDATE_WARNING_THRESHOLD:,}. At the default batch size in main.py, "
            f"this backlog will take a long time to cycle through. Consider raising "
            f"OVERDUE_THRESHOLD_DAYS in this script, or raising --max-flagged-per-run in "
            f"main.py, or both.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Companies House full-register bulk pre-filter")
    parser.add_argument("--snapshot-url", default=None, help="Override the auto-resolved snapshot URL")
    parser.add_argument("--flagged-csv", default="data/flagged_companies.csv")
    args = parser.parse_args()
    run(args.snapshot_url, args.flagged_csv)
