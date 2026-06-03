"""build_wizard.py — Compose wizard.html from index.html's Style Atlas + new components.

Strategy: extract style + reusable React components from index.html as-is,
add wizard-specific code (PhotoUpload, HomeReport, Listing, new App),
write the result to wizard.html.

Run from: /Users/jimmy20020528/Desktop/Edensign/bi/frontend/
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent

# Read Supabase credentials from bi/.env
_env_text = (ROOT.parent / ".env").read_text()
def _env_val(key):
    m = re.search(rf'^{key}=(.+)', _env_text, re.MULTILINE)
    return m.group(1).strip() if m else ""
SUPABASE_URL = _env_val("SUPABASE_URL")
SUPABASE_ANON_KEY = _env_val("SUPABASE_ANON_KEY")
SRC = (ROOT / "index.html").read_text()
LINES = SRC.split("\n")  # 1-indexed via LINES[i-1]


def slice_lines(start: int, end: int) -> str:
    """Inclusive 1-indexed slice."""
    return "\n".join(LINES[start - 1:end])


# Extract reusable chunks from index.html
CSS_BLOCK = slice_lines(13, 194)                  # <style>...</style>
ICONS_AND_MAPS = slice_lines(204, 257)            # Ic, ZIP_META, STYLE_PHOTOS, STYLE_TAGS, styleToId
API_HELPERS = slice_lines(259, 282)               # apiAnalyze, apiExplain, apiListingWrite
MAP_ANALYSIS = slice_lines(285, 383)              # mapAnalysis function
SIDEBAR = slice_lines(386, 414)                   # Sidebar component
TOPBAR = slice_lines(416, 442)                    # Topbar component
KPI_STRIP = slice_lines(497, 509)                 # KpiStrip
STYLE_CARD = slice_lines(511, 564)                # StyleCard
MARKET_INTEL = slice_lines(566, 666)              # MarketIntelSection
AI_SECTION = slice_lines(668, 774)                # AISection
FACTORS_CHART = slice_lines(776, 815)             # FactorsChart


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
  .thumb .x{position:absolute;top:5px;right:5px;width:24px;height:24px;border-radius:99px;
    background:rgba(0,0,0,.72);color:#fff;border:none;display:grid;place-items:center;
    font-size:14px;line-height:1;cursor:pointer;opacity:1;transition:background .12s;}
  .thumb .x:hover{background:rgba(180,0,0,.85);}
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
  /* ── Room classification UI ── */
  .thumb-label{position:absolute;bottom:0;left:0;right:0;padding:3px 6px;
    font-size:10px;font-weight:600;letter-spacing:.03em;text-align:center;color:#fff;}
  .classify-bar{display:flex;justify-content:space-between;align-items:center;
    padding:12px 0 4px;gap:12px;}
  .classify-hint{font-size:12px;color:var(--fg-subtle);}
  .review-panel{background:var(--paper);border:1px solid var(--line);
    border-radius:18px;padding:24px 26px;margin-bottom:20px;}
  .review-header{display:flex;justify-content:space-between;align-items:flex-start;
    padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid var(--line-soft);}
  .rg-card{border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:10px;}
  .rg-header{padding:10px 14px;display:flex;justify-content:space-between;
    align-items:center;border-bottom:1px solid var(--line-soft);}
  .rg-group-badge{font-size:10px;color:var(--fg-subtle);background:rgba(0,0,0,.06);
    padding:2px 7px;border-radius:99px;letter-spacing:.04em;font-weight:500;}
  .empty-badge{font-size:10px;color:var(--fg-muted);background:var(--cream-soft);border:1px solid var(--line);
    padding:2px 7px;border-radius:99px;letter-spacing:.04em;font-weight:500;}
  .rg-mini-thumbs{display:flex;gap:6px;padding:10px 14px;flex-wrap:wrap;max-height:120px;overflow-y:auto;}
  .rg-mini-thumb{width:56px;height:44px;border-radius:6px;overflow:hidden;
    background:var(--cream-soft);flex:none;}
  .rg-staging-row{display:flex;align-items:center;gap:8px;padding:8px 14px;
    border-top:1px solid var(--line-soft);background:var(--cream-soft);}
  .staging-tag{font-size:11px;font-weight:600;color:#444;background:var(--cream-soft);
    border:1px solid var(--line);padding:2px 10px;border-radius:99px;letter-spacing:.02em;}
  .review-footer{display:flex;justify-content:flex-end;margin-top:16px;
    padding-top:14px;border-top:1px solid var(--line-soft);}
  /* ── StagingModal ── */
  .staging-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:1000;
    display:flex;align-items:center;justify-content:center;padding:20px;}
  .staging-box{background:#fff;border-radius:16px;padding:24px;width:100%;max-width:440px;
    max-height:90vh;overflow-y:auto;box-shadow:0 8px 40px rgba(0,0,0,0.18);}
  .staging-style-pill{border-radius:99px;padding:5px 12px;font-size:11px;cursor:pointer;
    border:1px solid #e0d8d0;transition:background .12s,color .12s;}
  .staging-style-pill.active{background:var(--primary);color:#fff;font-weight:600;
    border-color:var(--primary);}
  .staging-style-pill:not(.active){background:#f5f0eb;color:var(--primary);}
  .staging-room-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:10px;margin-bottom:16px;}
  .staging-room-card{border-radius:10px;border:2px solid #e0d8d0;cursor:pointer;overflow:hidden;
    transition:border-color .12s,transform .1s;background:var(--paper);}
  .staging-room-card:not(.active):hover{transform:translateY(-1px);border-color:var(--primary);}
  .staging-room-card.active{border-color:var(--primary);box-shadow:0 0 0 3px rgba(91,75,55,0.12);}
  .staging-room-preview{position:relative;aspect-ratio:4/3;background:#f0ece8;overflow:hidden;}
  .staging-room-preview img{width:100%;height:100%;object-fit:cover;display:block;}
  .staging-room-badge{position:absolute;top:5px;right:5px;background:rgba(0,0,0,0.65);
    color:#fff;font-size:9px;font-weight:600;padding:2px 6px;border-radius:99px;white-space:nowrap;}
  .staging-toggle{width:42px;height:24px;border-radius:99px;position:relative;
    cursor:pointer;flex-shrink:0;transition:background .15s;}
  .staging-toggle-knob{width:18px;height:18px;background:#fff;border-radius:50%;
    position:absolute;top:3px;box-shadow:0 1px 3px rgba(0,0,0,0.2);transition:left .15s,right .15s;}
  .staging-run-btn{width:100%;border:none;border-radius:10px;padding:13px;font-size:14px;
    font-weight:600;cursor:pointer;transition:background .15s,opacity .15s;}
  .staging-run-btn:disabled{cursor:not-allowed;opacity:0.5;}
"""

WIZARD_APP = r"""
/* ── Supabase client ── */
const _sb = window.supabase.createClient("__SUPABASE_URL__", "__SUPABASE_ANON_KEY__");

async function saveSubmission(inputs, data) {
  const { data: row, error } = await _sb.from('wizard_submissions').insert({
    address: inputs.address || null,
    zipcode: data.zipcode || null,
    bedrooms: inputs.bedrooms !== "" ? parseInt(inputs.bedrooms) : null,
    bathrooms: inputs.bathrooms !== "" ? parseFloat(inputs.bathrooms) : null,
    sqft: inputs.sqft !== "" ? parseInt(inputs.sqft) : null,
    property_type: inputs.propertyType || null,
    listing_price: inputs.listingPrice !== "" ? parseInt(inputs.listingPrice) : null,
    agent_name: inputs.agentName?.trim() || null,
    agent_contact: inputs.agentContact?.trim() || null,
    n_photos: data.n_photos || 0,
    classification_result: inputs.classificationResult || null,
    home_report: data.home_report || null,
    bi_analysis: data.bi_analysis || null,
    bi_explain: data.bi_explain || null,
    listing_text: data.listing_text || null,
    photo_urls: inputs.photoUrls || [],
  }).select('id').single();
  if (error) { console.error('[DB] saveSubmission error:', error); return null; }
  console.log('[DB] submission saved:', row.id);
  return row.id;
}

async function updateSubmissionListing(submissionId, listingText, style) {
  const { error } = await _sb.from('wizard_submissions')
    .update({ listing_text: listingText, listing_style: style })
    .eq('id', submissionId);
  if (error) console.error('[DB] updateSubmissionListing error:', error);
  else console.log('[DB] submission listing updated:', submissionId);
}

async function uploadPhotos(files, uploadBase) {
  const urls = await Promise.all(files.map(async (file, i) => {
    try {
      const sresp = await fetch(`${uploadBase}/v1/imageSignedUrl`);
      if (!sresp.ok) return null;
      const {key, signedUrl} = await sresp.json();
      const put = await fetch(signedUrl, {method: "PUT", headers: {"Content-Type": file.type || "image/jpeg"}, body: file});
      if (!put.ok) return null;
      const vresp = await fetch(`${uploadBase}/v1/imageValidations`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({key}),
      });
      if (!vresp.ok) return null;
      const {url} = await vresp.json();
      return url;
    } catch(e) { console.error('[upload] error:', i, e); return null; }
  }));
  return urls.filter(Boolean);
}

async function saveStagingRun(submissionId, d) {
  const { error } = await _sb.from('staging_runs').insert({
    submission_id: submissionId || null,
    room_type: d.roomType,
    style: d.style,
    remove_furniture: d.removeFurniture,
    image_urls: d.imageUrls,
    output_urls: d.outputUrls,
    job_id: d.jobId,
  });
  if (error) console.error('[DB] saveStagingRun error:', error);
  else console.log('[DB] staging run saved');
}

/* ── Wizard-specific components ── */
const ROOM_TYPE_COLORS = {
  bathroom:      {border:'#C8A96E',labelBg:'rgba(200,169,110,0.85)',headerBg:'#FBF5EA'},
  kitchen:       {border:'#A3B89A',labelBg:'rgba(107,142,97,0.85)', headerBg:'#F1F5EE'},
  bedroom:       {border:'#C4A9C8',labelBg:'rgba(160,120,168,0.85)',headerBg:'#F7F1F9'},
  living:        {border:'#94A3B8',labelBg:'rgba(100,116,139,0.85)',headerBg:'#F1F5F9'},
  living_bedroom:{border:'#B8A8C8',labelBg:'rgba(150,130,175,0.85)',headerBg:'#F5F1F9'},
  living_dining: {border:'#C4AA94',labelBg:'rgba(170,130,100,0.85)',headerBg:'#F9F3EE'},
  dining:        {border:'#C4957A',labelBg:'rgba(180,110,80,0.85)', headerBg:'#FAF0EB'},
  hallway:       {border:'#B8A094',labelBg:'rgba(150,120,108,0.85)',headerBg:'#F9F5F3'},
  home_office:   {border:'#94B8B4',labelBg:'rgba(80,140,140,0.85)', headerBg:'#EEF7F6'},
  balcony:       {border:'#94B8A0',labelBg:'rgba(80,150,120,0.85)', headerBg:'#F0F7F4'},
  outdoor:       {border:'#8AAE8A',labelBg:'rgba(90,145,90,0.85)',  headerBg:'#EEF5EE'},
  theatre:       {border:'#A894B8',labelBg:'rgba(130,100,160,0.85)',headerBg:'#F3F0F8'},
  kidsroom:      {border:'#C8AA80',labelBg:'rgba(190,155,90,0.85)', headerBg:'#FAF4E8'},
};
function roomColor(room_type) {
  return ROOM_TYPE_COLORS[room_type] || {border:'#B8B494',labelBg:'rgba(140,135,100,0.85)',headerBg:'#F7F5ED'};
}

const ROOM_ICONS = {
  kitchen:'🍳', bedroom:'🛏', bathroom:'🛁', living:'🛋',
  living_bedroom:'🛋', living_dining:'🍽', dining:'🍽',
  hallway:'🚪', home_office:'💼', balcony:'🌿', outdoor:'🌳',
  theatre:'🎬', kidsroom:'🧸',
};

function fmtRoom(r) {
  return (r || '').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}

function PhotoUpload({files, setFiles, location, setLocation, onRun, running,
                      classificationResult, classificationLoading, onClassify, onReview,
                      bedrooms, setBedrooms, bathrooms, setBathrooms, sqft, setSqft,
                      propertyType, setPropertyType, listingPrice, setListingPrice,
                      agentName, setAgentName, agentContact, setAgentContact,
                      readOnly, onRemoveFile}) {
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
  const remove = (i) => {
    setFiles(files.filter((_, idx) => idx !== i));
    onRemoveFile?.();
  };
  const locTrim = location.trim();
  const canRun = files.length > 0 && locTrim.length >= 6 && !!classificationResult;

  return (
    <div className="search-card">
      <div style={{fontSize:11,textTransform:"uppercase",letterSpacing:".12em",color:"var(--fg-subtle)",fontWeight:500,marginBottom:14}}>
        Property Photos & Location
      </div>
      {!readOnly && (
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
      )}
      {files.length > 0 && (
        <div className="thumbs">
          {files.map((f, i) => {
            const pr = classificationResult?.photos?.[i];
            const gc = pr ? roomColor(pr.room_type) : null;
            return (
              <div key={i} className="thumb"
                style={gc ? {border:`2.5px solid ${gc.border}`,borderRadius:'10px'} : {}}>
                <img src={URL.createObjectURL(f)} alt=""/>
                {!readOnly && <button className="x" onClick={()=>remove(i)} type="button">×</button>}
                {pr && (
                  <div className="thumb-label" style={{background:gc.labelBg}}>
                    {fmtRoom(pr.room_type)}{pr.occupancy==='empty'?' · Empty':''} · G{pr.group_id}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {!readOnly && files.length > 0 && (
        <div className="classify-bar">
          <span className="classify-hint">
            {classificationResult
              ? `${classificationResult.groups.length} group${classificationResult.groups.length!==1?'s':''} detected — click to review & adjust`
              : classificationLoading ? 'Classifying rooms…' : ''}
          </span>
          {classificationResult
            ? <button className="btn" style={{background:'#111',color:'#fff',padding:'9px 20px'}}
                onClick={onReview}>Edit Rooms →</button>
            : <button className="btn" style={{background:'#111',color:'#fff',padding:'9px 20px',
                opacity:classificationLoading?0.6:1}}
                disabled={classificationLoading} onClick={onClassify}>
                {classificationLoading ? 'Classifying…' : 'Classify Rooms →'}
              </button>
          }
        </div>
      )}
      {!readOnly && (
        <div className="wizard-form-row" style={{gridTemplateColumns:"1fr auto"}}>
          <div className="field">
            <label>Address</label>
            <input
              className="input"
              type="text"
              placeholder="20 Allston St, Boston MA"
              value={location}
              onChange={(e)=>setLocation(e.target.value)}
            />
          </div>
          <button className="submit-btn" disabled={!canRun || running} onClick={onRun}
            title={!classificationResult ? "Classify rooms first" : ""}>
            {running ? "Running…" : <>Run Pipeline <Ic.arrow/></>}
          </button>
          {!classificationResult && files.length > 0 && !running && (
            <div style={{fontSize:11,color:"var(--fg-subtle)",marginTop:4,textAlign:"center"}}>
              Classify rooms first to enable Run Pipeline
            </div>
          )}
          {!running && files.length > 0 && (
            <div style={{fontSize:12,color:"var(--fg-subtle)",marginTop:6,textAlign:"center"}}>
              {files.length <= 10
                ? `~${Math.round(files.length * 5 + 15)}s estimated`
                : `More photos = longer analysis — ${files.length} photos may take ${Math.round(files.length * 4 + 15)}s`}
            </div>
          )}
        </div>
      )}
      {!readOnly && (
        <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:"12px",marginTop:12}}>
          <div className="field">
            <label>Beds</label>
            <input className="input" type="number" min="0" max="20" placeholder="3"
              value={bedrooms} onChange={e=>setBedrooms(e.target.value)}/>
          </div>
          <div className="field">
            <label>Baths</label>
            <input className="input" type="number" min="0" max="20" step="0.5" placeholder="2"
              value={bathrooms} onChange={e=>setBathrooms(e.target.value)}/>
          </div>
          <div className="field">
            <label>Sqft</label>
            <input className="input" type="number" min="0" placeholder="1200"
              value={sqft} onChange={e=>setSqft(e.target.value)}/>
          </div>
          <div className="field">
            <label>Type</label>
            <select className="input" value={propertyType} onChange={e=>setPropertyType(e.target.value)}>
              <option value="residential">Residential</option>
              <option value="condo">Condo</option>
              <option value="commercial">Commercial</option>
            </select>
          </div>
        </div>
      )}
      {!readOnly && (
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:"12px",marginTop:12}}>
          <div className="field">
            <label>Listing Price</label>
            <input className="input" type="number" min="0" placeholder="650000"
              value={listingPrice} onChange={e=>setListingPrice(e.target.value)}/>
          </div>
          <div className="field">
            <label>Agent Name</label>
            <input className="input" type="text" placeholder="Jane Smith"
              value={agentName} onChange={e=>setAgentName(e.target.value)}/>
          </div>
          <div className="field">
            <label>Agent Contact</label>
            <input className="input" type="text" placeholder="617-555-0100"
              value={agentContact} onChange={e=>setAgentContact(e.target.value)}/>
          </div>
        </div>
      )}
    </div>
  );
}

function GroupingEditor({result, files, onConfirm, onClose}) {
  const [groups, setGroups] = useState(() => {
    if (!result) return {};
    const g = {};
    for (const gr of result.groups)
      g[String(gr.group_id)] = {room_type:gr.room_type, occupancy:gr.occupancy, photo_indices:[...gr.photo_indices]};
    return g;
  });
  const [nextId, setNextId] = useState(() => result ? Math.max(0, ...result.groups.map(g=>g.group_id)) + 1 : 1);
  const [selected, setSelected] = useState(null);
  const [dragging, setDragging] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const [collapsed, setCollapsed] = useState({});
  const [expandedGroups, setExpandedGroups] = useState(new Set());
  const [hoverPhoto, setHoverPhoto] = useState(null);
  const [hoverPos, setHoverPos] = useState({x:0, y:0});

  const rtMap = useMemo(() => {
    const m = {};
    for (const [gid, g] of Object.entries(groups)) {
      if (!m[g.room_type]) m[g.room_type] = [];
      m[g.room_type].push(gid);
    }
    return m;
  }, [groups]);

  const movePhoto = (photoIdx, targetGid) => {
    setGroups(prev => {
      const next = {};
      for (const [k, v] of Object.entries(prev))
        next[k] = {...v, photo_indices: v.photo_indices.filter(i => i !== photoIdx)};
      if (next[targetGid])
        next[targetGid] = {...next[targetGid], photo_indices:[...next[targetGid].photo_indices, photoIdx]};
      return next;
    });
    setSelected(null); setDragging(null); setDropTarget(null);
  };

  const addGroup = (room_type, occupancy) => {
    const gid = `n${nextId}`;
    setNextId(n => n+1);
    setGroups(prev => ({...prev, [gid]:{room_type, occupancy, photo_indices:[]}}));
    setCollapsed(c => ({...c, [room_type]:false}));
  };

  const toggleExpand = (gid, e) => {
    e.stopPropagation();
    setExpandedGroups(prev => {
      const next = new Set(prev);
      next.has(gid) ? next.delete(gid) : next.add(gid);
      return next;
    });
  };

  const handleConfirm = () => {
    const newPhotos = result.photos.map(p => ({...p}));
    const newGroups = [];
    let counter = 1;
    for (const [, g] of Object.entries(groups).sort((a,b) => a[1].room_type.localeCompare(b[1].room_type))) {
      if (!g.photo_indices.length) continue;
      const gid = counter++;
      newGroups.push({group_id:gid, room_type:g.room_type, occupancy:g.occupancy, photo_indices:[...g.photo_indices]});
      for (const idx of g.photo_indices)
        newPhotos[idx] = {...newPhotos[idx], group_id:gid, room_type:g.room_type, occupancy:g.occupancy};
    }
    onConfirm({photos:newPhotos, groups:newGroups});
  };

  if (!result) return null;
  const isMoving = selected !== null || dragging !== null;
  const movingIdx = selected !== null ? selected : dragging;

  return (
    <div className="review-panel">
      {/* Hover preview overlay — fixed, follows cursor */}
      {hoverPhoto !== null && files[hoverPhoto] && (
        <div style={{position:'fixed',left:hoverPos.x+16,top:Math.min(hoverPos.y-90, window.innerHeight-210),
          zIndex:9999,width:'33vw',height:'auto',maxWidth:480,minHeight:180,borderRadius:12,overflow:'hidden',
          boxShadow:'0 12px 40px rgba(0,0,0,0.28)',border:'2.5px solid #fff',pointerEvents:'none'}}>
          <img src={URL.createObjectURL(files[hoverPhoto])} alt=""
            style={{width:'100%',height:'100%',objectFit:'cover',display:'block'}}/>
        </div>
      )}

      <div className="review-header">
        <div>
          <h3 style={{margin:0,fontFamily:'var(--font-display)',fontSize:20,color:'var(--primary)'}}>
            Review & Adjust Rooms
          </h3>
          <div style={{fontSize:12,color:'var(--fg-subtle)',marginTop:3}}>
            {isMoving ? '↕ Click a group to move selected photo, or drag it there'
                      : 'Hover to preview · Click to select · Drag to rearrange'}
          </div>
        </div>
        <button className="btn ghost" onClick={onClose} style={{fontSize:12,padding:'6px 14px'}}>← Back</button>
      </div>

      {selected !== null && (
        <div style={{display:'flex',alignItems:'center',gap:10,padding:'8px 12px',background:'#EEF4FF',
          border:'1px solid #c0d4f5',borderRadius:10,marginBottom:12}}>
          <div style={{width:52,height:40,borderRadius:5,overflow:'hidden',flex:'none'}}>
            {files[selected] && <img src={URL.createObjectURL(files[selected])} alt=""
              style={{width:'100%',height:'100%',objectFit:'cover'}}/>}
          </div>
          <span style={{fontSize:12,color:'#4a90e2',fontWeight:500,flex:1}}>
            Photo {selected} selected — click any group to move it there
          </span>
          <button style={{fontSize:11,background:'none',border:'none',color:'#888',cursor:'pointer',padding:'2px 6px'}}
            onClick={() => setSelected(null)}>✕</button>
        </div>
      )}

      {Object.entries(rtMap).sort((a,b)=>a[0].localeCompare(b[0])).map(([rt, gids]) => {
        const gc = roomColor(rt);
        const isOpen = !collapsed[rt];
        const totalPhotos = gids.reduce((s,gid) => s + groups[gid].photo_indices.length, 0);
        return (
          <div key={rt} style={{border:`2px solid ${gc.border}`,borderRadius:10,overflow:'hidden',marginBottom:8}}>
            <div style={{background:gc.headerBg,padding:'8px 12px',display:'flex',
              justifyContent:'space-between',alignItems:'center',cursor:'pointer'}}
              onClick={() => setCollapsed(c => ({...c,[rt]:!c[rt]}))}>
              <span style={{fontWeight:700,fontSize:12,color:'#444'}}>
                {ROOM_ICONS[rt]||'🏠'} {fmtRoom(rt)}
                <span style={{fontWeight:400,fontSize:11,color:'#aaa',marginLeft:6}}>
                  {totalPhotos} photo{totalPhotos!==1?'s':''}
                </span>
              </span>
              <div style={{display:'flex',gap:6,alignItems:'center'}} onClick={e=>e.stopPropagation()}>
                <button
                  style={{fontSize:10,background:'none',border:`1px solid ${gc.border}`,borderRadius:99,
                    padding:'1px 8px',color:'#777',cursor:'pointer'}}
                  onClick={() => addGroup(rt, groups[gids[0]]?.occupancy||'furnished')}>
                  + group
                </button>
                <span style={{fontSize:10,color:'#bbb',marginLeft:2}}>{isOpen?'▼':'▶'}</span>
              </div>
            </div>

            {isOpen && gids.map((gid, li) => {
              const g = groups[gid];
              const isTarget = dropTarget === gid;
              const canDrop = isMoving && !g.photo_indices.includes(movingIdx);
              const isExpanded = expandedGroups.has(gid);
              const TW = isExpanded ? 200 : 80;
              const TH = isExpanded ? 150 : 62;
              return (
                <div key={gid}
                  style={{borderTop:'1px solid rgba(0,0,0,0.06)',
                    background: isTarget ? 'rgba(74,144,226,0.07)' : 'white',
                    outline: isTarget ? '2px solid #4a90e2' : (canDrop ? '1px dashed #d0d8e8' : 'none'),
                    outlineOffset: -2,
                    cursor: canDrop ? 'copy' : 'default',
                    transition: 'background 0.12s'}}
                  onDragOver={e => {e.preventDefault(); setDropTarget(gid);}}
                  onDragLeave={() => setDropTarget(t => t===gid ? null : t)}
                  onDrop={e => {e.preventDefault(); if (dragging!==null) movePhoto(dragging, gid);}}
                  onClick={() => {if (selected!==null && canDrop) movePhoto(selected, gid);}}>
                  <div style={{padding:'5px 12px 0',fontSize:10,display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                    <span style={{color:isTarget?'#4a90e2':'#bbb',fontWeight:600}}>
                      {isTarget ? '▸ Drop here' : `Group ${li+1}`}
                      {g.occupancy==='empty' && <span style={{marginLeft:5,opacity:0.7,fontWeight:400}}>· Empty</span>}
                    </span>
                    <button
                      style={{fontSize:10,background:'none',border:'none',color:'#bbb',cursor:'pointer',
                        padding:'0 4px',lineHeight:1}}
                      title={isExpanded ? 'Collapse' : 'Expand for larger view'}
                      onClick={e => toggleExpand(gid, e)}>
                      {isExpanded ? '⊟' : '⤢'}
                    </button>
                  </div>
                  <div style={{display:'flex',gap:8,padding:'6px 12px 10px',
                    flexWrap: isExpanded ? 'wrap' : 'nowrap',
                    overflowX: isExpanded ? 'visible' : 'auto',
                    minHeight: TH + 16}}>
                    {g.photo_indices.map(idx => {
                      const isSel = selected === idx;
                      return (
                        <div key={idx} draggable
                          style={{position:'relative',width:TW,height:TH,borderRadius:8,overflow:'visible',
                            border: isSel ? '2.5px solid #4a90e2' : '2.5px solid transparent',
                            cursor:'grab',flex:'none',transition:'width 0.18s,height 0.18s'}}
                          onMouseEnter={e => {setHoverPhoto(idx); setHoverPos({x:e.clientX,y:e.clientY});}}
                          onMouseMove={e => setHoverPos({x:e.clientX,y:e.clientY})}
                          onMouseLeave={() => setHoverPhoto(null)}
                          onDragStart={e => {setDragging(idx); setSelected(null); setHoverPhoto(null); e.dataTransfer.effectAllowed='move';}}
                          onDragEnd={() => {setDragging(null); setDropTarget(null);}}
                          onClick={e => {e.stopPropagation(); setSelected(p => p===idx ? null : idx);}}>
                          <div style={{width:'100%',height:'100%',borderRadius:6,overflow:'hidden'}}>
                            {files[idx] && <img src={URL.createObjectURL(files[idx])} alt=""
                              style={{width:'100%',height:'100%',objectFit:'cover',display:'block'}}/>}
                          </div>
                          {isSel && (
                            <div style={{position:'absolute',top:-4,right:-4,background:'#4a90e2',
                              borderRadius:'50%',width:16,height:16,display:'flex',alignItems:'center',
                              justifyContent:'center',fontSize:9,color:'#fff',fontWeight:700,zIndex:2}}>✓</div>
                          )}
                        </div>
                      );
                    })}
                    {g.photo_indices.length === 0 && (
                      <div style={{width:TW,height:TH,border:'1px dashed #ccc',borderRadius:8,
                        display:'flex',alignItems:'center',justifyContent:'center',fontSize:11,color:'#ccc'}}>
                        empty
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}

      <div className="review-footer">
        <button className="btn primary" onClick={handleConfirm}>Confirm Rooms →</button>
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
  if (!text || text.startsWith('[listing_error') || text.startsWith('[listing_exception')) return null;
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

/* ── StagingModal ── */
const STAGING_STYLES = ["Transitional","Modern","Scandinavian","Industrial",
  "Mid-Century Modern","Luxury","Coastal","Farmhouse","Standard"];

function StagingModal({files, classificationResult, defaultStyle, top1Style, biBase, submissionId, onClose}) {
  const [activeStyle, setActiveStyle] = React.useState(defaultStyle);
  const [selectedRooms, setSelectedRooms] = React.useState(new Set());
  const [imageUrls, setImageUrls] = React.useState({});
  const [uploadingRooms, setUploadingRooms] = React.useState(new Set());
  const [removeFurniture, setRemoveFurniture] = React.useState(true);
  const [jobMap, setJobMap] = React.useState({}); // {roomType: {jobId,status,outputUrls,error}}
  const [anyRunning, setAnyRunning] = React.useState(false);
  const [globalError, setGlobalError] = React.useState(null);
  const intervalsRef = React.useRef({});
  const activeJobsRef = React.useRef(0);
  const resultRef = React.useRef(null);

  React.useEffect(() => () => { Object.values(intervalsRef.current).forEach(clearInterval); }, []);
  React.useEffect(() => {
    const hasResult = Object.values(jobMap).some(j => j.status === 'COMPLETED');
    if (hasResult && resultRef.current) resultRef.current.scrollIntoView({behavior:'smooth'});
  }, [jobMap]);

  const roomGroups = React.useMemo(() => {
    const photos = classificationResult?.photos;
    if (!photos?.length) return {"All Photos": files.map((_, i) => i)};
    const g = {};
    photos.forEach((p, i) => {
      const rt = p.room_type || "unknown";
      if (!g[rt]) g[rt] = [];
      g[rt].push(p.photo_index != null ? p.photo_index : i);
    });
    return g;
  }, [classificationResult, files.length]);

  const toggleRoom = async (roomType) => {
    if (anyRunning) return;
    if (selectedRooms.has(roomType)) {
      setSelectedRooms(prev => { const n = new Set(prev); n.delete(roomType); return n; });
      const indices = roomGroups[roomType] || [];
      setImageUrls(prev => { const n = {...prev}; indices.forEach(i => delete n[i]); return n; });
      setJobMap(prev => { const n = {...prev}; delete n[roomType]; return n; });
      return;
    }
    setSelectedRooms(prev => new Set([...prev, roomType]));
    setUploadingRooms(prev => new Set([...prev, roomType]));
    const indices = roomGroups[roomType] || [];
    let errMsg = null;
    await Promise.all(indices.map(async (i) => {
      const file = files[i];
      if (!file) return;
      try {
        const b64 = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = e => res(e.target.result.split(",")[1]);
          r.onerror = rej;
          r.readAsDataURL(file);
        });
        const resp = await fetch(`${biBase}/upload`, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({filename:file.name, content_type:file.type||"image/jpeg", data:b64}),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const {url} = await resp.json();
        setImageUrls(prev => ({...prev, [i]: url}));
      } catch(e) { errMsg = `Upload failed (photo ${i+1}): ${e.message}`; }
    }));
    setUploadingRooms(prev => { const n = new Set(prev); n.delete(roomType); return n; });
    if (errMsg) setGlobalError(errMsg);
  };

  const runStaging = async () => {
    setGlobalError(null);
    try {
      await fetch(`${biBase}/health`, {headers: {"X-Requested-With": "fetch"}});
    } catch(e) {
      setGlobalError(`Cannot reach server: ${e.message}`); return;
    }
    setAnyRunning(true);
    activeJobsRef.current = 0;

    for (const roomType of selectedRooms) {
      const indices = roomGroups[roomType] || [];
      const urls = indices.map(i => imageUrls[i]).filter(Boolean);
      if (!urls.length) continue;
      const roomTypeLabel = roomType === "All Photos" ? "living room" : roomType.replace(/_/g, " ");
      try {
        const r = await fetch(`${biBase}/staging/run`, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({image_urls:urls, room_type_label:roomTypeLabel,
            style:activeStyle, remove_furniture:removeFurniture}),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const {job_id} = await r.json();
        setJobMap(prev => ({...prev, [roomType]: {jobId:job_id, status:'RUNNING', outputUrls:[], error:null}}));
        activeJobsRef.current++;
        const pollErr = {count: 0};
        let pollCount = 0;
        intervalsRef.current[roomType] = setInterval(async () => {
          pollCount++;
          const done = () => { activeJobsRef.current--; if (activeJobsRef.current <= 0) setAnyRunning(false); };
          if (pollCount > 60) {
            clearInterval(intervalsRef.current[roomType]);
            setJobMap(prev => ({...prev, [roomType]: {...(prev[roomType]||{}), status:'TIMEOUT', error:'Timed out'}}));
            done(); return;
          }
          const pollUrl = `${biBase}/staging/status/${job_id}`;
          try {
            const sr = await fetch(pollUrl, {headers: {"X-Requested-With": "fetch"}});
            if (!sr.ok) {
              clearInterval(intervalsRef.current[roomType]);
              setJobMap(prev => ({...prev, [roomType]: {...(prev[roomType]||{}), status:'FAILED', error:`HTTP ${sr.status}`}}));
              done(); return;
            }
            pollErr.count = 0;
            const sd = await sr.json();
            if (sd.status === "COMPLETED") {
              clearInterval(intervalsRef.current[roomType]);
              const outputUrls = sd.output_urls || [];
              setJobMap(prev => ({...prev, [roomType]: {jobId:job_id, status:'COMPLETED', outputUrls, error:null}}));
              saveStagingRun(submissionId, {roomType, style:activeStyle, removeFurniture, imageUrls:urls, outputUrls, jobId:job_id});
              done();
            } else if (sd.status === "FAILED") {
              clearInterval(intervalsRef.current[roomType]);
              setJobMap(prev => ({...prev, [roomType]: {...(prev[roomType]||{}), status:'FAILED', error:sd.error||'Staging failed'}}));
              done();
            }
          } catch(e) {
            pollErr.count++;
            if (pollErr.count >= 3) {
              clearInterval(intervalsRef.current[roomType]);
              setJobMap(prev => ({...prev, [roomType]: {...(prev[roomType]||{}), status:'FAILED', error:e.message}}));
              done();
            }
          }
        }, 3000);
      } catch(e) {
        setJobMap(prev => ({...prev, [roomType]: {jobId:null, status:'ERROR', outputUrls:[], error:e.message}}));
      }
    }
  };

  const allReady = selectedRooms.size > 0 && uploadingRooms.size === 0 &&
    [...selectedRooms].every(rt => (roomGroups[rt]||[]).every(i => !!imageUrls[i]));

  return (
    <div className="staging-overlay" onClick={e=>{if(e.target===e.currentTarget)onClose();}}>
      <div className="staging-box">
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:18}}>
          <div style={{fontWeight:700,fontSize:16,color:"var(--primary)"}}>✦ Go Staging</div>
          <button style={{background:"none",border:"none",fontSize:18,color:"#bbb",cursor:"pointer"}} onClick={onClose}>✕</button>
        </div>

        <div style={{fontSize:10,textTransform:"uppercase",letterSpacing:".1em",color:"#888",marginBottom:8,fontWeight:600}}>Staging Style</div>
        <div style={{display:"flex",flexWrap:"wrap",gap:6,marginBottom:18}}>
          {STAGING_STYLES.map(st => (
            <span key={st} className={`staging-style-pill${activeStyle===st?" active":""}`} onClick={()=>setActiveStyle(st)}>
              {st}{st===top1Style?" ★":""}
            </span>
          ))}
        </div>

        <div style={{fontSize:10,textTransform:"uppercase",letterSpacing:".1em",color:"#888",marginBottom:8,fontWeight:600}}>
          Select Rooms
          <span style={{fontWeight:400,textTransform:"none",letterSpacing:0,color:"#bbb",marginLeft:6}}>tap to select multiple</span>
        </div>
        <div className="staging-room-grid">
          {Object.entries(roomGroups).map(([roomType, indices]) => {
            const firstFile = files[indices[0]];
            const gc = roomColor(roomType);
            const isActive = selectedRooms.has(roomType);
            const isUploading = uploadingRooms.has(roomType);
            const ready = isActive && !isUploading && indices.every(i => !!imageUrls[i]);
            const job = jobMap[roomType];
            return (
              <div key={roomType}
                className={`staging-room-card${isActive?" active":""}`}
                style={{borderColor:isActive?"var(--primary)":(gc?.border||"#e0d8d0"),opacity:anyRunning?0.85:1}}
                onClick={()=>toggleRoom(roomType)}>
                <div className="staging-room-preview">
                  {firstFile && <img src={URL.createObjectURL(firstFile)} alt=""/>}
                  <div className="staging-room-badge">{indices.length} photo{indices.length>1?"s":""}</div>
                  {isUploading && (
                    <div style={{position:"absolute",inset:0,background:"rgba(0,0,0,0.5)",
                      display:"flex",alignItems:"center",justifyContent:"center",fontSize:11,color:"#fff",fontWeight:700}}>
                      uploading…
                    </div>
                  )}
                  {ready && !job && (
                    <div style={{position:"absolute",top:4,left:4,background:"var(--primary)",
                      borderRadius:"50%",width:20,height:20,display:"flex",alignItems:"center",
                      justifyContent:"center",fontSize:11,color:"#fff",fontWeight:700}}>✓</div>
                  )}
                  {job?.status === 'RUNNING' && (
                    <div style={{position:"absolute",inset:0,background:"rgba(0,0,0,0.45)",
                      display:"flex",alignItems:"center",justifyContent:"center",fontSize:11,color:"#fff",fontWeight:700}}>
                      Processing…
                    </div>
                  )}
                  {job?.status === 'COMPLETED' && (
                    <div style={{position:"absolute",top:4,left:4,background:"#16a34a",
                      borderRadius:"50%",width:20,height:20,display:"flex",alignItems:"center",
                      justifyContent:"center",fontSize:11,color:"#fff",fontWeight:700}}>✓</div>
                  )}
                  {(job?.status === 'FAILED' || job?.status === 'ERROR' || job?.status === 'TIMEOUT') && (
                    <div style={{position:"absolute",top:4,left:4,background:"#dc2626",
                      borderRadius:"50%",width:20,height:20,display:"flex",alignItems:"center",
                      justifyContent:"center",fontSize:11,color:"#fff",fontWeight:700}}>✕</div>
                  )}
                </div>
                <div style={{padding:"5px 8px",fontSize:12,fontWeight:600,
                  color:isActive?"var(--primary)":"#666",textTransform:"capitalize"}}>
                  {fmtRoom(roomType==="All Photos"?"All Photos":roomType)}
                </div>
              </div>
            );
          })}
        </div>

        {selectedRooms.size > 0 && (
          <div style={{fontSize:11,color:allReady?"#16a34a":"#888",marginBottom:10}}>
            {uploadingRooms.size > 0
              ? `Uploading ${uploadingRooms.size} room${uploadingRooms.size>1?"s":""}…`
              : allReady
                ? `✓ ${selectedRooms.size} room${selectedRooms.size>1?"s":""} ready`
                : "Preparing…"}
          </div>
        )}

        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",
          background:"#faf8f5",border:"1px solid #e8e4e0",borderRadius:10,padding:"12px 14px",marginBottom:16}}>
          <div>
            <div style={{fontSize:13,fontWeight:600,color:"var(--primary)"}}>Remove existing furniture</div>
            <div style={{fontSize:11,color:"#888",marginTop:2}}>AI clears all furniture before staging</div>
          </div>
          <div className="staging-toggle" style={{background:removeFurniture?"var(--primary)":"#ddd"}}
            onClick={()=>setRemoveFurniture(v=>!v)}>
            <div className="staging-toggle-knob" style={{[removeFurniture?"right":"left"]:3}}/>
          </div>
        </div>

        <button className="staging-run-btn"
          style={{background:allReady&&!anyRunning?"var(--primary)":"#ccc",color:"#fff"}}
          disabled={!allReady||anyRunning} onClick={runStaging}>
          {anyRunning ? "Processing…" : `Run Staging${selectedRooms.size>1?` (${selectedRooms.size} rooms)`:""} →`}
        </button>
        <div style={{textAlign:"center",fontSize:10,color:"#bbb",marginTop:7}}>
          {`HD · ${activeStyle} · remove: ${removeFurniture?"on":"off"} · sub-style randomized`}
        </div>

        {Object.entries(jobMap).some(([,j]) => j.status==='COMPLETED'||j.status==='FAILED'||j.status==='ERROR'||j.status==='TIMEOUT') && (
          <div ref={resultRef} style={{marginTop:16}}>
            {Object.entries(jobMap).map(([roomType, job]) => (
              job.status==='COMPLETED' || job.status==='FAILED' || job.status==='ERROR' || job.status==='TIMEOUT' ? (
                <div key={roomType} style={{marginBottom:16}}>
                  <div style={{fontSize:11,fontWeight:600,color:"var(--primary)",marginBottom:6}}>
                    ✦ {fmtRoom(roomType)}{job.outputUrls?.length>1?` (${job.outputUrls.length} photos)`:""}
                    {job.status!=='COMPLETED' && <span style={{color:"#dc2626",marginLeft:6}}>— {job.status.toLowerCase()}</span>}
                  </div>
                  {job.outputUrls?.map((url, i) => (
                    <img key={i} src={url} alt={`Staged result ${i+1}`}
                      style={{width:"100%",borderRadius:10,display:"block",marginBottom:i<job.outputUrls.length-1?8:0}}/>
                  ))}
                  {job.error && job.status!=='COMPLETED' && (
                    <div style={{padding:"8px 12px",background:"#fee2e2",borderRadius:8,fontSize:11,color:"#991b1b"}}>{job.error}</div>
                  )}
                </div>
              ) : null
            ))}
          </div>
        )}

        {globalError && (
          <div style={{marginTop:12,padding:"10px 14px",background:"#fee2e2",borderRadius:8,fontSize:12,color:"#991b1b"}}>
            {globalError}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── App ── */
function App() {
  const [apiBase] = useState(()=>{const o=window.location.origin;return o.includes(".proxy.runpod.net")?o.replace(/-\d+\.proxy\.runpod\.net/,"-8002.proxy.runpod.net"):"http://localhost:8002";});           // Tool service (Wizard's backend)
  const [biBase]  = useState(()=>window.location.origin==="null"?"http://localhost:8000":window.location.origin);
  const [page, setPage] = useState("wizard");
  const [files, setFiles] = useState([]);
  const [location, setLocation] = useState("");        // property address
  const [bedrooms, setBedrooms] = useState("");
  const [bathrooms, setBathrooms] = useState("");
  const [sqft, setSqft] = useState("");
  const [propertyType, setPropertyType] = useState("residential");
  const [listingPrice, setListingPrice] = useState("");
  const [agentName, setAgentName] = useState("");
  const [agentContact, setAgentContact] = useState("");
  const [running, setRunning] = useState(false);
  const [progressStep, setProgressStep] = useState(0);
  const [result, setResult] = useState(null);        // { home_report, bi_analysis, listing_text }
  const [mapped, setMapped] = useState(null);        // mapAnalysis output
  const [error, setError] = useState(null);
  const [activeStyleId, setActiveStyleId] = useState("");
  const [selectedCard, setSelectedCard] = useState("");
  const [apiOk, setApiOk] = useState(null);
  const [classificationResult, setClassificationResult] = useState(null);
  const [classificationLoading, setClassificationLoading] = useState(false);
  const [classificationError, setClassificationError] = useState(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [stagingModalStyle, setStagingModalStyle] = useState(null);
  const [stagingTop1, setStagingTop1] = useState("");
  const [stagingBase] = useState(()=>{const o=window.location.origin;return o.includes(".proxy.runpod.net")?o.replace(/-\d+\.proxy\.runpod\.net/,"-8000.proxy.runpod.net"):"http://localhost:8000";});
  const [submissionId, setSubmissionId] = useState(null);
  const [listingText, setListingText] = useState(null);
  const [listingStyle, setListingStyle] = useState(null);
  const [listingLoadingId, setListingLoadingId] = useState(null);
  const [listingError, setListingError] = useState(null);

  const openStagingModal = (styleName) => {
    setStagingTop1(mapped?.styles?.[0]?.name || "");
    setStagingModalStyle(styleName);
  };

  const runPipeline = async () => {
    setError(null);
    setResult(null);
    setMapped(null);
    setClassificationError(null);
    setReviewOpen(false);
    setRunning(true);
    setProgressStep(0);

    // Fake-advance the visible steps while the request runs
    const t1 = setTimeout(()=>setProgressStep(1), 1500);
    const t2 = setTimeout(()=>setProgressStep(2), 18000);
    const t3 = setTimeout(()=>setProgressStep(3), 28000);

    try {
      const form = new FormData();
      const locTrim = location.trim();
      form.append("address", locTrim);
      if (bedrooms !== "") form.append("bedrooms", bedrooms);
      if (bathrooms !== "") form.append("bathrooms", bathrooms);
      if (sqft !== "") form.append("sqft", sqft);
      form.append("property_type", propertyType);
      if (listingPrice !== "") form.append("listing_price", listingPrice);
      if (agentName.trim()) form.append("agent_name", agentName.trim());
      if (agentContact.trim()) form.append("agent_contact", agentContact.trim());
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
      uploadPhotos(files, "https://analytics.edensign.io").then(photoUrls =>
        saveSubmission(
          {address:location.trim(), bedrooms, bathrooms, sqft, propertyType, listingPrice, agentName, agentContact, classificationResult, photoUrls},
          data
        ).then(id => { if (id) setSubmissionId(id); })
      );
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

  const generateListing = async (s) => {
    if (listingLoadingId !== null) return;            // guard rapid double-clicks
    setListingLoadingId(s.id);
    setListingError(null);
    try {
      const body = {
        style: s.name,
        home_report: result?.home_report || null,
        address: location.trim() || null,
        zipcode: result?.zipcode || null,
        bedrooms: bedrooms !== "" ? parseInt(bedrooms) : null,
        bathrooms: bathrooms !== "" ? parseFloat(bathrooms) : null,
        sqft: sqft !== "" ? parseInt(sqft) : null,
        property_type: propertyType || "residential",
        listing_price: listingPrice !== "" ? parseInt(listingPrice) : null,
        agent_name: agentName.trim() || null,
        agent_contact: agentContact.trim() || null,
      };
      const r = await fetch(`${apiBase}/generate-listing`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setListingText(data.listing_text);
      setListingStyle(s.name);
      if (submissionId) updateSubmissionListing(submissionId, data.listing_text, s.name);
    } catch (e) {
      setListingError(e.message);
    } finally {
      setListingLoadingId(null);
    }
  };

  const classifyRooms = async () => {
    setClassificationLoading(true);
    setClassificationError(null);
    try {
      const imageUrls = await uploadPhotos(files, "https://analytics.edensign.io");
      if (!imageUrls.length) { setClassificationError("Photo upload failed - no image URLs."); return; }
      const r = await fetch(`${apiBase}/classify-rooms`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({image_urls: imageUrls}),
      });
      if (!r.ok) {
        const body = await r.json().catch(()=>({}));
        if (r.status === 503) {
          setClassificationError("Room classification service is not running.");
          return;
        }
        throw new Error(`HTTP ${r.status}`);
      }
      setClassificationResult(await r.json());
    } catch(e) {
      setClassificationError(e.message);
    } finally {
      setClassificationLoading(false);
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

          <PhotoUpload
            files={files} setFiles={setFiles}
            location={location} setLocation={setLocation}
            onRun={runPipeline} running={running}
            classificationResult={classificationResult}
            classificationLoading={classificationLoading}
            onClassify={classifyRooms}
            onReview={()=>setReviewOpen(true)}
            bedrooms={bedrooms} setBedrooms={setBedrooms}
            bathrooms={bathrooms} setBathrooms={setBathrooms}
            sqft={sqft} setSqft={setSqft}
            propertyType={propertyType} setPropertyType={setPropertyType}
            listingPrice={listingPrice} setListingPrice={setListingPrice}
            agentName={agentName} setAgentName={setAgentName}
            agentContact={agentContact} setAgentContact={setAgentContact}
            readOnly={running || !!result}
            onRemoveFile={()=>{setClassificationResult(null);setClassificationError(null);}}
          />
          {!running && !result && (
            <>
              {classificationError && (
                <div className="error-banner" style={{marginTop:8}}>
                  <Ic.alert style={{flex:"none",marginTop:1}}/>
                  <span>{classificationError}</span>
                </div>
              )}
              {reviewOpen && (
                <GroupingEditor
                  result={classificationResult}
                  files={files}
                  onConfirm={(edited) => {setClassificationResult(edited); setReviewOpen(false);}}
                  onClose={()=>setReviewOpen(false)}
                />
              )}
            </>
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
                  <StyleCard key={s.id} s={s} selected={selectedCard===s.id} onSelect={onSelectCard}
                    onGoStaging={classificationResult ? ()=>openStagingModal(s.name) : undefined}/>
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
                <button className="btn ghost" onClick={()=>{setResult(null);setMapped(null);setFiles([]);setLocation("");setClassificationResult(null);setClassificationError(null);setReviewOpen(false);}}>
                  ← Start over
                </button>
              </div>
            </>
          )}
        </div>
          {stagingModalStyle && (
            <StagingModal
              files={files}
              classificationResult={classificationResult}
              defaultStyle={stagingModalStyle}
              top1Style={stagingTop1}
              biBase={stagingBase}
              submissionId={submissionId}
              onClose={()=>setStagingModalStyle(null)}
            />
          )}
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
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
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
out = out.replace("__SUPABASE_URL__", SUPABASE_URL)
out = out.replace("__SUPABASE_ANON_KEY__", SUPABASE_ANON_KEY)
out_path = ROOT / "wizard.html"
out_path.write_text(out)
print(f"Wrote {out_path} ({len(out)} bytes, {out.count(chr(10))} lines)")
