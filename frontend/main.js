const form = document.getElementById("query-form");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const fallbackEl = document.getElementById("fallback");
const summaryEl = document.getElementById("summary");
const top3El = document.getElementById("top3");
const fallbackSummaryEl = document.getElementById("fallback-summary");
const fallbackTop3El = document.getElementById("fallback-top3");
const submitBtn = document.getElementById("submit-btn");
const explainBtn = document.getElementById("explain-btn");
const explanationEl = document.getElementById("explanation");
const explanationJsonEl = document.getElementById("explanation-json");
const explanationCardsEl = document.getElementById("explanation-cards");
const expSummaryEl = document.getElementById("exp-summary");
const expConfidenceEl = document.getElementById("exp-confidence");
const expWhyTop1El = document.getElementById("exp-why-top1");
const expActionPlanEl = document.getElementById("exp-action-plan");
const expRiskNotesEl = document.getElementById("exp-risk-notes");
const expStyleTipsEl = document.getElementById("exp-style-tips");
const apiBaseInput = document.getElementById("api-base");
let lastQuery = null;

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.style.color = isError ? "#b91c1c" : "#334155";
}

function toPretty(obj) {
  return JSON.stringify(obj, null, 2);
}

function toArraySafe(value) {
  if (Array.isArray(value)) return value;
  if (value == null) return [];
  if (typeof value === "string") return [value];
  if (typeof value === "object") return [toPretty(value)];
  return [String(value)];
}

function renderList(el, arr) {
  el.innerHTML = "";
  toArraySafe(arr).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = typeof item === "string" ? item : toPretty(item);
    el.appendChild(li);
  });
  if (toArraySafe(arr).length === 0) {
    const li = document.createElement("li");
    li.textContent = "N/A";
    el.appendChild(li);
  }
}

function renderStyleTips(tips) {
  expStyleTipsEl.innerHTML = "";
  const safeTips = Array.isArray(tips)
    ? tips
    : tips && typeof tips === "object"
      ? [tips]
      : [];

  safeTips.forEach((t) => {
    const tipObj = t && typeof t === "object" ? t : {};
    const div = document.createElement("div");
    div.className = "tip-item";
    div.innerHTML = `
      <div><b>${tipObj.style || "Style"}</b></div>
      <div>Tip: ${tipObj.tip || "N/A"}</div>
      <div>Watchout: ${tipObj.watchout || "N/A"}</div>
    `;
    expStyleTipsEl.appendChild(div);
  });
  if (safeTips.length === 0) {
    expStyleTipsEl.innerHTML = "<div>N/A</div>";
  }
}

function renderTop3(container, items) {
  container.innerHTML = "";
  (items || []).slice(0, 3).forEach((item, idx) => {
    const div = document.createElement("div");
    div.className = "rec-item";
    const score =
      item.hybrid_score ?? item.model_score ?? item.style_score ?? "n/a";
    const dom =
      item.median_days_on_market == null ? "n/a" : item.median_days_on_market;
    const ppsfRaw = item.median_price_per_sqft;
    const ppsf =
      typeof ppsfRaw === "number" ? ppsfRaw.toFixed(2) : (ppsfRaw ?? "n/a");

    div.innerHTML = `
      <h4>#${idx + 1} ${item.style}</h4>
      <div>score: <b>${score}</b></div>
      <div>n_listings: ${item.n_listings ?? "n/a"}</div>
      <div>median_days_on_market: ${dom}</div>
      <div>median_price_per_sqft: ${ppsf}</div>
      <div>warnings: ${
        Array.isArray(item?.confidence?.warnings)
          ? item.confidence.warnings.join(", ") || "none"
          : "none"
      }</div>
    `;
    container.appendChild(div);
  });
}

async function fetchAnalyze(baseUrl, params) {
  const url = new URL("/analyze/by-zipcode", baseUrl);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url.toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

async function fetchExplain(baseUrl, body) {
  const url = new URL("/analyze/explain/by-zipcode", baseUrl);
  const res = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  submitBtn.disabled = true;
  resultEl.classList.add("hidden");
  fallbackEl.classList.add("hidden");
  explanationEl.classList.add("hidden");
  explanationJsonEl.textContent = "";
    explanationCardsEl.classList.add("hidden");
  top3El.innerHTML = "";
  fallbackTop3El.innerHTML = "";

  const baseUrl = document.getElementById("api-base").value.trim();
  const zipcode = document.getElementById("zipcode").value.trim();
  const objective = document.getElementById("objective").value;
  const scoringMode = document.getElementById("scoring-mode").value;

  try {
    setStatus("Loading...");
    lastQuery = {
      zipcode,
      objective,
      scoring_mode: scoringMode,
    };
    const primary = await fetchAnalyze(baseUrl, {
      zipcode,
      objective,
      scoring_mode: scoringMode,
    });

    const summary = {
      zipcode: primary.zipcode,
      objective: primary.objective,
      scoring_mode: primary.scoring_mode || "heuristic",
      status: primary.status,
      confidence: primary.confidence,
      warnings: primary.warnings || [],
      model_artifacts: primary.model_meta
        ? {
            log_psf: primary.model_meta.log_psf_artifact,
            log_dom: primary.model_meta.log_dom_artifact,
          }
        : undefined,
    };

    summaryEl.textContent = toPretty(summary);
    renderTop3(top3El, primary.recommended_styles);
    resultEl.classList.remove("hidden");

    // Auto fallback: if small_zip warning appears, fetch heuristic too.
    const warnings = primary.warnings || [];
    if (warnings.includes("small_zip_low_support") && scoringMode !== "heuristic") {
      const fallback = await fetchAnalyze(baseUrl, {
        zipcode,
        objective,
        scoring_mode: "heuristic",
      });
      fallbackSummaryEl.textContent = toPretty({
        zipcode: fallback.zipcode,
        status: fallback.status,
        confidence: fallback.confidence,
      });
      renderTop3(fallbackTop3El, fallback.recommended_styles);
      fallbackEl.classList.remove("hidden");
      setStatus("Loaded model/hybrid result + heuristic fallback.");
    } else {
      setStatus("Loaded.");
    }
  } catch (err) {
    const baseUrl = document.getElementById("api-base").value.trim();
    setStatus(
      `Failed: ${err.message}. Check API Base URL (${baseUrl}) and ensure backend is running on that host/port.`,
      true
    );
  } finally {
    submitBtn.disabled = false;
  }
});

explainBtn.addEventListener("click", async () => {
  const baseUrl = document.getElementById("api-base").value.trim();
  if (!lastQuery) {
    setStatus("Please run Analyze first.", true);
    return;
  }
  explainBtn.disabled = true;
  try {
    setStatus("Generating AI explanation...");
    const resp = await fetchExplain(baseUrl, {
      ...lastQuery,
      client_context: {
        language: "English",
        audience: "homeowner_or_staging_team",
      },
    });
    const expl = resp?.llm?.explanation || {};
    explanationJsonEl.textContent = toPretty(resp.llm);

    expSummaryEl.textContent = expl.executive_summary || "N/A";
    expConfidenceEl.textContent = expl.confidence_readout || "N/A";
    renderList(expWhyTop1El, expl.why_top1);
    renderList(expActionPlanEl, expl.action_plan);
    renderList(expRiskNotesEl, expl.risk_notes);
    renderStyleTips(expl.style_specific_tips);
    explanationCardsEl.classList.remove("hidden");

    explanationEl.classList.remove("hidden");
    setStatus("AI explanation ready.");
  } catch (err) {
    setStatus(`Explain failed: ${err.message}`, true);
  } finally {
    explainBtn.disabled = false;
  }
});
