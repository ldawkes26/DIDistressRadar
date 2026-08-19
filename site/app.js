// Loads site/data/alerts.json (written by main.py during the GitHub Actions
// run) and renders it as a filterable register. No individual-level data
// is ever present in this file, see indicators.py for the source of that
// constraint.

const DATA_URL = "data/alerts.json";

const state = {
  alerts: [],
  filters: { indicator: "", confidence: "", company: "" },
};

async function loadData() {
  const tbody = document.getElementById("registerBody");
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();

    state.alerts = payload.alerts || [];
    document.getElementById("statGeneratedAt").textContent =
      formatTimestamp(payload.generated_at);
    document.getElementById("statScanned").textContent =
      payload.companies_scanned ?? "—";
    document.getElementById("statAlerts").textContent = state.alerts.length;

    populateFilterOptions();
    render();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="state-msg">
      Could not load register data (${escapeHtml(String(err.message || err))}).
      The first scheduled scan may not have run yet.
    </td></tr>`;
  }
}

function formatTimestamp(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", timeZone: "UTC",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

function populateFilterOptions() {
  const indicators = [...new Set(state.alerts.map(a => a.indicator))].sort();
  const confidences = [...new Set(state.alerts.map(a => a.confidence))].sort();

  const indicatorSelect = document.getElementById("filterIndicator");
  indicators.forEach(val => {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = labelize(val);
    indicatorSelect.appendChild(opt);
  });

  const confidenceSelect = document.getElementById("filterConfidence");
  confidences.forEach(val => {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = labelize(val);
    confidenceSelect.appendChild(opt);
  });
}

function labelize(value) {
  return String(value).replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function render() {
  const tbody = document.getElementById("registerBody");
  const { indicator, confidence, company } = state.filters;

  const rows = state.alerts.filter(a => {
    if (indicator && a.indicator !== indicator) return false;
    if (confidence && a.confidence !== confidence) return false;
    if (company) {
      const needle = company.toLowerCase();
      const haystack = `${a.company_number} ${a.company_name || ""}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="state-msg">
      No flags match the current filters.
    </td></tr>`;
    return;
  }

  // Most recent first
  rows.sort((a, b) => (b.detected_at || "").localeCompare(a.detected_at || ""));

  tbody.innerHTML = rows.map(a => `
    <tr>
      <td class="cell-company-name">${escapeHtml(a.company_name || "—")}</td>
      <td class="cell-company">${escapeHtml(a.company_number)}</td>
      <td class="cell-indicator">${escapeHtml(labelize(a.indicator))}</td>
      <td>${escapeHtml(a.detail)}</td>
      <td><span class="badge badge--${escapeHtml(a.confidence || "unknown")}">${escapeHtml(a.confidence || "unknown")}</span></td>
      <td class="cell-detected">${escapeHtml(formatTimestamp(a.detected_at))}</td>
      <td class="cell-source"><a href="${a.evidence_url}" target="_blank" rel="noopener noreferrer">View record</a></td>
    </tr>
  `).join("");
}

document.getElementById("filterIndicator").addEventListener("change", e => {
  state.filters.indicator = e.target.value;
  render();
});
document.getElementById("filterConfidence").addEventListener("change", e => {
  state.filters.confidence = e.target.value;
  render();
});
document.getElementById("filterCompany").addEventListener("input", e => {
  state.filters.company = e.target.value;
  render();
});

loadData();
