"""
Runs the detailed per-company Companies House checks (profile, officers,
charges, insolvency) for two combined sources of companies:

  1. watchlist.csv - your manually curated list, always fully checked
     every run, no cap.
  2. data/flagged_companies.csv - produced by bulk_scan.py from the
     full-register monthly snapshot. This list can be large (a few
     percent of 5 million companies is still tens of thousands), so
     it is NOT fully processed every run. Instead this script works
     through it in rotating daily batches, tracked by a cursor file,
     so the whole backlog gets covered over several runs rather than
     either failing outright or taking days to complete in one job.

Final output merges three things into one alerts feed:
  - detailed alerts from this run's per-company checks (indicators.py)
  - bulk-level alerts from the most recent bulk_scan.py run, if present
    (data/bulk_alerts.json), for companies not yet reached by the
    detailed pass, so a flagged company still shows *something* on
    the dashboard even before its turn in the detailed queue comes up
  - nothing is ever duplicated: if a company has both a bulk alert and
    a fresher detailed-pass alert for the same indicator, only kept once

Usage:
    export CH_API_KEY="your_key_here"
    python main.py

All output remains company-level only, no officer or individual names
are collected or written anywhere in this pipeline, see indicators.py.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from ch_client import CompaniesHouseClient
from indicators import run_all_detectors


def load_company_csv(path):
    """Returns an ordered list of company numbers from a CSV with a
    company_number column. Returns an empty list if the file doesn't
    exist, callers treat that as optional input, not an error."""
    if not os.path.exists(path):
        return []
    companies = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            number = (row.get("company_number") or "").strip()
            if number:
                companies.append(number)
    return companies


def load_cursor(path):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("next_index", 0)
    except (json.JSONDecodeError, OSError):
        return 0


def save_cursor(path, next_index):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"next_index": next_index, "updated_at": datetime.now(timezone.utc).isoformat()}, f)


def select_batch(flagged_companies, cursor_path, batch_size):
    """Rotates through flagged_companies, batch_size at a time, wrapping
    around to the start once the end of the list is reached. Returns the
    batch and the new cursor position to persist for next run."""
    if not flagged_companies or batch_size <= 0:
        return [], 0

    start = load_cursor(cursor_path) % len(flagged_companies)
    end = start + batch_size
    if end <= len(flagged_companies):
        batch = flagged_companies[start:end]
        next_index = end % len(flagged_companies)
    else:
        batch = flagged_companies[start:] + flagged_companies[: end - len(flagged_companies)]
        next_index = end - len(flagged_companies)
    return batch, next_index


def load_bulk_alerts(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("alerts", [])
    except (json.JSONDecodeError, OSError):
        return []


def process_company(client, company_number):
    profile = client.company_profile(company_number)
    officers_response = client.officers(company_number)
    charges_response = client.charges(company_number)
    insolvency_response = client.insolvency(company_number)
    return run_all_detectors(
        company_number, profile, officers_response, charges_response, insolvency_response
    )


def main():
    parser = argparse.ArgumentParser(description="Companies House distress indicator scan")
    parser.add_argument("--watchlist", default="watchlist.csv")
    parser.add_argument("--flagged-csv", default="data/flagged_companies.csv")
    parser.add_argument("--bulk-alerts", default="data/bulk_alerts.json")
    parser.add_argument("--cursor-file", default="data/detail_scan_cursor.json")
    parser.add_argument("--max-flagged-per-run", type=int, default=500,
                         help="Cap on how many bulk-flagged companies get the detailed "
                              "REST check per run. Watchlist companies are never capped.")
    parser.add_argument("--output", default="alerts.csv")
    parser.add_argument("--json-output", default="site/data/alerts.json")
    args = parser.parse_args()

    try:
        client = CompaniesHouseClient()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    watchlist_companies = load_company_csv(args.watchlist)
    flagged_companies = load_company_csv(args.flagged_csv)

    batch, next_cursor = select_batch(flagged_companies, args.cursor_file, args.max_flagged_per_run)

    # Always check every watchlist company, plus this run's batch from the
    # bulk-flagged backlog. De-duplicate, preserving order, watchlist first.
    seen = set()
    companies_to_check = []
    for number in watchlist_companies + batch:
        if number not in seen:
            seen.add(number)
            companies_to_check.append(number)

    if not companies_to_check:
        print("No companies to check (empty watchlist and no flagged backlog yet).", file=sys.stderr)

    detailed_alerts = []
    for company_number in companies_to_check:
        print(f"Checking {company_number}...")
        try:
            alerts = process_company(client, company_number)
        except Exception as e:
            print(f"  Error retrieving data for {company_number}: {e}", file=sys.stderr)
            continue
        if alerts:
            print(f"  {len(alerts)} alert(s) found.")
        detailed_alerts.extend(alerts)

    if flagged_companies:
        save_cursor(args.cursor_file, next_cursor)
        print(
            f"\nBulk-flagged backlog: {len(flagged_companies):,} companies total, "
            f"{len(batch):,} checked this run, next run resumes at index {next_cursor}."
        )

    # Merge in bulk-level alerts for companies not covered by this run's
    # detailed pass, so they still show something on the dashboard rather
    # than nothing while they wait their turn in the batch rotation.
    bulk_alerts = load_bulk_alerts(args.bulk_alerts)
    detailed_company_numbers = {a["company_number"] for a in detailed_alerts}
    supplementary_bulk_alerts = [
        a for a in bulk_alerts if a["company_number"] not in detailed_company_numbers
    ]

    all_alerts = detailed_alerts + supplementary_bulk_alerts

    fieldnames = [
        "company_number", "indicator", "detail",
        "evidence_url", "confidence", "detected_at",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_alerts)

    os.makedirs(os.path.dirname(args.json_output) or ".", exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "companies_scanned": len(companies_to_check),
        "flagged_backlog_size": len(flagged_companies),
        "alerts": all_alerts,
    }
    with open(args.json_output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(
        f"\n{len(all_alerts)} total alert(s) written to {args.output} and {args.json_output} "
        f"({len(detailed_alerts)} from this run's detailed checks, "
        f"{len(supplementary_bulk_alerts)} carried over from the bulk snapshot)."
    )


if __name__ == "__main__":
    main()
