// =============================================================================
// GTM Orchestrator v10 — Complete Frontend
// Every endpoint wired. Zero emojis. Zero dead buttons.
// =============================================================================

const API_BASE = "";
const H_AUTH = {};
let CURRENT_TAB = "caps";

// =============================================================================
// TAB SWITCHING
// =============================================================================
document.querySelectorAll('.nav-list button[data-tab]').forEach(b => {
  b.addEventListener('click', () => switchTab(b.dataset.tab));
});
function switchTab(name){
  CURRENT_TAB = name;
  document.querySelectorAll('.nav-list button[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
  // Load on tab change
  const loader = TAB_LOADERS[name];
  if (loader) loader();
}
const TAB_LOADERS = {
  caps: loadCaps,
  discover: loadDiscover,
  leads: loadLeads,
  "leads-enrich": () => {},
  funnel: loadFunnel,
  qualify: () => {},
  icp: () => {},
  prospex: () => {},
  identity: () => {},
  "crm-evidence": () => {},
  "draft-outbound": () => {},
  "draft-reply": () => {},
  drafts: loadDrafts,
  deal: () => {},
  health: () => {},
  social: loadSocial,
  browser: loadBrowser,
  outreach: () => {},
  voice: () => {},
  pipeline: () => {},
  memory: () => {},
  cognitive: () => {},
  constitutional: () => {},
  compliance: () => {},
  gdpr: () => {},
  security: () => {},
  observability: () => {},
  webhooks: () => {},
  dsh: refreshDsh,
  chat: () => {},
};

// =============================================================================
// HELPERS
// =============================================================================
async function api(method, path, body){
  const opts = { method, headers: { "Content-Type": "application/json", ...H_AUTH } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(API_BASE + path, opts);
  const ct = r.headers.get("content-type") || "";
  let data;
  if (ct.includes("json")) data = await r.json();
  else data = await r.text();
  if (!r.ok) {
    const err = (typeof data === "object" && data && data.error) || ("HTTP " + r.status);
    throw new Error(err);
  }
  return data;
}
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]); }
function pretty(o){ try { return JSON.stringify(o, null, 2); } catch { return String(o); } }

// Strip emojis from any text shown in the UI (defense in depth — server also strips)
function stripEmojis(s){
  if (!s) return s;
  // Variation selectors, ZWJ, etc.
  s = s.replace(/[\u{FE0F}\u{FE0E}\u{200D}\u{200B}\u{200C}\u{200E}\u{200F}\u{FEFF}\u{00AD}]/gu, '');
  // Match all common emoji ranges using a single unicode regex
  s = s.replace(/[\u{1F300}-\u{1FAFF}\u{1F1E6}-\u{1F1FF}\u{2600}-\u{27BF}\u{2300}-\u{23FF}\u{2B00}-\u{2BFF}]/gu, '');
  return s;
}
function setHTML(id, html){
  const el = document.getElementById(id);
  if (!el) return;
  // Defense in depth: strip emojis from any user-visible content
  el.innerHTML = stripEmojis(html);
}
function out(id, text){ setHTML(id, '<pre style="white-space:pre-wrap">' + esc(text) + '</pre>'); }
function outJSON(id, obj){ setHTML(id, '<pre style="white-space:pre-wrap">' + esc(pretty(obj)) + '</pre>'); }
function err(id, e){ setHTML(id, '<span class="tag-pill bad">' + esc(String(e && e.message || e)) + '</span>'); }

// =============================================================================
// INIT
// =============================================================================
async function init(){
  const key = window.prompt("API key (paste from keys/.orchestrator_api_key.txt) — leave empty for open mode");
  if (key) H_AUTH["Authorization"] = "Bearer " + key;
  await runHealth();
  loadCaps();
}
init();

// =============================================================================
// TOP BAR HEALTH
// =============================================================================
async function runHealth(){
  try {
    const r = await api("GET", "/health");
    const llm = r.llm_live ? "LLM live" : "LLM down";
    setHTML("topbar-msg", llm + " &middot; " + r.integrations.twilio + " &middot; " + r.integrations.sendgrid);
    document.querySelector(".topbar-status .dot").style.background = r.llm_live ? "#3dd5f4" : "#ff8888";
  } catch (e) {
    setHTML("topbar-msg", "error: " + e.message);
  }
}
async function runReadyz(){
  try {
    const r = await api("GET", "/readyz");
    setHTML("topbar-msg", "ready &middot; " + JSON.stringify(r.breakers || r));
  } catch (e) { setHTML("topbar-msg", "not ready: " + e.message); }
}

// =============================================================================
// OVERVIEW
// =============================================================================
async function loadCaps(){
  try {
    const r = await api("GET", "/api/capabilities");
    let html = '<div style="display:grid;gap:6px;font-size:11px">';
    (r.capabilities || []).slice(0, 30).forEach(c => {
      html += '<div style="padding:6px;background:rgba(255,255,255,0.03);border-radius:4px"><strong>' + esc(c.name || c.id || "?") + "</strong>";
      if (c.description) html += "<br>" + esc(String(c.description).slice(0, 80));
      html += "</div>";
    });
    html += "</div>";
    setHTML("sys-caps", html);
  } catch (e) { err("sys-caps", e); }
  try {
    const r = await api("GET", "/api/social/channels");
    let html = "<div>WRITE: " + r.channels.length + " &middot; BROWSER: " + (r.browser_channels || []).length + " &middot; READ: " + r.agent_reach_platforms.length + "</div>";
    setHTML("sys-channels", html);
  } catch (e) { err("sys-channels", e); }
  try {
    const r = await api("GET", "/health");
    setHTML("sys-stats", "<pre style='font-size:10px'>" + esc(pretty(r)) + "</pre>");
  } catch (e) { err("sys-stats", e); }
}

// =============================================================================
// DISCOVER
// =============================================================================
async function loadDiscover(){}
async function runGoogleMaps(){
  const out = "gmaps-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/google_maps/prospect", {
      query: document.getElementById("gmaps-query").value,
      city: document.getElementById("gmaps-city").value,
      count: parseInt(document.getElementById("gmaps-count").value || "5"),
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runExtract(){
  const out = "extract-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/internet/extract_leads", {
      url: document.getElementById("extract-url").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// LEADS
// =============================================================================
async function loadLeads(){
  const out = "leads-list";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/leads");
    if (!Array.isArray(r) || r.length === 0) { setHTML(out, "<em>no leads</em>"); return; }
    let html = '<table style="width:100%;font-size:11px"><tr><th>id</th><th>name</th><th>email</th><th>city</th><th>industry</th><th>icp</th><th>tier</th></tr>';
    r.slice(0, 50).forEach(l => {
      html += '<tr>' +
        '<td>' + esc(l.id) + '</td>' +
        '<td>' + esc(l.business_name || l.contact_name) + '</td>' +
        '<td>' + esc(l.contact_email) + '</td>' +
        '<td>' + esc(l.city) + '</td>' +
        '<td>' + esc(l.industry) + '</td>' +
        '<td>' + esc(l.icp_score) + '</td>' +
        '<td>' + esc(l.icp_tier) + '</td>' +
        '</tr>';
    });
    html += "</table>";
    setHTML(out, html);
  } catch (e) { err(out, e); }
}
async function runEnrich(){
  const out = "enrich-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/leads/enrich", { lead_id: parseInt(document.getElementById("enrich-id").value) });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadFunnel(){
  const out = "funnel-data";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/funnel");
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// QUALIFY / ICP / PROSPEX / IDENTITY / CRM
// =============================================================================
async function runQualify(){
  const out = "qualify-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/qualify", {
      name: document.getElementById("q-name").value,
      company: document.getElementById("q-company").value,
      email: document.getElementById("q-email").value,
      message: document.getElementById("q-message").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runIcpTrain(){
  const out = "icp-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/icp/train", {
      text: document.getElementById("icp-text").value,
      label: document.getElementById("icp-label").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runIcpScore(){
  const out = "icp-score-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/qualify", {
      name: "Test", company: document.getElementById("icp-lead-company").value,
      email: document.getElementById("icp-lead-email").value,
      message: "Looking for lead routing tool",
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runProspexScore(){
  const out = "ps-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/prospex/score", {
      lead: {
        email: document.getElementById("ps-lead-email").value,
        company: document.getElementById("ps-lead-company").value,
        city: document.getElementById("ps-lead-city").value,
      },
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runProspexBatch(){
  const out = "ps-batch-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const leads = JSON.parse(document.getElementById("ps-batch").value);
    const r = await api("POST", "/api/prospex/score_batch", { leads });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runIdentity(){
  const out = "id-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/crm/identity_verdict", {
      name: document.getElementById("id-name").value,
      email: document.getElementById("id-email").value,
      employer: document.getElementById("id-employer").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runCrmEvidence(){
  const out = "ev-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const weights = document.getElementById("ev-weights").value.split(",").map(parseFloat);
    const r = await api("POST", "/api/crm/score_evidence", { weights });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// DRAFTS
// =============================================================================
async function runDraftOutbound(){
  const out = "ob-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/draft/outbound", {
      contact_name: document.getElementById("ob-contact").value,
      company_name: document.getElementById("ob-company").value,
      industry: document.getElementById("ob-industry").value,
      campaign_name: document.getElementById("ob-campaign").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runDraftReply(){
  const out = "dr-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/draft/reply", {
      name: document.getElementById("dr-name").value,
      company: document.getElementById("dr-company").value,
      message: document.getElementById("dr-message").value,
      lead_id: parseInt(document.getElementById("dr-lead-id").value || "0"),
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadDrafts(){
  const out = "drafts-list";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const status = document.getElementById("drafts-status").value;
    const r = await api("GET", "/api/drafts" + (status ? "?status=" + status : ""));
    if (!Array.isArray(r) || r.length === 0) { setHTML(out, "<em>no drafts</em>"); return; }
    let html = '<div style="display:grid;gap:6px">';
    r.slice(0, 20).forEach(d => {
      html += '<div style="padding:8px;background:rgba(255,255,255,0.03);border-radius:6px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">' +
        '<span style="font-family:var(--font-mono);font-size:11px">id=' + esc(d.id) + ' lead_id=' + esc(d.lead_id) + ' channel=' + esc(d.channel) + '</span>' +
        '<span class="tag-pill">' + esc(d.status) + '</span></div>' +
        '<div style="font-size:11px"><strong>' + esc(d.subject) + '</strong></div>' +
        '<div style="font-size:10px;color:var(--muted);margin-top:4px">' + esc(String(d.body).slice(0, 200)) + '</div>' +
        '<div style="margin-top:6px;display:flex;gap:4px">' +
        (d.status === "pending" ? '<button class="btn small" onclick="approveDraft(' + d.id + ')">Approve</button><button class="btn small" onclick="rejectDraft(' + d.id + ')">Reject</button>' : '') +
        (d.status === "approved" ? '<button class="btn small primary" onclick="sendDraft(' + d.id + ', \'email\')">Send</button>' : '') +
        '</div></div>';
    });
    html += "</div>";
    setHTML(out, html);
  } catch (e) { err(out, e); }
}
async function approveDraft(id){
  try { await api("POST", "/api/drafts/" + id + "/approve", {}); loadDrafts(); } catch (e) { alert(e.message); }
}
async function rejectDraft(id){
  try { await api("POST", "/api/drafts/" + id + "/reject", {}); loadDrafts(); } catch (e) { alert(e.message); }
}
async function sendDraft(id, channel){
  const out = "send-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/drafts/send", { draft_id: id, channel });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
window.approveDraft = approveDraft;
window.rejectDraft = rejectDraft;
window.sendDraft = sendDraft;

// =============================================================================
// DEAL / HEALTH
// =============================================================================
async function runDealRisk(){
  const out = "dr-risk-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const deal = JSON.parse(document.getElementById("dr-deal").value);
    const r = await api("POST", "/api/deal/risk", { deal });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runHealthScore(){
  const out = "ah-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const account = JSON.parse(document.getElementById("ah-account").value);
    const r = await api("POST", "/api/account/health", { account });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// SOCIAL
// =============================================================================
async function loadSocial(){
  const out = "social-channels";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/social/channels");
    let html = '<div style="display:grid;gap:4px;font-size:11px">';
    (r.channels || []).forEach(c => {
      const badge = c.status === "live" ? '<span class="tag-pill good">live</span>' : (c.status === "read-only" ? '<span class="tag-pill muted">read-only</span>' : '<span class="tag-pill bad">dry-run</span>');
      html += '<div style="display:flex;justify-content:space-between;padding:6px 10px;background:rgba(255,255,255,0.03);border-radius:4px"><span style="font-family:var(--font-mono)">' + esc(c.name) + '</span>' + badge + '</div>';
    });
    html += "</div>";
    setHTML(out, html);
    setHTML("social-read-platforms", (r.agent_reach_platforms || []).map(p => '<span class="tag-pill" style="font-size:10px">' + esc(p) + '</span>').join(" "));
  } catch (e) { err(out, e); }
}
async function testSocialChannel(){
  const out = "social-test-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const ch = document.getElementById("social-test-channel").value;
    const r = await api("POST", "/api/social/test", ch ? { channel: ch } : {});
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function doReadSocial(){
  const out = "read-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/agent_reach/read", {
      platform: document.getElementById("read-platform").value,
      target: document.getElementById("read-target").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// BROWSER
// =============================================================================
async function loadBrowser(){
  for (const [id, url, fn] of [
    ["browser-setup-status", "/api/browser/setup", null],
    ["browser-channels-list", "/api/browser/channels", null],
    ["flows-list", "/api/flows/list", "flows"],
  ]) {
    setHTML(id, '<span class="spinner"></span>');
    try {
      const r = await api("GET", url);
      if (id === "browser-setup-status") {
        const s = r.status;
        let h = '<div style="font-size:12px;line-height:1.7">';
        h += '<div>Chrome: ' + (s.chrome.chrome_installed ? '<span class="tag-pill good">yes</span>' : '<span class="tag-pill bad">no</span>') + '</div>';
        h += '<div>User profile: ' + (s.chrome.user_data_dir ? '<span class="tag-pill good">found</span>' : '<span class="tag-pill bad">not found</span>') + '</div>';
        h += '<div>opencli: ' + (s.opencli_installed ? '<span class="tag-pill good">installed</span>' : '<span class="tag-pill bad">no</span>') + '</div>';
        h += '<div>Browser ready: ' + (s.ready_for_browser_automation ? '<span class="tag-pill good">yes</span>' : '<span class="tag-pill bad">needs setup</span>') + '</div>';
        h += '</div>';
        if (s.next_steps && s.next_steps.length) h += '<div style="margin-top:10px;font-size:11px"><strong>Next:</strong><ul style="padding-left:18px">' + s.next_steps.map(x => '<li>' + esc(x) + '</li>').join("") + '</ul></div>';
        setHTML(id, h);
      } else if (id === "browser-channels-list") {
        let h = '<div style="display:grid;gap:4px;font-size:11px">';
        (r.browser_channels || []).forEach(c => {
          const badge = c.status === "unavailable" ? '<span class="tag-pill bad">unavailable</span>' : '<span class="tag-pill good">' + esc(c.status) + '</span>';
          h += '<div style="display:flex;justify-content:space-between;padding:6px;background:rgba(255,255,255,0.03);border-radius:4px"><span style="font-family:var(--font-mono)">' + esc(c.name) + '</span>' + badge + '</div>';
        });
        h += "</div>";
        setHTML(id, h);
      } else if (id === "flows-list") {
        let h = "";
        (r.flows || []).forEach(f => {
          h += '<div style="padding:10px;background:rgba(255,255,255,0.03);border-radius:6px">' +
            '<div style="display:flex;justify-content:space-between;margin-bottom:4px">' +
            '<span style="font-family:var(--font-mono);font-weight:600">' + esc(f.name) + '</span>' +
            '<span class="tag-pill" style="font-size:10px">' + esc(f.average_runtime_seconds + "s") + '</span></div>' +
            '<div style="font-size:11px;color:var(--muted)">' + esc(f.description) + '</div></div>';
        });
        setHTML(id, h);
      }
    } catch (e) { err(id, e); }
  }
}
async function runFlow(name){
  const outMap = {
    cross_post_content: "flow-cross-result",
    quote_to_post: "flow-quote-result",
    social_listening: "flow-listening-result",
    auto_respond_dm: "flow-ar-result",
  };
  const out = outMap[name] || "flow-result";
  setHTML(out, '<span class="spinner"></span>');
  let body = {};
  if (name === "cross_post_content") body = {
    text: document.getElementById("flow-cross-text").value,
    hashtags: document.getElementById("flow-cross-tags").value.split(",").map(s => s.trim()).filter(Boolean),
    channels: ["linkedin", "twitter", "facebook", "reddit"],
  };
  else if (name === "quote_to_post") body = {
    quote: document.getElementById("flow-quote-text").value,
    author: document.getElementById("flow-quote-author").value,
    channels: ["linkedin", "twitter", "facebook"],
  };
  else if (name === "social_listening") {
    const checks = document.querySelectorAll(".flow-platform:checked");
    body = { brand: document.getElementById("flow-listening-brand").value, platforms: Array.from(checks).map(c => c.value) };
  }
  else if (name === "auto_respond_dm") {
    body = { platform: document.getElementById("flow-ar-platform").value, since_minutes: 60 };
  }
  try {
    const r = await api("POST", "/api/flows/run/" + name, body);
    let html = '<div class="tag-pill good">' + esc(name) + " done</div>";
    (r.steps || []).forEach(step => {
      const ok = step.ok ? '<span class="tag-pill good">ok</span>' : '<span class="tag-pill bad">fail</span>';
      html += '<div style="margin-top:8px;padding:8px;background:rgba(255,255,255,0.03);border-radius:6px">' +
        '<div style="display:flex;justify-content:space-between;margin-bottom:4px">' +
        '<span style="font-family:var(--font-mono);font-size:11px">' + esc(step.name) + "</span>" + ok + "</div>";
      const d = step.detail || {};
      if (d.bytes) html += '<div style="font-size:10px;color:var(--muted)">' + d.bytes + " bytes</div>";
      if (d.summary) html += '<div style="margin-top:4px;font-size:11px">' + esc(String(d.summary).slice(0, 500)) + "</div>";
      if (d.post) html += '<div style="margin-top:4px;font-size:11px">' + esc(String(d.post).slice(0, 500)) + "</div>";
      if (d.error) html += '<div style="margin-top:4px;font-size:11px;color:#ff8888">' + esc(d.error) + "</div>";
      html += "</div>";
    });
    if (r.summary) {
      const s = typeof r.summary === "string" ? r.summary : pretty(r.summary);
      html += '<div style="margin-top:8px;padding:10px;background:rgba(61,213,244,0.08);border-radius:6px"><div style="font-weight:600;color:var(--cyan);font-size:11px;margin-bottom:4px">Final summary</div><div style="font-size:11px">' + esc(s.slice(0, 800)) + "</div></div>";
    }
    setHTML(out, html);
  } catch (e) { err(out, e); }
}

// =============================================================================
// OUTREACH
// =============================================================================
async function planOutreach(){
  const out = "outreach-plan";
  setHTML(out, '<span class="spinner"></span>');
  const lead = {
    name: document.getElementById("outreach-lead-name").value,
    country: document.getElementById("outreach-country").value,
    industry: document.getElementById("outreach-industry").value,
    phone: document.getElementById("outreach-phone").value || undefined,
    telegram_id: document.getElementById("outreach-telegram").value || undefined,
    email: document.getElementById("outreach-email").value || undefined,
    linkedin_url: document.getElementById("outreach-linkedin").value || undefined,
  };
  try {
    const r = await api("POST", "/api/social/plan", { lead, body: document.getElementById("outreach-body").value });
    let html = "";
    if (r.available_channels && r.available_channels.length) {
      html += '<div style="font-size:11px;margin-bottom:8px">LIVE: ' + r.available_channels.map(c => '<span class="tag-pill good" style="font-size:10px">' + esc(c) + "</span>").join(" ") + "</div>";
    }
    (r.ranked || []).forEach(c => {
      const badge = c.available ? '<span class="tag-pill good">avail</span>' : '<span class="tag-pill bad">no key</span>';
      html += '<div style="display:flex;justify-content:space-between;padding:8px;background:rgba(255,255,255,0.03);border-radius:6px;margin-bottom:4px"><div><span style="font-family:var(--font-mono);font-weight:600">' + esc(c.channel) + '</span> <span style="font-size:10px;color:var(--muted)">via ' + esc(c.via) + '</span><br><span style="font-size:10px;color:var(--muted)">' + esc(c.reason) + '</span></div><div style="text-align:right"><div style="font-size:18px;font-weight:700;color:var(--cyan)">' + c.score + '</div>' + badge + '</div></div>';
    });
    if (r.plan && r.plan.primary) {
      html += '<div style="margin-top:12px"><div style="font-weight:600;margin-bottom:4px">Body for ' + esc(r.plan.primary.channel) + '</div><pre style="background:rgba(61,213,244,0.05);padding:10px;border-radius:6px;font-size:11px;white-space:pre-wrap">' + esc(r.plan.bodies[r.plan.primary.channel] || "") + "</pre></div>";
    }
    setHTML(out, html);
  } catch (e) { err(out, e); }
}
async function doSendSocial(){
  const out = "send-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/social/send", {
      channel: document.getElementById("send-channel").value,
      to: document.getElementById("send-to").value,
      subject: document.getElementById("send-subject").value || undefined,
      body: document.getElementById("send-body").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function doAutoSend(){
  const out = "autosend-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/social/auto_send", {
      lead: { name: "Sarah", telegram_id: "@sarah_chen", email: "sarah@acme.com", phone: "+5511999998888" },
      body: "Hi Sarah, quick intro to our lead routing tool.",
      subject: "Quick intro",
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function doBrowserSend(){
  const out = "brow-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/browser/send", {
      channel: document.getElementById("brow-channel").value,
      to: document.getElementById("brow-to").value,
      subject: document.getElementById("brow-subject").value || undefined,
      body: document.getElementById("brow-body").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// VOICE
// =============================================================================
async function simulateVoice(){
  const out = "voice-reply";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/voice/simulate", {
      user_text: document.getElementById("voice-text").value,
      from: document.getElementById("voice-from").value,
      to: document.getElementById("voice-to").value,
    });
    const res = r.result || {};
    setHTML(out, '<div class="tag-pill good">NIM 30B live</div><div style="margin-top:8px"><strong>Reply:</strong></div><pre style="background:rgba(61,213,244,0.05);padding:10px;border-radius:6px;margin-top:4px">' + esc(res.text || "(empty)") + "</pre>");
  } catch (e) { err(out, e); }
}
async function testTwiML(){
  const out = "voice-twiml";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await fetch(API_BASE + "/voice/twiml?call_sid=CAtest&from=%2B15551234567&to=%2B18005551234", { headers: H_AUTH });
    setHTML(out, "<pre style='white-space:pre-wrap'>" + esc(await r.text()) + "</pre>");
  } catch (e) { err(out, e); }
}
async function testVoiceKey(){
  const out = "voice-test";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/voice/test", { prompt: "ping" });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// FULL PIPELINE
// =============================================================================
async function runPipeline(){
  const out = "pipeline-result";
  setHTML(out, '<span class="spinner"></span> Running...');
  try {
    const r = await api("POST", "/api/demo/full_pipeline", {});
    let html = "";
    (r.steps || []).forEach(step => {
      const ok = step.ok ? '<span class="tag-pill good">ok</span>' : '<span class="tag-pill bad">fail</span>';
      html += '<div style="margin-bottom:12px;padding:10px;background:rgba(255,255,255,0.03);border-radius:6px">' +
        '<div style="display:flex;justify-content:space-between;margin-bottom:6px">' +
        '<span style="font-weight:600;color:var(--cyan)">' + esc(step.name) + "</span>" + ok + "</div>" +
        '<pre style="white-space:pre-wrap;font-size:10px;margin:0">' + esc(pretty(step.detail).slice(0, 1000)) + "</pre></div>";
    });
    html += '<div style="margin-top:16px;padding:12px;background:rgba(61,213,244,0.08);border-radius:8px"><div style="font-weight:600;color:var(--cyan);margin-bottom:6px">Summary</div><pre style="white-space:pre-wrap;font-size:11px;margin:0">' + esc(pretty(r.summary)) + "</pre></div>";
    setHTML(out, html);
  } catch (e) { err(out, e); }
}

// =============================================================================
// MEMORY
// =============================================================================
async function runRecall(){
  const out = "mem-recall";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/memory/recall?query=" + encodeURIComponent(document.getElementById("mem-query").value));
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runRemember(){
  const out = "mem-remember-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/memory/remember", {
      kind: document.getElementById("mem-kind").value,
      content: document.getElementById("mem-content").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadEpisodes(){
  const out = "mem-episodes";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/memory/episodes");
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadLessons(){
  const out = "mem-lessons";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/memory/lessons");
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadCapabilities(){
  const out = "mem-caps";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/memory/capabilities");
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// COGNITIVE
// =============================================================================
async function runCognitive(){
  const out = "cog-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/cognitive/run", { goal: document.getElementById("cog-goal").value });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runSynthesis(){
  const out = "cog-other";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/cognitive/synthesis")); } catch (e) { err(out, e); }
}
async function runRefine(){
  const out = "cog-other";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/cognitive/refine")); } catch (e) { err(out, e); }
}
async function runImprovement(){
  const out = "cog-other";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/cognitive/improvement")); } catch (e) { err(out, e); }
}
async function runConstitutional(){
  const out = "const-result";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/cognitive/constitutional")); } catch (e) { err(out, e); }
}

// =============================================================================
// COMPLIANCE
// =============================================================================
async function runConsent(){
  const out = "comp-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/compliance/consent", {
      email: document.getElementById("comp-email").value,
      purpose: document.getElementById("comp-purpose").value,
      granted: document.getElementById("comp-granted").value === "true",
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runCheckConsent(){
  const out = "comp-check-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/compliance/check_consent", {
      email: document.getElementById("comp-check-email").value,
      purpose: document.getElementById("comp-check-purpose").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runUnsubUrl(){
  const out = "comp-unsub-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/compliance/unsubscribe_url", {
      email: document.getElementById("comp-unsub-email").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runRetention(){
  const out = "comp-ret-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/compliance/retention", {
      retention_days: parseInt(document.getElementById("comp-ret-days").value),
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadAudit(){
  const out = "comp-audit";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/compliance/audit")); } catch (e) { err(out, e); }
}

// =============================================================================
// GDPR
// =============================================================================
async function runGdprDelete(){
  const out = "gdpr-del-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("DELETE", "/api/leads/" + document.getElementById("gdpr-del-id").value + "/delete");
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runGdprExport(){
  const out = "gdpr-exp-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("GET", "/api/leads/" + document.getElementById("gdpr-exp-id").value + "/export");
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function testUnsubscribe(){
  const out = "gdpr-unsub-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await fetch(API_BASE + "/unsubscribe?token=test-token").then(r => r.text());
    setHTML(out, "<pre style='white-space:pre-wrap'>" + esc(r) + "</pre>");
  } catch (e) { err(out, e); }
}

// =============================================================================
// SECURITY
// =============================================================================
async function runHmacSign(){
  const out = "sec-sign-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/security/hmac/sign", { body: document.getElementById("sec-body").value });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function runHmacVerify(){
  const out = "sec-verify-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/security/hmac/verify", {
      body: document.getElementById("sec-body").value,
      signature: document.getElementById("sec-sig").value,
    });
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function loadVault(){
  const out = "sec-vault";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/security/vault")); } catch (e) { err(out, e); }
}

// =============================================================================
// OBSERVABILITY
// =============================================================================
async function loadMetrics(){
  const out = "obs-metrics";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/observability/metrics")); } catch (e) { err(out, e); }
}
async function loadBreakers(){
  const out = "obs-breakers";
  setHTML(out, '<span class="spinner"></span>');
  try { outJSON(out, await api("GET", "/api/observability/metrics")); } catch (e) { err(out, e); }
  try {
    const r = await fetch(API_BASE + "/metrics", { headers: H_AUTH });
    setHTML("obs-prom", "<pre style='font-size:10px;white-space:pre-wrap'>" + esc((await r.text()).slice(0, 5000)) + "</pre>");
  } catch (e) { err("obs-prom", e); }
}

// =============================================================================
// WEBHOOKS
// =============================================================================
async function testWebhookInbound(){
  const out = "wh-inb-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/webhooks/inbound", JSON.parse(document.getElementById("wh-body").value));
    outJSON(out, r);
  } catch (e) { err(out, e); }
}
async function testWebhookTwilio(){
  const out = "wh-tw-result";
  setHTML(out, '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/webhooks/twilio", JSON.parse(document.getElementById("wh-tw-body").value));
    outJSON(out, r);
  } catch (e) { err(out, e); }
}

// =============================================================================
// DSH
// =============================================================================
async function refreshDsh(){
  setHTML("dsh-status-out", '<span class="spinner"></span>');
  try { outJSON("dsh-status-out", await api("GET", "/api/dsh/info")); } catch (e) { err("dsh-status-out", e); }
}
async function startDsh(){
  setHTML("dsh-status-out", '<span class="spinner"></span>');
  try { outJSON("dsh-status-out", await api("POST", "/api/dsh/start", {})); } catch (e) { err("dsh-status-out", e); }
}
async function stopDsh(){
  setHTML("dsh-status-out", '<span class="spinner"></span>');
  try { outJSON("dsh-status-out", await api("POST", "/api/dsh/stop", {})); } catch (e) { err("dsh-status-out", e); }
}
async function runDshSubagent(){
  setHTML("dsh-subagent-out", '<span class="spinner"></span>');
  try {
    const r = await api("POST", "/api/dsh/subagent/delegate", {
      provider: document.getElementById("dsh-provider").value,
      spec: JSON.parse(document.getElementById("dsh-spec").value),
    });
    outJSON("dsh-subagent-out", r);
  } catch (e) { err("dsh-subagent-out", e); }
}
async function loadDshSessions(){
  setHTML("dsh-sessions-out", '<span class="spinner"></span>');
  try { outJSON("dsh-sessions-out", await api("GET", "/api/dsh/sessions")); } catch (e) { err("dsh-sessions-out", e); }
}
async function loadDshRuns(){
  setHTML("dsh-runs-out", '<span class="spinner"></span>');
  try { outJSON("dsh-runs-out", await api("GET", "/api/dsh/subagent/runs")); } catch (e) { err("dsh-runs-out", e); }
}
async function loadDshMeter(){
  setHTML("dsh-meter-out", '<span class="spinner"></span>');
  try { outJSON("dsh-meter-out", await api("GET", "/api/dsh/meter/summary")); } catch (e) { err("dsh-meter-out", e); }
}
async function loadDshSkills(){
  setHTML("dsh-skills-out", '<span class="spinner"></span>');
  try { outJSON("dsh-skills-out", await api("GET", "/api/dsh/skills")); } catch (e) { err("dsh-skills-out", e); }
}
async function runDshSchedule(){
  setHTML("dsh-sched-out", '<span class="spinner"></span>');
  try {
    const action = document.getElementById("dsh-sched-action").value;
    const taskRaw = document.getElementById("dsh-sched-task").value.trim();
    const body = { action };
    if (taskRaw && action !== "list") body.task = JSON.parse(taskRaw);
    outJSON("dsh-sched-out", await api("POST", "/api/dsh/schedule", body));
  } catch (e) { err("dsh-sched-out", e); }
}

// =============================================================================
// CHAT
// =============================================================================
const chatLog = document.getElementById("chat-log");
function appendUser(txt){
  const d = document.createElement("div");
  d.className = "msg user";
  d.innerHTML = '<div class="role">You</div>' + esc(stripEmojis(txt)).replace(/\n/g, "<br>");
  chatLog.appendChild(d); chatLog.scrollTop = chatLog.scrollHeight;
}
function appendBot(txt, kind){
  const d = document.createElement("div");
  d.className = "msg bot" + (kind === "tool" ? " tool" : "");
  d.innerHTML = '<div class="role">' + (kind === "tool" ? "Tool" : "Agent") + "</div>" + stripEmojis(txt);
  chatLog.appendChild(d); chatLog.scrollTop = chatLog.scrollHeight;
}
async function sendChat(text){
  if (!text.trim()) return;
  appendUser(text);
  document.getElementById("btn-chat-send").disabled = true;
  document.getElementById("chat-status").textContent = "thinking...";
  try {
    const r = await api("POST", "/api/chat", { messages: [{ role: "user", content: text }] });
    if (r.error) appendBot("Error: " + esc(r.error));
    else if (r.steps && r.steps.length) {
      for (const step of r.steps) {
        if (step.tool) appendBot("Tool `" + esc(step.tool) + "` &rarr; " + esc(pretty(step.result).slice(0, 400)), "tool");
      }
      appendBot(esc(r.reply || "(no reply)"));
    } else if (r.reply) appendBot(esc(r.reply));
    else outJSON_append(chatLog, r);
  } catch (e) {
    appendBot("Error: " + esc(String(e.message || e)));
  }
  document.getElementById("btn-chat-send").disabled = false;
  document.getElementById("chat-status").textContent = "ready";
}
function outJSON_append(el, obj){
  const d = document.createElement("div");
  d.className = "msg bot";
  d.innerHTML = "<div class='role'>Agent</div><pre style='white-space:pre-wrap'>" + esc(pretty(obj)) + "</pre>";
  el.appendChild(d);
}
document.getElementById("btn-chat-send").addEventListener("click", () => {
  const t = document.getElementById("chat-input").value;
  if (t) { sendChat(t); document.getElementById("chat-input").value = ""; }
});
document.getElementById("chat-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    document.getElementById("btn-chat-send").click();
  }
});

// Expose globals for inline onclick
window.runHealth = runHealth;
window.runReadyz = runReadyz;
window.loadCaps = loadCaps;
window.loadDiscover = loadDiscover;
window.runGoogleMaps = runGoogleMaps;
window.runExtract = runExtract;
window.loadLeads = loadLeads;
window.runEnrich = runEnrich;
window.loadFunnel = loadFunnel;
window.runQualify = runQualify;
window.runIcpTrain = runIcpTrain;
window.runIcpScore = runIcpScore;
window.runProspexScore = runProspexScore;
window.runProspexBatch = runProspexBatch;
window.runIdentity = runIdentity;
window.runCrmEvidence = runCrmEvidence;
window.runDraftOutbound = runDraftOutbound;
window.runDraftReply = runDraftReply;
window.loadDrafts = loadDrafts;
window.runDealRisk = runDealRisk;
window.runHealthScore = runHealthScore;
window.loadSocial = loadSocial;
window.testSocialChannel = testSocialChannel;
window.doReadSocial = doReadSocial;
window.loadBrowser = loadBrowser;
window.runFlow = runFlow;
window.planOutreach = planOutreach;
window.doSendSocial = doSendSocial;
window.doAutoSend = doAutoSend;
window.doBrowserSend = doBrowserSend;
window.simulateVoice = simulateVoice;
window.testTwiML = testTwiML;
window.testVoiceKey = testVoiceKey;
window.runPipeline = runPipeline;
window.runRecall = runRecall;
window.runRemember = runRemember;
window.loadEpisodes = loadEpisodes;
window.loadLessons = loadLessons;
window.loadCapabilities = loadCapabilities;
window.runCognitive = runCognitive;
window.runSynthesis = runSynthesis;
window.runRefine = runRefine;
window.runImprovement = runImprovement;
window.runConstitutional = runConstitutional;
window.runConsent = runConsent;
window.runCheckConsent = runCheckConsent;
window.runUnsubUrl = runUnsubUrl;
window.runRetention = runRetention;
window.loadAudit = loadAudit;
window.runGdprDelete = runGdprDelete;
window.runGdprExport = runGdprExport;
window.testUnsubscribe = testUnsubscribe;
window.runHmacSign = runHmacSign;
window.runHmacVerify = runHmacVerify;
window.loadVault = loadVault;
window.loadMetrics = loadMetrics;
window.loadBreakers = loadBreakers;
window.testWebhookInbound = testWebhookInbound;
window.testWebhookTwilio = testWebhookTwilio;
window.refreshDsh = refreshDsh;
window.startDsh = startDsh;
window.stopDsh = stopDsh;
window.runDshSubagent = runDshSubagent;
window.loadDshSessions = loadDshSessions;
window.loadDshRuns = loadDshRuns;
window.loadDshMeter = loadDshMeter;
window.loadDshSkills = loadDshSkills;
window.runDshSchedule = runDshSchedule;
window.switchTab = switchTab;
window.loadIcp = () => {};
window.loadDraftOutbound = () => {};
window.testVoiceKey = testVoiceKey;
