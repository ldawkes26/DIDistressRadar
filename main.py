"""
Pulls Companies House data for a watchlist of companies, runs the
distress indicator detectors, and writes results to both a CSV (for
analyst review) and a JSON file (consumed by the static dashboard in
site/). All output is company-level only, no officer or individual
names are written anywhere in this pipeline, see indicators.py.

Usage:
    export CH_API_KEY="your_key_here"
    python main.py --watchlist watchlist.csv --output alerts.csv --json-output site/data/alerts.json

watchlist.csv format: a header row, then one company_number per row
(company_name is optional, for readability only). See the sample
watchlist.csv included alongside this script.

This script performs no automated escalation. It produces a reviewed
alert feed for an analyst to triage, consistent with the internal
proactive-flagging use case confirmed for this build. The JSON output
is published to a public GitHub Pages dashboard by the GitHub Actions
workflow in .github/workflows/, so nothing written here should ever
include individual-level data.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from ch_client import CompaniesHouseClient
from indicators import run_all_detectors


def load_watchlist(path):
    companies = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            number = (row.get("company_number") or "").strip()
            if number:
                companies.append(number)
    return companies


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
    parser.add_argument("--output", default="alerts.csv")
    parser.add_argument("--json-output", default="site/data/alerts.json")
    args = parser.parse_args()

    try:
        client = CompaniesHouseClient()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    companies = load_watchlist(args.watchlist)
    if not companies:
        print(f"No company numbers found in {args.watchlist}", file=sys.stderr)
        sys.exit(1)

    all_alerts = []
    for company_number in companies:
        print(f"Checking {company_number}...")
        try:
            alerts = process_company(client, company_number)
        except Exception as e:
            print(f"  Error retrieving data for {company_number}: {e}", file=sys.stderr)
            continue
        if alerts:
            print(f"  {len(alerts)} alert(s) found.")
        all_alerts.extend(alerts)

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
        "companies_scanned": len(companies),
        "alerts": all_alerts,
    }
    with open(args.json_output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    if all_alerts:
        print(f"\n{len(all_alerts)} total alert(s) written to {args.output} and {args.json_output}")
    else:
        print(f"\nNo alerts triggered for this watchlist. Empty result written to {args.output} and {args.json_output}")


if __name__ == "__main__":
    main()
