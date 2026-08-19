"""
Full-register pass using Companies House's free monthly bulk data
product (the "Free Company Data Product"), rather than the REST API.
This is the only realistic way to cover all ~5 million companies on
the register: at the REST API's rate limit, a per-company crawl of the
whole register would take on the order of months, and Companies House
explicitly discourages that kind of bulk crawling via the API.

This script downloads and parses that snapshot and computes two
company-level indicators for every company on the register:
  - company status flags (administration, liquidation, receivership,
    voluntary arrangement)
  - accounts / confirmation statement overdue

It does NOT attempt officer resignation clustering, charges detail,
or insolvency case detail, none of those are in this bulk snapshot.
Officer bulk data exists but is not self-serve, Companies House
requires an individual, named request for it (see README). Charges
detail and insolvency case detail still require the per-company REST
API, that's what main.py's detailed pass does for the companies this
script flags.

IMPORTANT, unverified assumptions flagged here rather than hidden:
  - The exact CompanyStatus string values in the live file (e.g.
    whether it's "Liquidation" vs "In Liquidation" vs something else)
    are matched here by case-insensitive substring, not exact string,
    specifically because I could not verify the precise current values
    against a live download from this environment. Check the first
    run's output against a sample of real flagged rows and tighten the
    matching in WATCH_STATUS_SUBSTRINGS below if it's over- or
    under-matching.
  - Column names occasionally carry a leading space in this file
    (a known, longstanding quirk raised on the Companies House forum).
    This script strips whitespace from every header name to guard
    against that.
  - The confirmation statement due-date column has changed name over
    the life of this file (older "Returns.NextDueDate" vs newer
    "ConfStmtNextDueDate"). This script checks for both and uses
    whichever is present.
  - Date format in the file is DD/MM/YYYY based on documented samples;
    if parsing fails for a meaningful fraction of rows, check this
    against the actual header/sample rows in your downloaded file.
"""

import argparse
import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import requests

DOWNLOAD_BASE = "https://download.companieshouse.gov.uk"
WEB_BASE = "https://find-and-update.company-information.service.gov.uk/company"

WATCH_STATUS_SUBSTRINGS = [
    "liquidat",
    "administrat",
    "receiv",
    "voluntary arrangement",
    "insolvency",
]

DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _company_url(company_number):
    return f"{WEB_BASE}/{company_number}"


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
    """
    Builds the current month's snapshot URL. Companies House publishes
    this on the 1st of each month; if this month's file isn't up yet
    (e.g. the job runs on the 1st before their publish completes),
    falls back to the previous month.
    """
    if explicit_url:
        return explicit_url

    today = datetime.now(timezone.utc)
    candidates = []
    year, month = today.year, today.month
    candidates.append(f"{DOWNLOAD_BASE}/BasicCompanyDataAsOneFile-{year:04d}-{month:02d}-01.zip")
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
            # Strip whitespace from header names, known quirk in this file.
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            for row in reader:
                yield {k.strip() if k else k: v for k, v in row.items()}


def company_status_alert(company_number, row):
    status = (row.get("CompanyStatus") or "").strip()
    if not status:
        return None
    lowered = status.lower()
    if any(sub in lowered for sub in WATCH_STATUS_SUBSTRINGS):
        return {
            "company_number": company_number,
            "indicator": "company_status_change",
            "detail": f"Bulk snapshot reports company status as '{status}'.",
            "evidence_url": _company_url(company_number),
            "confidence": "medium",
            "detected_at": _now(),
        }
    return None


def accounts_overdue_alert(company_number, row, as_of_date):
    next_due = _parse_date(row.get("Accounts.NextDueDate"))
    if next_due and next_due.date() < as_of_date:
        return {
            "company_number": company_number,
            "indicator": "accounts_overdue",
            "detail": (
                f"Bulk snapshot shows accounts next due date "
                f"{next_due.date()}, which has passed."
            ),
            "evidence_url": _company_url(company_number) + "/filing-history",
            "confidence": "medium",
            "detected_at": _now(),
        }
    return None


def confirmation_statement_overdue_alert(company_number, row, as_of_date):
    # Column name has changed over the life of this file, check both.
    raw = row.get("ConfStmtNextDueDate") or row.get("Returns.NextDueDate")
    next_due = _parse_date(raw)
    if next_due and next_due.date() < as_of_date:
        return {
            "company_number": company_number,
            "indicator": "confirmation_statement_overdue",
            "detail": (
                f"Bulk snapshot shows confirmation statement/return next "
                f"due date {next_due.date()}, which has passed."
            ),
            "evidence_url": _company_url(company_number) + "/filing-history",
            "confidence": "medium",
            "detected_at": _now(),
        }
    return None


def run(snapshot_url, flagged_csv_path, bulk_alerts_path):
    zip_path = download_and_open_csv(resolve_snapshot_url(snapshot_url))
    as_of_date = datetime.now(timezone.utc).date()

    all_bulk_alerts = []
    flagged_numbers = {}
    total_rows = 0

    for row in iter_csv_rows(zip_path):
        total_rows += 1
        company_number = (row.get("CompanyNumber") or "").strip()
        if not company_number:
            continue

        row_alerts = []
        for alert in (
            company_status_alert(company_number, row),
            accounts_overdue_alert(company_number, row, as_of_date),
            confirmation_statement_overdue_alert(company_number, row, as_of_date),
        ):
            if alert:
                row_alerts.append(alert)

        if row_alerts:
            all_bulk_alerts.extend(row_alerts)
            flagged_numbers[company_number] = (row.get("CompanyName") or "").strip()

        if total_rows % 500000 == 0:
            print(f"  processed {total_rows:,} rows, {len(flagged_numbers):,} flagged so far")

    os.makedirs(os.path.dirname(flagged_csv_path) or ".", exist_ok=True)
    with open(flagged_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["company_number", "company_name"])
        for number, name in flagged_numbers.items():
            writer.writerow([number, name])

    os.makedirs(os.path.dirname(bulk_alerts_path) or ".", exist_ok=True)
    with open(bulk_alerts_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": _now(),
            "total_companies_in_snapshot": total_rows,
            "alerts": all_bulk_alerts,
        }, f, indent=2)

    print(
        f"\nDone. {total_rows:,} companies in snapshot, "
        f"{len(flagged_numbers):,} flagged, "
        f"{len(all_bulk_alerts):,} total bulk alerts written."
    )
    print(f"Flagged company list: {flagged_csv_path}")
    print(f"Bulk alerts: {bulk_alerts_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Companies House full-register bulk scan")
    parser.add_argument("--snapshot-url", default=None, help="Override the auto-resolved snapshot URL")
    parser.add_argument("--flagged-csv", default="data/flagged_companies.csv")
    parser.add_argument("--bulk-alerts", default="data/bulk_alerts.json")
    args = parser.parse_args()
    run(args.snapshot_url, args.flagged_csv, args.bulk_alerts)
