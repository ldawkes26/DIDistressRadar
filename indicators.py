"""
Indicator detection logic for the distress early-warning tool, phase 1.

Each detector takes raw Companies House API responses (as returned by
CompaniesHouseClient) for a single company and returns a list of alert
dicts. Every alert carries an evidence link back to the underlying
Companies House record and a confidence flag, consistent with the
"flag, don't conclude" design principle in the architecture document.

Coverage in this build: filing defaults, company status changes,
director/officer resignation clustering, new registered charges, and
open insolvency cases. Auditor resignation and going-concern wording
require parsing the text of filed accounts documents, not just filing
metadata, and are intentionally left for a later phase (see README).
"""

from datetime import datetime, timedelta, timezone

WEB_BASE = "https://find-and-update.company-information.service.gov.uk/company"

WATCH_STATUSES = {
    "administration",
    "liquidation",
    "receivership",
    "voluntary-arrangement",
    "insolvency-proceedings",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _company_url(company_number):
    return f"{WEB_BASE}/{company_number}"


def detect_filing_defaults(company_number, profile):
    """Flags overdue accounts or an overdue confirmation statement."""
    alerts = []
    if not profile:
        return alerts

    accounts = profile.get("accounts", {})
    if accounts.get("overdue"):
        alerts.append({
            "company_number": company_number,
            "indicator": "accounts_overdue",
            "detail": "Company accounts are shown as overdue on the company profile.",
            "evidence_url": _company_url(company_number) + "/filing-history",
            "confidence": "high",
            "detected_at": _now(),
        })

    confirmation_statement = profile.get("confirmation_statement", {})
    if confirmation_statement.get("overdue"):
        alerts.append({
            "company_number": company_number,
            "indicator": "confirmation_statement_overdue",
            "detail": "Confirmation statement is shown as overdue.",
            "evidence_url": _company_url(company_number) + "/filing-history",
            "confidence": "high",
            "detected_at": _now(),
        })

    return alerts


def detect_company_status_flags(company_number, profile):
    """Flags a non-active company status (administration, liquidation, etc)."""
    alerts = []
    if not profile:
        return alerts

    status = profile.get("company_status")
    if status in WATCH_STATUSES:
        alerts.append({
            "company_number": company_number,
            "indicator": "company_status_change",
            "detail": f"Company status is reported as '{status}'.",
            "evidence_url": _company_url(company_number),
            "confidence": "high",
            "detected_at": _now(),
        })
    return alerts


def detect_officer_resignation_cluster(
    company_number, officers_response, window_days=90, cluster_size=2
):
    """
    Flags a cluster of officer resignations within a rolling window.

    A single resignation is not flagged on its own; clustering is the
    signal, consistent with the architecture document's guidance that
    a lone departure is weaker evidence than several in a short window.
    """
    alerts = []
    if not officers_response:
        return alerts

    resignations = []
    for officer in officers_response.get("items", []):
        resigned_on = officer.get("resigned_on")
        if resigned_on:
            try:
                resigned_date = datetime.strptime(resigned_on, "%Y-%m-%d")
            except ValueError:
                continue
            # Deliberately not capturing officer name here. This indicator
            # is published to a public dashboard, so the output must stay
            # at the company level, not the individual level. See README.
            resignations.append(resigned_date)

    resignations.sort()
    for i in range(len(resignations)):
        window_start = resignations[i]
        window_end = window_start + timedelta(days=window_days)
        cluster = [r for r in resignations if window_start <= r <= window_end]
        if len(cluster) >= cluster_size:
            alerts.append({
                "company_number": company_number,
                "indicator": "officer_resignation_cluster",
                "detail": (
                    f"{len(cluster)} officer resignation(s) recorded within "
                    f"a {window_days}-day window (earliest {cluster[0].date()}, "
                    f"latest {cluster[-1].date()})."
                ),
                "evidence_url": _company_url(company_number) + "/officers",
                "confidence": "medium",
                "detected_at": _now(),
            })
            break  # report the first qualifying cluster only, avoid overlapping duplicates

    return alerts


def detect_new_charges(company_number, charges_response, lookback_days=90):
    """Flags newly created registered charges within a lookback window."""
    alerts = []
    if not charges_response:
        return alerts

    cutoff = datetime.now() - timedelta(days=lookback_days)
    for charge in charges_response.get("items", []):
        created_on = charge.get("created_on")
        if not created_on:
            continue
        try:
            created_date = datetime.strptime(created_on, "%Y-%m-%d")
        except ValueError:
            continue
        if created_date >= cutoff:
            alerts.append({
                "company_number": company_number,
                "indicator": "new_registered_charge",
                "detail": (
                    f"New charge created on {created_on}, "
                    f"status '{charge.get('status', 'unknown')}'."
                ),
                "evidence_url": _company_url(company_number) + "/charges",
                "confidence": "medium",
                "detected_at": _now(),
            })

    return alerts


def detect_insolvency_case(company_number, insolvency_response):
    """Flags any open insolvency case returned by the insolvency endpoint."""
    alerts = []
    if not insolvency_response:
        return alerts

    for case in insolvency_response.get("cases", []):
        alerts.append({
            "company_number": company_number,
            "indicator": "insolvency_case",
            "detail": f"Insolvency case recorded, type '{case.get('type', 'unknown')}'.",
            "evidence_url": _company_url(company_number),
            "confidence": "high",
            "detected_at": _now(),
        })

    return alerts


def run_all_detectors(
    company_number, profile, officers_response, charges_response, insolvency_response
):
    alerts = []
    alerts += detect_filing_defaults(company_number, profile)
    alerts += detect_company_status_flags(company_number, profile)
    alerts += detect_officer_resignation_cluster(company_number, officers_response)
    alerts += detect_new_charges(company_number, charges_response)
    alerts += detect_insolvency_case(company_number, insolvency_response)
    return alerts
