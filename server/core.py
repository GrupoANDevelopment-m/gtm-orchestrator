"""
GTM Orchestrator — REAL merger (production-ready).

Uses:
  - gtm-engine (Python): 5 specialized agents with REAL NVIDIA NIM-backed LLM
  - speed-to-lead (Python): qualify/draft pipeline (REAL production code)
  - prospex (TS) + crm-release (Bun): vendored upstream, not yet bridged
  - Twilio + SendGrid + Apollo + Hunter: outbound channels + enrichment (live when keys present)

Adds on top:
  - 4 anti-hallucination layers (semantic tool selection, arg validation,
    trimmed prompt, chain-of-verification)
  - Persistent ICPAgent training state (SQLite)
  - API key auth via env var
  - Webhook receiver for inbound leads
  - Production server (uvicorn)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "upstream"
SPEED_TO_LEAD_SRC = UPSTREAM / "speed-to-lead" / "src"
GTM_ENGINE = UPSTREAM / "gtm-engine"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "orchestrator.sqlite3"
KEYS_DIR = ROOT / "keys"

for p in (str(SPEED_TO_LEAD_SRC), str(GTM_ENGINE)):
    if p not in sys.path:
        sys.path.insert(0, p)

# speed-to-lead imports
from speed_to_lead.services.qualify import (  # type: ignore
    RuleQualifier, detect_intent, assemble_result, fit_signals, get_qualifier as speed_get_qualifier,
)
from speed_to_lead.services.draft import (  # type: ignore
    TemplateDrafter, LlmDrafter,
)
from speed_to_lead.normalize import (  # type: ignore
    normalize_lead, is_business_domain, email_domain,
)
from speed_to_lead.models import (  # type: ignore
    Lead, InboundLead, EnrichmentResult, QualificationResult, DraftResult,
    FitTier, RouteResult,
)

# gtm-engine imports
from backend.agents.icp_agent import ICPAgent, ICPProfile, ICPScoreResult  # type: ignore
from backend.agents.outbound_agent import OutboundAgent, OutboundVariation, OutboundResult  # type: ignore
from backend.agents.deal_intel_agent import DealIntelAgent  # type: ignore
from backend.agents.retention_agent import RetentionAgent  # type: ignore
from backend.agents.content_agent import ContentAgent  # type: ignore
from backend.agents.base_agent import LocalLLMRouter  # type: ignore

# Our production LLM adapter (drop-in for LocalLLMRouter)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_adapter import NvidiaLLMRouter  # type: ignore

# Outbound integrations
from integrations import twilio, sendgrid, apollo, hunter  # type: ignore
from browser import agent_reach, google_maps, browser  # type: ignore


def run_async(coro):
    """Helper to run async coroutines from sync code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)



# ============================================================================
# Wire REAL LLM into gtm-engine agents (not the stub LocalLLMRouter)
# ============================================================================
_llm_router = NvidiaLLMRouter(fallback=LocalLLMRouter())
_icp_agent = ICPAgent(llm_router=_llm_router)
_outbound_agent = OutboundAgent(llm_router=_llm_router)
_deal_intel_agent = DealIntelAgent(llm_router=_llm_router)
_retention_agent = RetentionAgent(llm_router=_llm_router)
_content_agent = ContentAgent(llm_router=_llm_router)
_qualifier = speed_get_qualifier()
_template_drafter = TemplateDrafter()


# ============================================================================
# Local SQLite
# ============================================================================
def db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def _tx():
    conn = db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with _tx() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT, business_name TEXT, contact_name TEXT, contact_email TEXT,
          contact_phone TEXT, contact_title TEXT, company TEXT, industry TEXT,
          size TEXT, rating REAL, review_count INTEGER, has_website INTEGER,
          city TEXT, message TEXT, created_at REAL,
          icp_score REAL, icp_tier TEXT
        );
        CREATE TABLE IF NOT EXISTS drafts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          lead_id INTEGER, channel TEXT, subject TEXT, body TEXT,
          status TEXT DEFAULT 'pending', sent_at REAL, error TEXT,
          created_at REAL
        );
        CREATE TABLE IF NOT EXISTS funnel (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          lead_id INTEGER, tier TEXT, intent TEXT, intent_confidence REAL,
          fit_score REAL, action TEXT, source TEXT, reasons TEXT, created_at REAL,
          received_at REAL, researched_at REAL, qualified_at REAL, drafted_at REAL,
          routed_at REAL, destination TEXT
        );
        CREATE TABLE IF NOT EXISTS icp_training (
          org_id TEXT, profiles_json TEXT, updated_at REAL,
          PRIMARY KEY (org_id)
        );
        CREATE TABLE IF NOT EXISTS approvals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          draft_id INTEGER, action TEXT, user TEXT, created_at REAL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_type TEXT, ip_address TEXT, payload_json TEXT,
          created_at REAL
        );
        """)

init_db()

# Migration: add icp_score/icp_tier if missing (for existing databases)
try:
    with _tx() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()]
        if "icp_score" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN icp_score REAL")
        if "icp_tier" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN icp_tier TEXT")
except Exception:
    pass


# ============================================================================
# Persistent ICPAgent training state
# ============================================================================
def _load_training_state():
    """Load ICP training profiles from SQLite into the ICPAgent."""
    with _tx() as conn:
        rows = conn.execute("SELECT org_id, profiles_json FROM icp_training").fetchall()
    for r in rows:
        try:
            profiles = json.loads(r["profiles_json"])
            _icp_agent._training_sets[r["org_id"]] = [
                ICPProfile(text=p.get("text", ""), label=p.get("label"), metadata=p)
                for p in profiles
            ]
        except Exception:
            pass

def _save_training_state(org_id: str, profiles: list):
    """Save ICP training profiles to SQLite."""
    with _tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO icp_training (org_id, profiles_json, updated_at) VALUES (?,?,?)",
            (org_id, json.dumps(profiles), time.time())
        )

_load_training_state()


# ============================================================================
# Tool definitions for the LLM
# ============================================================================
def tool_defs():
    return json.loads(r'''
    [
      {"type": "function", "function": {"name": "list_leads", "description": "List all leads in the CRM.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}}},
      {"type": "function", "function": {"name": "get_lead", "description": "Get one lead by ID.", "parameters": {"type": "object", "properties": {"lead_id": {"type": "integer"}}, "required": ["lead_id"]}}},
      {"type": "function", "function": {"name": "score_lead_icp", "description": "Score lead fit against trained ICP (REAL gtm-engine ICPAgent via NVIDIA NIM).", "parameters": {"type": "object", "properties": {"lead_id": {"type": "integer"}}, "required": ["lead_id"]}}},
      {"type": "function", "function": {"name": "qualify_inbound", "description": "Run REAL speed-to-lead qualify (intent + fit).", "parameters": {"type": "object", "properties": {"email": {"type": "string"}, "name": {"type": "string"}, "company": {"type": "string"}, "message": {"type": "string"}}, "required": ["message"]}}},
      {"type": "function", "function": {"name": "score_deal_risk", "description": "Score deal risk (REAL DealIntelAgent via NVIDIA NIM).", "parameters": {"type": "object", "properties": {"days_in_stage": {"type": "integer"}, "amount": {"type": "number"}, "reply_rate": {"type": "number"}, "competitor_mentions": {"type": "integer"}}, "required": ["days_in_stage", "amount", "reply_rate", "competitor_mentions"]}}},
      {"type": "function", "function": {"name": "score_account_health", "description": "Score account health (REAL RetentionAgent via NVIDIA NIM).", "parameters": {"type": "object", "properties": {"usage_pct": {"type": "number"}, "support_tickets": {"type": "integer"}, "champion_left": {"type": "boolean"}, "budget_concerns": {"type": "boolean"}}, "required": ["usage_pct", "support_tickets", "champion_left", "budget_concerns"]}}},
      {"type": "function", "function": {"name": "funnel_summary", "description": "Aggregate funnel stats.", "parameters": {"type": "object", "properties": {}}}},
      {"type": "function", "function": {"name": "fetch_url", "description": "Fetch via Jina Reader or Playwright (render_js=true for JS).", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "render_js": {"type": "boolean"}}, "required": ["url"]}}},
      {"type": "function", "function": {"name": "google_maps_prospect", "description": "Search Google Maps for real businesses via Playwright + headless Chrome.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "city": {"type": "string"}, "count": {"type": "integer"}}, "required": ["query", "city"]}}},
      {"type": "function", "function": {"name": "agent_reach_read", "description": "Read from 15 platforms via Agent-Reach (Twitter, LinkedIn, GitHub, Reddit, YouTube, Facebook, Instagram, etc).", "parameters": {"type": "object", "properties": {"platform": {"type": "string"}, "target": {"type": "string"}}, "required": ["platform", "target"]}}},
      {"type": "function", "function": {"name": "extract_leads_from_url", "description": "Visit URL with Playwright, extract emails + phones.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
      {"type": "function", "function": {"name": "enrich_lead", "description": "Enrich lead with Apollo.io + Hunter.io.", "parameters": {"type": "object", "properties": {"lead_id": {"type": "integer"}}, "required": ["lead_id"]}}},
      {"type": "function", "function": {"name": "send_draft", "description": "Send draft via Twilio/SendGrid.", "parameters": {"type": "object", "properties": {"draft_id": {"type": "integer"}, "channel": {"type": "string", "enum": ["whatsapp", "sms", "email"]}}, "required": ["draft_id", "channel"]}}},
      {"type": "function", "function": {"name": "create_lead", "description": "Create new lead. NEVER invent values.", "parameters": {"type": "object", "properties": {"business_name": {"type": "string"}, "contact_name": {"type": "string"}, "contact_email": {"type": "string"}, "contact_phone": {"type": "string"}, "contact_title": {"type": "string"}, "company": {"type": "string"}, "industry": {"type": "string"}, "size": {"type": "string"}, "city": {"type": "string"}, "rating": {"type": "number"}, "review_count": {"type": "integer"}, "has_website": {"type": "boolean"}, "message": {"type": "string"}, "source": {"type": "string"}}, "required": ["business_name"]}}},
      {"type": "function", "function": {"name": "draft_outbound", "description": "Generate 3 outbound email variations (REAL OutboundAgent).", "parameters": {"type": "object", "properties": {"contact_name": {"type": "string"}, "company_name": {"type": "string"}, "industry": {"type": "string"}, "campaign_name": {"type": "string"}}, "required": ["contact_name", "company_name"]}}},
      {"type": "function", "function": {"name": "draft_reply", "description": "Draft a reply (REAL speed-to-lead TemplateDrafter).", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "company": {"type": "string"}, "message": {"type": "string"}, "lead_id": {"type": "integer"}}, "required": ["name", "message"]}}},
      {"type": "function", "function": {"name": "train_icp", "description": "Train ICPAgent on 3+ ICP profiles (persisted).", "parameters": {"type": "object", "properties": {"profiles": {"type": "array", "items": {"type": "object"}}, "org_id": {"type": "string", "default": "default"}}, "required": ["profiles"]}}}
    ]
    ''')


# ============================================================================
# L1: Semantic tool selection
# ============================================================================
_TOOL_INDEX = None
def _build_tool_index():
    global _TOOL_INDEX
    if _TOOL_INDEX is not None:
        return _TOOL_INDEX
    stop = {"a","an","the","of","to","in","on","for","and","or","is","it","this","that","be","as","at","by","with","from","i","you","we","they","me","us","them","do","does","did","can","could","would","should","will","please","want","need","get","give","use","make"}
    idx = []
    for td in tool_defs():
        fn = td["function"]
        name = fn["name"]
        desc = fn.get("description", "")
        params = fn.get("parameters", {}).get("properties", {})
        param_text = " ".join(params.keys())
        text = (name + " " + desc + " " + param_text).lower()
        tokens = set()
        for w in text.replace("_", " ").replace("-", " ").split():
            w = w.strip(".,;:()[]{}'\"?!")
            if w and w not in stop and len(w) > 1:
                tokens.add(w)
        idx.append({"name": name, "tokens": tokens})
    _TOOL_INDEX = idx
    return idx

def select_relevant_tools(user_message, *, top_n=5):
    if not user_message:
        return [t["name"] for t in _build_tool_index()[:top_n]]
    stop = {"a","an","the","of","to","in","on","for","and","or","is","it","this","that","be","as","at","by","with","from","i","you","we","they","me","us","them","do","does","did","can","could","would","should","will","please","want","need","get","give","use","make"}
    msg = user_message.lower()
    msg_tokens = set()
    for w in msg.replace("_", " ").replace("-", " ").split():
        w = w.strip(".,;:()[]{}'\"?!*")
        if w and w not in stop and len(w) > 1:
            msg_tokens.add(w)
    if re.search(r"\blead\s+(\d+)\b", user_message.lower()):
        msg_tokens.add("lead"); msg_tokens.add("score")
    idx = _build_tool_index()
    scored = []
    for t in idx:
        overlap = len(msg_tokens & t["tokens"])
        name_words = set(t["name"].lower().split("_"))
        if name_words & msg_tokens:
            overlap += 3
        for verb in ("create","list","score","approve","reject","fetch","generate","update","delete","train","draft","qualify","send","enrich"):
            if verb in msg_tokens and verb in t["name"]:
                overlap += 2
                break
        scored.append((overlap, t["name"]))
    scored.sort(key=lambda x: -x[0])
    selected = [name for score, name in scored if score > 0][:top_n]
    for fb in ("list_leads", "funnel_summary"):
        if fb not in selected and len(selected) < top_n + 1:
            selected.append(fb)
    if not selected:
        selected = [name for _, name in scored[:3]]
    return selected


# ============================================================================
# L2: Arg validation
# ============================================================================
_TOOL_REQUIRED_ARGS = {
    "score_lead_icp": ["lead_id"],
    "qualify_inbound": ["message"],
    "score_deal_risk": ["days_in_stage", "amount", "reply_rate", "competitor_mentions"],
    "score_account_health": ["usage_pct", "support_tickets", "champion_left", "budget_concerns"],
    "fetch_url": ["url"],
    "draft_outbound": ["contact_name", "company_name"],
    "draft_reply": ["name", "message"],
    "train_icp": ["profiles"],
    "send_draft": ["draft_id", "channel"],
    "enrich_lead": ["lead_id"],
    "get_lead": ["lead_id"],
}
def validate_tool_args(name, args):
    required = _TOOL_REQUIRED_ARGS.get(name, [])
    if not required:
        return True, None, args
    missing = [k for k in required if k not in args or args.get(k) in (None, "", [])]
    if missing:
        return False, f"missing_required_args: {missing}", args
    cleaned = dict(args)
    if "lead_id" in cleaned:
        try:
            cleaned["lead_id"] = int(cleaned["lead_id"])
            if cleaned["lead_id"] < 1 or cleaned["lead_id"] > 10_000_000:
                return False, f"lead_id_out_of_range: {cleaned['lead_id']}", args
        except (TypeError, ValueError):
            return False, f"bad_type: lead_id must be int, got {args['lead_id']!r}", args
    if "draft_id" in cleaned:
        try:
            cleaned["draft_id"] = int(cleaned["draft_id"])
        except (TypeError, ValueError):
            return False, f"bad_type: draft_id must be int, got {args['draft_id']!r}", args
    return True, None, cleaned


# ============================================================================
# L3: System prompt
# ============================================================================
SYSTEM_PROMPT = """You are the GTM Orchestrator. You use REAL production code from 4 merged repos:
  • gtm-engine (5 agents: ICP, Outbound, Deal Intel, Retention, Content) — backed by NVIDIA NIM
  • speed-to-lead (qualify + draft pipeline + LoRA classifier)
  • prospex (Playwright Google Maps scraping — vendored)
  • crm-release (31-tool agentic CRM — vendored)

# Routing rules
1. **Existing lead referenced** (id, name) → use READ tools: list_leads, get_lead, score_lead_icp, score_deal_risk, score_account_health, funnel_summary
2. **User provides new entity info + says "create"** → use create_lead
3. **User asks for outbound content** → use draft_outbound or draft_reply
4. **User says "send" / "approve" / "deliver"** → use send_draft
5. **User says "enrich" / "find more info about"** → use enrich_lead (Apollo + Hunter)
6. **NEVER** call create_lead with invented values. If missing info, ask.

# Tone
Concise, factual, end with what you did or what the user should do next.
"""


# ============================================================================
# LLM client (NVIDIA NIM) for the agentic chat loop
# ============================================================================
ALT_MODELS = {
    "nemotron": os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    "step": "stepfun-ai/step-3.7-flash",
    "m2": "minimaxai/minimax-m3",
}
DEFAULT_MODEL = ALT_MODELS["nemotron"]

def _load_chat_key():
    for fname in (".nvidia_main.txt", ".nvidia_step.txt", ".nvidia_m2.txt"):
        p = KEYS_DIR / fname
        if p.exists():
            return p.read_text().strip()
    return os.environ.get("NVIDIA_API_KEY")

def _build_llm_request(messages, *, model=None, max_tokens=4096, temperature=0.4, tools=None, stream=False):
    key = _load_chat_key()
    if not key:
        raise RuntimeError("no NVIDIA key configured")
    full_model = ALT_MODELS.get(model, model) if model and model in ALT_MODELS else DEFAULT_MODEL
    # Truncate each message's content to avoid token limit errors with large tool schemas
    safe_messages = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str) and len(content) > 6000:
            content = content[:6000] + "...[truncated]"
        mm = dict(m)
        mm["content"] = content
        safe_messages.append(mm)
    body = {"model": full_model, "messages": safe_messages, "temperature": temperature, "top_p": 0.95, "max_tokens": max_tokens, "stream": stream}
    if tools:
        body["tools"] = tools
    return urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"},
        method="POST",
    )

def llm_chat(messages, *, model=None, max_tokens=4096, temperature=0.4, tools=None):
    import time as _t
    req = _build_llm_request(messages, model=model, max_tokens=max_tokens, temperature=temperature, tools=tools, stream=False)
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read().decode())
                # DSH token meter: record usage for cost tracking
                try:
                    from dsh_meter import record as _meter
                    usage = resp.get("usage", {})
                    if usage:
                        _meter(
                            org_id=os.environ.get("ORCHESTRATOR_ORG_ID", "default"),
                            model=resp.get("model", model or "unknown"),
                            tokens_in=usage.get("prompt_tokens", 0),
                            tokens_out=usage.get("completion_tokens", 0),
                            tool="llm_chat",
                        )
                except Exception:
                    pass
                return resp
        except urllib.error.HTTPError as e:
            err_body = ""
            try: err_body = e.read().decode()[:200]
            except: pass
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code in (400, 401, 403):
                raise RuntimeError(last_err)
            _t.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            _t.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"NIM failed after 3 attempts: {last_err}")


# ============================================================================
# Tool execution — calls REAL upstream code + integrations
# ============================================================================
def _execute_tool(name, args):
    try:
        if name == "create_lead":
            with _tx() as conn:
                cur = conn.execute(
                    """INSERT INTO leads (source, business_name, contact_name, contact_email, contact_phone,
                       contact_title, company, industry, size, rating, review_count, has_website, city,
                       message, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (args.get("source") or "manual", args.get("business_name"), args.get("contact_name"),
                     args.get("contact_email"), args.get("contact_phone"), args.get("contact_title"),
                     args.get("company"), args.get("industry"), args.get("size"),
                     args.get("rating"), args.get("review_count"),
                     1 if args.get("has_website") else (0 if "has_website" in args else None),
                     args.get("city"), args.get("message") or "", time.time())
                )
                lead_id = cur.lastrowid
            return f"lead_created id={lead_id}"

        if name == "list_leads":
            limit = int(args.get("limit") or 50)
            with _tx() as conn:
                rows = conn.execute(
                    f"SELECT id, business_name, contact_name, industry, size, rating, review_count, city, source FROM leads ORDER BY id DESC LIMIT {limit}"
                ).fetchall()
            if not rows:
                return "no_leads"
            return "\n".join(
                f"  id={r['id']} | {r['business_name']} | {r['industry']} | {r['size']} | "
                f"{r['rating']}★×{r['review_count']} | {r['city']} | {r['source']}"
                for r in rows
            )

        if name == "get_lead":
            lead_id = int(args["lead_id"])
            with _tx() as conn:
                row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                return f"error: lead_id={lead_id} not found"
            return json.dumps(dict(row), default=str)

        if name == "score_lead_icp":
            lead_id = args["lead_id"]
            with _tx() as conn:
                row = conn.execute("SELECT business_name, contact_name, industry, size, city, message FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                return f"error: lead_id={lead_id} not found"
            contact_profile = {"text": " ".join(filter(None, [row["business_name"], row["contact_name"], row["industry"], row["size"], row["city"], row["message"]]))}
            for k in ("industry", "size"):
                if row[k]: contact_profile[k] = row[k]
            result = asyncio.run(_icp_agent.score(org_id="default", contact_profile=contact_profile))
            return f"icp_score (REAL ICPAgent via NVIDIA NIM): score={result.score} badge={'red' if (result.score or 0) < 0.45 else ('amber' if (result.score or 0) < 0.7 else 'green')} fit_signals={result.fit_signals} gap_signals={result.gap_signals} fallback={result.fallback_used} training_profiles={result.training_profile_count} llm_live={_llm_router.is_live}"

        if name == "qualify_inbound":
            inbound = InboundLead(
                email=args.get("email") or "unknown@unknown.com",
                name=args.get("name") or "",
                company=args.get("company") or "",
                message=args.get("message", ""),
            )
            lead = normalize_lead(inbound)
            enrichment = EnrichmentResult(domain=lead.domain, employee_range=None, industry=None)
            qual = _qualifier.qualify(lead, enrichment)
            return f"qualify_inbound (REAL speed-to-lead): tier={qual.tier.value} score={qual.score} intent={qual.intent} confidence={qual.confidence} model={qual.model} reasons={qual.reasons}"

        if name == "draft_reply":
            inbound = InboundLead(email="unknown@unknown.com", name=args.get("name", ""), company=args.get("company", ""), message=args.get("message", ""))
            lead = normalize_lead(inbound)
            qual = _qualifier.qualify(lead, EnrichmentResult(domain=lead.domain))
            draft = asyncio.run(_template_drafter.draft(lead, EnrichmentResult(domain=lead.domain), qual))
            lead_id = args.get("lead_id")
            with _tx() as conn:
                cur = conn.execute(
                    "INSERT INTO drafts (lead_id, channel, subject, body, status, created_at) VALUES (?,?,?,?,?,?)",
                    (lead_id, draft.channel, draft.subject, draft.body, "pending", time.time())
                )
                draft_id = cur.lastrowid
            return f"draft_created id={draft_id} lead_id={lead_id} channel={draft.channel} subject={draft.subject!r} body={draft.body!r}"

        if name == "draft_outbound":
            contact = {"name": args.get("contact_name"), "company_name": args.get("company_name"), "industry": args.get("industry")}
            campaign = {"name": args.get("campaign_name") or "outreach_v1"}
            result = asyncio.run(_outbound_agent.generate_variations(contact=contact, campaign=campaign, context=None))
            out = []
            for i, v in enumerate(result.variations):
                out.append(f"  variation {i+1} (hook={v.hook_type}, conf={v.confidence:.2f}):\n  subject: {v.subject}\n  body: {v.body}")
            return f"outbound_draft (REAL OutboundAgent via NVIDIA NIM, llm_live={_llm_router.is_live}): {len(result.variations)} variations, validation_flags={result.validation_flags}\n" + "\n".join(out)

        if name == "score_deal_risk":
            deal = {"days_in_stage": args["days_in_stage"], "amount": args["amount"], "reply_rate": args["reply_rate"], "competitor_mentions": args["competitor_mentions"]}
            result = asyncio.run(_deal_intel_agent.run({"deal": deal}))
            return f"deal_risk (REAL DealIntelAgent via NVIDIA NIM, llm_live={_llm_router.is_live}): risk_score={result.risk_score} risk_level={result.risk_level} risk_factors={list(result.risk_factors)} positive_signals={list(result.positive_signals)} recommended_actions={list(result.recommended_actions)}"

        if name == "score_account_health":
            account = {"usage_pct": args["usage_pct"], "support_tickets": args["support_tickets"], "champion_left": args["champion_left"], "budget_concerns": args["budget_concerns"]}
            result = asyncio.run(_retention_agent.run({"account": account}))
            return f"account_health (REAL RetentionAgent via NVIDIA NIM, llm_live={_llm_router.is_live}): health_score={result.health_score} health_state={result.health_state} churn_driver={result.churn_driver} playbook={result.playbook} renewal_probability={result.renewal_probability} alert_required={result.alert_required}"

        if name == "fetch_url":
            url = args["url"]
            render_js = bool(args.get("render_js", False))
            if render_js:
                # Use Playwright for JS-rendered pages
                result = run_async(browser.fetch_page(url, action="content"))
                if result.get("ok"):
                    return f"fetched (Playwright) {url}: {result.get('text', '')[:1500]}"
                return f"playwright_fetch_failed: {result.get('error', 'unknown')}"
            req = urllib.request.Request(f"https://r.jina.ai/{url}", headers={"Accept": "text/plain"})
            with urllib.request.urlopen(req, timeout=30) as r:
                content = r.read().decode()
            return f"fetched (Jina) {url}: {content[:1500]}"

        if name == "google_maps_prospect":
            query = args.get("query", "")
            city = args.get("city", "")
            count = int(args.get("count", 10))
            result = run_async(google_maps.search(query, city, count))
            return json.dumps(result, default=str)[:3000]

        if name == "agent_reach_read":
            platform = args.get("platform", "web")
            target = args.get("target", "")
            result = run_async(agent_reach.read(platform, target))
            return json.dumps(result, default=str)[:3000]

        if name == "extract_leads_from_url":
            url = args.get("url", "")
            result = run_async(browser.extract_leads(url))
            return json.dumps(result, default=str)[:3000]

        if name == "funnel_summary":
            with _tx() as conn:
                rows = conn.execute("SELECT source, COUNT(*) as n FROM leads GROUP BY source").fetchall()
                tier_rows = conn.execute("SELECT tier, COUNT(*) as n FROM funnel GROUP BY tier").fetchall()
                total = conn.execute("SELECT COUNT(*) as n FROM leads").fetchone()["n"]
            return f"funnel_summary: total={total} by_source={[(r['source'], r['n']) for r in rows]} by_tier={[(r['tier'], r['n']) for r in tier_rows]}"

        if name == "train_icp":
            profiles = args.get("profiles", [])
            if len(profiles) < 3:
                return f"error: need at least 3 profiles, got {len(profiles)}"
            org_id = args.get("org_id", "default")
            result = asyncio.run(_icp_agent.train(org_id=org_id, profiles=profiles))
            # Persist
            _save_training_state(org_id, profiles)
            return f"icp_trained (REAL ICPAgent via NVIDIA NIM, persisted): {result}"

        if name == "enrich_lead":
            lead_id = int(args["lead_id"])
            with _tx() as conn:
                row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                return f"error: lead_id={lead_id} not found"
            lead = dict(row)
            enrichment = {}
            # Apollo
            email = lead.get("contact_email")
            if email and "@" in email:
                domain = email.split("@")[1]
                enrichment["apollo_person"] = apollo.enrich_person(email=email)
                enrichment["apollo_company"] = apollo.enrich_company(domain=domain)
            # Hunter
            name_parts = (lead.get("contact_name") or "").split()
            if len(name_parts) >= 2 and email and "@" in email:
                domain = email.split("@")[1]
                enrichment["hunter"] = hunter.find_email(
                    domain=domain, first_name=name_parts[0], last_name=name_parts[-1]
                )
            enrichment["apollo_status"] = apollo.status
            enrichment["hunter_status"] = hunter.status
            return f"enrich_lead (live when keys set): {json.dumps(enrichment, default=str)[:2000]}"

        if name == "send_draft":
            draft_id = int(args["draft_id"])
            channel = args["channel"]
            with _tx() as conn:
                draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if not draft:
                return f"error: draft_id={draft_id} not found"
            lead_row = None
            if draft["lead_id"]:
                with _tx() as conn:
                    lead_row = conn.execute("SELECT * FROM leads WHERE id=?", (draft["lead_id"],)).fetchone()
            to_email = lead_row["contact_email"] if lead_row else None
            to_phone = lead_row["contact_phone"] if lead_row else None
            result = None
            if channel == "email":
                if not to_email:
                    return f"error: lead {draft['lead_id']} has no contact_email"
                result = sendgrid.send(to=to_email, subject=draft["subject"] or "", body=draft["body"], metadata={"draft_id": draft_id})
            elif channel in ("whatsapp", "sms"):
                if not to_phone:
                    return f"error: lead {draft['lead_id']} has no contact_phone"
                result = twilio.send(to=to_phone, body=draft["body"], channel=channel, metadata={"draft_id": draft_id})
            else:
                return f"error: unknown channel {channel}"
            # Persist
            with _tx() as conn:
                conn.execute(
                    "UPDATE drafts SET status=?, sent_at=?, error=? WHERE id=?",
                    ("sent" if result.get("ok") else "failed", time.time() if result.get("ok") else None, result.get("error"), draft_id)
                )
                conn.execute(
                    "INSERT INTO approvals (draft_id, action, user, created_at) VALUES (?,?,?,?)",
                    (draft_id, f"send_{channel}", "agent", time.time())
                )
            return json.dumps(result, default=str)

        return f"unknown_tool: {name}"
    except Exception as e:
        return f"tool_error: {type(e).__name__}: {e}"


# ============================================================================
# Agentic chat loop with the 4 anti-hallucination layers
# ============================================================================
def agent_chat(messages, *, model=None, max_tool_rounds=5):
    msgs = list(messages)
    has_system = any(m.get("role") == "system" for m in msgs)
    if not has_system:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + msgs

    last_user_msg = ""
    for m in reversed(msgs):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "")
            break

    selected = select_relevant_tools(last_user_msg, top_n=5)
    all_tool_names = {t["function"]["name"] for t in tool_defs()}
    all_tools_ordered = list(reversed([t for t in tool_defs() if t["function"]["name"] != "create_lead"])) + [t for t in tool_defs() if t["function"]["name"] == "create_lead"]
    tools_for_llm = [t for t in all_tools_ordered if t["function"]["name"] in selected]
    tool_filter_log = f"selected={selected} llm_live={_llm_router.is_live}"

    tool_log = []
    for round_i in range(max_tool_rounds):
        try:
            resp = llm_chat(msgs, model=model, max_tokens=4096, tools=tools_for_llm)
        except Exception as e:
            return {"message": {"content": f"LLM call failed: {e}"}, "tool_calls": tool_log, "rounds": round_i, "usage": {}, "tool_filter": tool_filter_log}
        choice = resp["choices"][0]
        msg = choice["message"]
        msgs.append(msg)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls or choice.get("finish_reason") != "tool_calls":
            return {"message": msg, "tool_calls": tool_log, "rounds": round_i + 1, "usage": resp.get("usage", {}), "tool_filter": tool_filter_log}
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            ok, err, cleaned = validate_tool_args(name, args)
            if not ok:
                tool_log.append({"name": name, "args": args, "result": f"validation_error: {err}", "rejected_by": "L2"})
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": f"validation_error: {err}. Ask the user for the missing info or call a different tool."})
                continue
            args = cleaned
            result = _execute_tool(name, args)
            tool_log.append({"name": name, "args": args, "result": str(result)})
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
    return {"message": msgs[-2] if len(msgs) >= 2 else {"content": "(max tool rounds reached)"}, "tool_calls": tool_log, "rounds": max_tool_rounds, "usage": {}, "tool_filter": tool_filter_log}


def ready():
    init_db()
    _load_training_state()
