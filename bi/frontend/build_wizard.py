"""build_wizard.py — Compose wizard.html from index.html's Style Atlas + new components.

Strategy: extract style + reusable React components from index.html as-is,
add wizard-specific code (PhotoUpload, HomeReport, Listing, new App),
write the result to wizard.html.

Run from: /Users/jimmy20020528/Desktop/Edensign/bi/frontend/
"""
from pathlib import Path

ROOT = Path(__file__).parent
SRC = (ROOT / "index.html").read_text()
LINES = SRC.split("\n")  # 1-indexed via LINES[i-1]


def slice_lines(start: int, end: int) -> str:
    """Inclusive 1-indexed slice."""
    return "\n".join(LINES[start - 1:end])


# Extract reusable chunks from index.html
CSS_BLOCK = slice_lines(13, 194)                  # <style>...</style>
ICONS_AND_MAPS = slice_lines(204, 253)            # Ic, ZIP_META, STYLE_PHOTOS, STYLE_TAGS, styleToId
API_HELPERS = slice_lines(254, 278)               # apiAnalyze, apiExplain, apiListingWrite
MAP_ANALYSIS = slice_lines(281, 380)              # mapAnalysis function
SIDEBAR = slice_lines(382, 411)                   # Sidebar component
TOPBAR = slice_lines(412, 439)                    # Topbar component
KPI_STRIP = slice_lines(493, 506)                 # KpiStrip
STYLE_CARD = slice_lines(507, 553)                # StyleCard
MARKET_INTEL = slice_lines(554, 655)              # MarketIntelSection
AI_SECTION = slice_lines(656, 763)                # AISection
FACTORS_CHART = slice_lines(764, 803)             # FactorsChart


# Wizard-specific additions appended after the imports
WIZARD_EXTRA_CSS = """
  /* ── Wizard-specific styles ── */
  .upload-zone{border:2px dashed var(--line);border-radius:14px;padding:48px 24px;text-align:center;
    transition:border-color .15s, background .15s;cursor:pointer;background:var(--cream-soft);}
  .upload-zone:hover, .upload-zone.dragover{border-color:var(--primary);background:var(--paper);}
  .upload-icon{width:44px;height:44px;margin:0 auto 12px;color:var(--primary);}
  .upload-title{font-size:16px;color:var(--primary);font-weight:500;margin:0 0 4px;}
  .upload-hint{font-size:13px;color:var(--fg-subtle);}
  .upload-zone input[type=file]{display:none;}
  .thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;margin-top:16px;}
  .thumb{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;background:var(--cream-soft);border:1px solid var(--line);}
  .thumb img{width:100%;height:100%;object-fit:cover;display:block;}
  .thumb .x{position:absolute;top:5px;right:5px;width:20px;height:20px;border-radius:99px;
    background:rgba(0,0,0,.65);color:#fff;border:none;display:grid;place-items:center;font-size:11px;line-height:1;}
  .wizard-form-row{display:grid;grid-template-columns:200px 1fr auto;gap:14px;align-items:end;
    margin-top:20px;padding-top:20px;border-top:1px dashed var(--line);}
  .progress-card{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:28px;
    margin-bottom:24px;display:flex;flex-direction:column;gap:14px;}
  .progress-step{display:flex;align-items:center;gap:14px;font-size:14px;color:var(--fg-muted);}
  .progress-step .pdot{width:18px;height:18px;border-radius:99px;border:2px solid var(--line);
    background:var(--paper);flex:none;display:grid;place-items:center;font-size:10px;color:var(--paper);}
  .progress-step.active .pdot{border-color:var(--primary);animation:ppulse 1.4s ease-in-out infinite;}
  .progress-step.done .pdot{border-color:var(--success);background:var(--success);}
  .progress-step.done .pdot::after{content:'✓';color:#fff;font-size:11px;}
  .progress-step.active{color:var(--primary);font-weight:500;}
  @keyframes ppulse{0%,100%{box-shadow:0 0 0 0 var(--primary-20);}50%{box-shadow:0 0 0 6px transparent;}}
  .hr-card{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:24px 26px;margin-bottom:20px;}
  .hr-overall{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px;}
  .hr-grade{padding:18px 22px;border-radius:14px;background:var(--cream-soft);border:1px solid var(--line);}
  .hr-grade-label{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--fg-subtle);font-weight:500;}
  .hr-grade-val{font-family:var(--font-display);font-size:36px;color:var(--primary);line-height:1;margin-top:6px;}
  .hr-grade-val .dec{font-size:16px;color:var(--fg-muted);font-family:var(--font-sans);margin-left:6px;}
  .hr-grade-desc{font-size:13px;color:var(--fg-muted);margin-top:8px;line-height:1.45;}
  .room-card{padding:16px 0;border-bottom:1px solid var(--line-soft);}
  .room-card:last-child{border-bottom:none;padding-bottom:0;}
  .room-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px;}
  .room-name{font-size:15px;font-weight:500;color:var(--primary);text-transform:capitalize;}
  .room-rating{font-family:var(--font-mono);font-size:13px;color:var(--fg-muted);}
  .room-rationale{font-size:13px;color:var(--fg-muted);line-height:1.55;margin-bottom:8px;}
  .room-features{display:flex;flex-wrap:wrap;gap:6px;}
  .feat-chip{font-size:11px;padding:3px 9px;border-radius:99px;background:var(--cream-soft);
    color:var(--fg-muted);border:1px solid var(--line-soft);}
  .action-block{padding:14px 0;border-bottom:1px solid var(--line-soft);}
  .action-block:last-child{border-bottom:none;padding-bottom:0;}
  .action-head-row{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:4px;}
  .action-title{font-size:14px;color:var(--primary);font-weight:500;flex:1;}
  .action-cost-tag{font-family:var(--font-mono);font-size:12px;color:var(--fg-muted);white-space:nowrap;}
  .action-desc{font-size:12.5px;color:var(--fg-muted);line-height:1.55;}
  .action-chips{display:inline-flex;gap:7px;margin-top:7px;}
  .a-chip{font-size:10px;text-transform:uppercase;letter-spacing:.05em;padding:2px 7px;border-radius:99px;font-weight:500;}
  .a-chip.roi-high{background:#dcfce7;color:#166534;}
  .a-chip.roi-medium{background:#fef3c7;color:#854d0e;}
  .a-chip.roi-low{background:var(--line-soft);color:var(--fg-muted);}
  .a-chip.cost-low{background:#dbeafe;color:#1e40af;}
  .a-chip.cost-medium{background:#fef3c7;color:#854d0e;}
  .a-chip.cost-high{background:#fee2e2;color:#991b1b;}
  .listing-body{background:var(--cream-soft);border-radius:12px;padding:22px 24px;font-size:15px;
    line-height:1.75;color:var(--fg);white-space:pre-wrap;font-family:var(--font-sans);}
  .copy-bar{margin-top:14px;display:flex;justify-content:flex-end;}
"""

WIZARD_APP = r"""
/* ── Wizard-specific components ── */
function PhotoUpload({files, setFiles, location, setLocation, onRun, running}) {
  const [drag, setDrag] = useState(false);
  const inputRef = useRef(null);

  const addFiles = (fileList) => {
    const next = [...files];
    for (const f of fileList) {
      if (!f.type.startsWith('image/')) continue;
      if (next.length >= 30) break;
      next.push(f);
    }
    setFiles(next);
  };
  const remove = (i) => setFiles(files.filter((_, idx) => idx !== i));
  // Allow either a 5-digit ZIP or any address (>= 6 chars)
  const locTrim = location.trim();
  const isZip = /^\d{5}$/.test(locTrim);
  const isAddr = locTrim.length >= 6 && /[a-zA-Z]/.test(locTrim);
  const canRun = files.length > 0 && (isZip || isAddr);

  return (
    <div className="search-card">
      <div style={{fontSize:11,textTransform:"uppercase",letterSpacing:".12em",color:"var(--fg-subtle)",fontWeight:500,marginBottom:14}}>
        Property Photos & Location
      </div>
      <div
        className={`upload-zone${drag?" dragover":""}`}
        onClick={()=>inputRef.current?.click()}
        onDragOver={(e)=>{e.preventDefault();setDrag(true);}}
        onDragLeave={()=>setDrag(false)}
        onDrop={(e)=>{e.preventDefault();setDrag(false);addFiles(e.dataTransfer.files);}}
      >
        <svg className="upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        <div className="upload-title">{files.length === 0 ? "Drop photos here or click to choose" : `${files.length} photo${files.length>1?"s":""} ready`}</div>
        <div className="upload-hint">JPG / PNG, up to 30 photos</div>
        <input ref={inputRef} type="file" multiple accept="image/*" onChange={(e)=>{addFiles(e.target.files);e.target.value="";}}/>
      </div>
      {files.length > 0 && (
        <div className="thumbs">
          {files.map((f, i) => (
            <div key={i} className="thumb">
              <img src={URL.createObjectURL(f)} alt=""/>
              <button className="x" onClick={()=>remove(i)} type="button">×</button>
            </div>
          ))}
        </div>
      )}
      <div className="wizard-form-row" style={{gridTemplateColumns:"1fr auto"}}>
        <div className="field">
          <label>Address or ZIP code</label>
          <input
            className="input"
            type="text"
            placeholder="20 Allston St Boston MA  —  or just  02135"
            value={location}
            onChange={(e)=>setLocation(e.target.value)}
          />
        </div>
        <button className="submit-btn" disabled={!canRun || running} onClick={onRun}>
          {running ? "Running…" : <>Run Pipeline <Ic.arrow/></>}
        </button>
      </div>
    </div>
  );
}

function ProgressIndicator({step}) {
  // step: 0=upload, 1=analyzing, 2=market, 3=listing, 4=done
  const steps = [
    {key:"upload",   label:"Uploading photos"},
    {key:"analyze",  label:"Analyzing photos with vision AI"},
    {key:"market",   label:"Loading market data for ZIP"},
    {key:"listing",  label:"Composing listing description"},
  ];
  return (
    <div className="progress-card">
      <h3 style={{margin:"0 0 4px",fontFamily:"var(--font-display)",fontSize:22,color:"var(--primary)"}}>Analyzing your property…</h3>
      <p style={{margin:"0 0 12px",fontSize:13,color:"var(--fg-muted)"}}>This usually takes 30 seconds to 2 minutes.</p>
      {steps.map((s, i) => {
        const cls = i < step ? "done" : i === step ? "active" : "";
        return (
          <div key={s.key} className={`progress-step ${cls}`}>
            <div className="pdot"/>
            <span>{s.label}</span>
          </div>
        );
      })}
    </div>
  );
}

function HomeReportDisplay({hr}) {
  if (!hr || !hr.overall_quality_rating) return null;
  const qDesc = {
    Q1:"Architect-designed, unique", Q2:"High-end custom", Q3:"Above builder-grade",
    Q4:"Standard builder-grade", Q5:"Economy", Q6:"Below minimum standards",
  };
  const cDesc = {
    C1:"New / never occupied", C2:"Like new / fully renovated", C3:"Normal wear, well maintained",
    C4:"Minor deferred maintenance", C5:"Obvious deterioration", C6:"Significant damage",
  };
  const allActions = [
    ...(hr.must_do || []).map(a => ({...a, _bucket:"must_do"})),
    ...(hr.recommended || []).map(a => ({...a, _bucket:"recommended"})),
    ...(hr.optional || []).map(a => ({...a, _bucket:"optional"})),
  ];
  return (
    <>
      <header className="page-head" style={{marginTop:32}}>
        <div>
          <h2 className="page-title" style={{fontSize:30}}>Property Assessment</h2>
          <p className="page-sub">UAD-calibrated quality and condition ratings from your photos.</p>
        </div>
      </header>
      <div className="hr-card">
        <div className="hr-overall">
          <div className="hr-grade">
            <div className="hr-grade-label">Quality</div>
            <div className="hr-grade-val">{hr.overall_quality_rating}<span className="dec">{hr.overall_quality_decimal?.toFixed(1)}</span></div>
            <div className="hr-grade-desc">{qDesc[hr.overall_quality_rating] || ""}</div>
          </div>
          <div className="hr-grade">
            <div className="hr-grade-label">Condition</div>
            <div className="hr-grade-val">{hr.overall_condition_rating}<span className="dec">{hr.overall_condition_decimal?.toFixed(1)}</span></div>
            <div className="hr-grade-desc">{cDesc[hr.overall_condition_rating] || ""}</div>
          </div>
        </div>
        {(hr.rooms || []).map((r, i) => (
          <div key={i} className="room-card">
            <div className="room-head">
              <div className="room-name">{r.room_type.replace(/_/g," ")}</div>
              <div className="room-rating">{r.quality_rating} · {r.condition_rating}</div>
            </div>
            <div className="room-rationale">{r.quality_rationale}</div>
            {r.notable_features?.length > 0 && (
              <div className="room-features">
                {r.notable_features.slice(0, 8).map((f, j) => (
                  <span key={j} className="feat-chip">{f.replace(/_/g," ")}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {allActions.length > 0 && (
        <div className="hr-card">
          <h3 style={{margin:"0 0 14px",fontFamily:"var(--font-display)",fontSize:22,color:"var(--primary)"}}>Upgrade Recommendations</h3>
          {allActions.map((a, i) => (
            <div key={i} className="action-block">
              <div className="action-head-row">
                <div className="action-title">{a.text}</div>
                <div className="action-cost-tag">{a.estimated_cost_range || ""}</div>
              </div>
              <div className="action-desc">{a.detail}</div>
              <div className="action-chips">
                <span className={`a-chip roi-${a.roi_tier}`}>ROI {a.roi_tier}</span>
                <span className={`a-chip cost-${a.cost_tier}`}>Cost {a.cost_tier}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function ListingDisplay({text}) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  const copy = () => {
    navigator.clipboard.writeText(text).then(()=>{
      setCopied(true);
      setTimeout(()=>setCopied(false), 2000);
    });
  };
  return (
    <>
      <header className="page-head" style={{marginTop:32}}>
        <div>
          <h2 className="page-title" style={{fontSize:30}}>Suggested Listing Description</h2>
          <p className="page-sub">Grounded in your photos and ZIP-level market intel.</p>
        </div>
      </header>
      <div className="hr-card">
        <div className="listing-body">{text}</div>
        <div className="copy-bar">
          <button className="btn primary" onClick={copy}>{copied ? "Copied!" : "Copy listing"}</button>
        </div>
      </div>
    </>
  );
}

/* ── App ── */
function App() {
  const [apiBase] = useState("http://localhost:8002");           // Tool service (Wizard's backend)
  const [biBase]  = useState(()=>window.location.origin==="null"?"http://localhost:8000":window.location.origin);
  const [page, setPage] = useState("wizard");
  const [files, setFiles] = useState([]);
  const [location, setLocation] = useState("");        // address OR 5-digit zip
  const [running, setRunning] = useState(false);
  const [progressStep, setProgressStep] = useState(0);
  const [result, setResult] = useState(null);        // { home_report, bi_analysis, listing_text }
  const [mapped, setMapped] = useState(null);        // mapAnalysis output
  const [error, setError] = useState(null);
  const [activeStyleId, setActiveStyleId] = useState("");
  const [selectedCard, setSelectedCard] = useState("");
  const [apiOk, setApiOk] = useState(null);

  const runPipeline = async () => {
    setError(null);
    setResult(null);
    setMapped(null);
    setRunning(true);
    setProgressStep(0);

    // Fake-advance the visible steps while the request runs
    const t1 = setTimeout(()=>setProgressStep(1), 1500);
    const t2 = setTimeout(()=>setProgressStep(2), 18000);
    const t3 = setTimeout(()=>setProgressStep(3), 28000);

    try {
      const form = new FormData();
      const locTrim = location.trim();
      if (/^\d{5}$/.test(locTrim)) {
        form.append("zipcode", locTrim);
      } else {
        form.append("address", locTrim);
      }
      files.forEach(f => form.append("files", f, f.name));
      const r = await fetch(`${apiBase}/pipeline/run`, {method:"POST", body: form});
      if (!r.ok) {
        const t = await r.text();
        throw new Error(`HTTP ${r.status}: ${t.slice(0,300)}`);
      }
      const data = await r.json();
      setApiOk(true);
      setProgressStep(4);
      setResult(data);
      // Use Style Atlas's mapAnalysis to produce the same display shape.
      // bi_explain is the full /analyze/explain response: { analysis, llm }
      // mapAnalysis expects the llm part as 2nd arg.
      try {
        const llm = data.bi_explain?.llm || null;
        const m = mapAnalysis(data.bi_analysis, llm);
        setMapped(m);
        setActiveStyleId(m.styles[0]?.id || "");
        setSelectedCard(m.styles[0]?.id || "");
      } catch (e) {
        console.error("mapAnalysis failed:", e);
      }
    } catch (e) {
      setApiOk(false);
      setError(e.message);
    } finally {
      clearTimeout(t1); clearTimeout(t2); clearTimeout(t3);
      setRunning(false);
    }
  };

  const onSelectCard = useCallback(id => {
    setSelectedCard(id);
    setActiveStyleId(id);
    document.querySelector("#ai-section")?.scrollIntoView?.({behavior:"smooth", block:"start"});
  }, []);

  return (
    <div className="app">
      <Sidebar page={page} setPage={setPage}/>
      <main>
        <Topbar page={page} zip={result?.zipcode || location} apiBase={biBase} setApiBase={()=>{}} apiOk={apiOk}/>
        <div className="page">
          <header className="page-head">
            <div>
              <h1 className="page-title">Listing Wizard</h1>
              <p className="page-sub">Upload property photos and a property location. We analyze condition, recommend the staging style that sells best, and draft a listing description — all grounded in real data.</p>
            </div>
          </header>

          {!running && !result && (
            <PhotoUpload
              files={files} setFiles={setFiles}
              location={location} setLocation={setLocation}
              onRun={runPipeline} running={running}
            />
          )}

          {running && <ProgressIndicator step={progressStep}/>}

          {error && (
            <div className="error-banner">
              <Ic.alert style={{flex:"none",marginTop:1}}/>
              <span><b>Pipeline failed:</b> {error}</span>
            </div>
          )}

          {result && mapped && (
            <>
              {/* Style Atlas display, identical to index.html */}
              <div className="results-head">
                <div className="market-summary">
                  <div className="market-eyebrow">Market snapshot</div>
                  <h2 className="market-title">{mapped.city}, {mapped.metro} · {mapped.zip}</h2>
                  <div className="market-meta">
                    <span>Mode <b>{mapped.scoringMode}</b></span>
                    <span className="meta-sep">·</span>
                    <span>As of <b>{mapped.asOf}</b></span>
                    <span className="meta-sep">·</span>
                    <span>{result.n_photos} photos analyzed</span>
                  </div>
                </div>
              </div>

              <KpiStrip kpis={mapped.kpis}/>

              <section className="results-grid">
                {mapped.styles.map(s => (
                  <StyleCard key={s.id} s={s} selected={selectedCard===s.id} onSelect={onSelectCard}/>
                ))}
              </section>

              <MarketIntelSection
                walkScore={mapped.walkScore}
                fredMacro={mapped.fredMacro}
                schoolProfile={mapped.schoolProfile}
                redfinMarket={mapped.redfinMarket}
              />

              <div id="ai-section">
                <AISection
                  ai={mapped.ai} aiLoading={false}
                  activeStyle={activeStyleId} setActiveStyle={setActiveStyleId}
                  styles={mapped.styles}
                  hmdaBuyer={mapped.hmdaBuyer}
                />
              </div>

              {mapped.factors.length > 0 && <FactorsChart factors={mapped.factors}/>}

              {/* Home Report */}
              <HomeReportDisplay hr={result.home_report}/>

              {/* Listing description */}
              <ListingDisplay text={result.listing_text}/>

              <div style={{marginTop:32,display:"flex",justifyContent:"center"}}>
                <button className="btn ghost" onClick={()=>{setResult(null);setMapped(null);setFiles([]);setLocation("");}}>
                  ← Start over
                </button>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
"""


# Compose final wizard.html
parts = [
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Listing Wizard — Edensign</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montaga&family=Jost:wght@300;400;500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
""",
    CSS_BLOCK.rstrip(),
    "<style>" + WIZARD_EXTRA_CSS + "</style>",
    """</head>
<body>
<div id="root"></div>
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous"></script>
<script type="text/babel" data-presets="react">
const { useState, useMemo, useCallback, useRef } = React;

/* ── Icons & static maps (from Style Atlas) ── */
""",
    ICONS_AND_MAPS,
    "\n/* ── API helpers (from Style Atlas) ── */\n",
    API_HELPERS,
    "\n/* ── Data mapper (from Style Atlas) ── */\n",
    MAP_ANALYSIS,
    "\n/* ── Shell components (from Style Atlas, page nav adapted) ── */\n",
    SIDEBAR,
    "\n",
    TOPBAR,
    "\n/* ── Display components (from Style Atlas, unchanged) ── */\n",
    KPI_STRIP,
    "\n",
    STYLE_CARD,
    "\n",
    MARKET_INTEL,
    "\n",
    AI_SECTION,
    "\n",
    FACTORS_CHART,
    WIZARD_APP,
    """
</script>
</body>
</html>
""",
]

out = "".join(parts)
out_path = ROOT / "wizard.html"
out_path.write_text(out)
print(f"Wrote {out_path} ({len(out)} bytes, {out.count(chr(10))} lines)")
