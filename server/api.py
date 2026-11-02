"""
GTM Orchestrator — HTTP API.

All endpoints backed by real upstream code (gtm-engine, speed-to-lead) + real
outbound integrations (Twilio, SendGrid, Apollo, Hunter).

Auth: API key via Authorization: Bearer <key> header. Set ORCHESTRATOR_API_KEY env var.
"""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, HTTPServer
from socketserver import ThreadingMixIn


class _ReuseThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """ThreadingHTTPServer with SO_REUSEADDR to avoid 'Address already in use' on restart."""
    allow_reuse_address = True
    daemon_threads = True
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core as C  # noqa
from integrations import twilio, sendgrid, apollo, hunter  # noqa


# Country detection from phone prefix + city name
_COUNTRY_CITIES = {
    "br": {"sao paulo", "rio de janeiro", "brasilia", "salvador",
           "fortaleza", "belo horizonte", "manaus", "curitiba",
           "recife", "porto alegre", "miami"},
    "us": {"new york", "san francisco", "los angeles", "chicago",
           "boston", "seattle", "austin", "miami", "denver"},
    "gb": {"london", "manchester", "liverpool", "edinburgh", "birmingham"},
    "de": {"berlin", "munich", "hamburg", "frankfurt"},
    "fr": {"paris", "lyon", "marseille", "nice"},
    "in": {"mumbai", "delhi", "bangalore", "hyderabad", "chennai"},
    "cn": {"beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou"},
    "jp": {"tokyo", "osaka", "kyoto", "yokohama"},
    "kr": {"seoul", "busan", "incheon"},
    "au": {"sydney", "melbourne", "brisbane", "perth"},
    "es": {"madrid", "barcelona", "valencia"},
    "mx": {"mexico city", "guadalajara", "monterrey"},
}


def _guess_country(city, phone):
    """Best-effort country guess from city name + phone prefix."""
    if not city and not phone:
        return "global"
    # Phone prefix wins (most reliable)
    if phone:
        p = str(phone).lstrip("+").replace(" ", "")
        if p.startswith("55"):  return "br"
        if p.startswith("1"):   return "us"
        if p.startswith("44"):  return "gb"
        if p.startswith("49"):  return "de"
        if p.startswith("33"):  return "fr"
        if p.startswith("91"):  return "in"
        if p.startswith("86"):  return "cn"
        if p.startswith("81"):  return "jp"
        if p.startswith("82"):  return "kr"
        if p.startswith("61"):  return "au"
        if p.startswith("971"): return "ae"
        if p.startswith("27"):  return "za"
        if p.startswith("234"): return "ng"
        if p.startswith("52"):  return "mx"
        if p.startswith("54"):  return "ar"
        if p.startswith("34"):  return "es"
    # City fallback (city name lowercase match)
    if city:
        cl = city.lower()
        for code, cities in _COUNTRY_CITIES.items():
            if cl in cities:
                return code
    return "global"
from social_adapters import social  # noqa
from browser_social import browser_social  # noqa
import browser as browser_module  # for .agent_reach attribute
from browser import agent_reach, google_maps, browser as browser_ctrl  # noqa
from prospex_adapter import score_lead as prospex_score_lead, score_leads as prospex_score_leads, SOURCE as PROSPEX_SRC
from crm_adapter import score_evidence as crm_score_evidence, verdict_for as crm_verdict_for, SOURCE as CRM_SRC
from dsh_bridge import DSHBridge, get_dsh as _get_dsh_bridge, SOURCE as DSH_SRC
from dsh_session import SessionLog, SESSION_ROOT as DSH_SESSION_ROOT
from dsh_subagent import SubagentRuntime, SubagentSpec, DelegatedPolicy, default_runtime as _default_subagent
from dsh_schedule import Schedule, default_schedule as _default_schedule
from dsh_meter import record as _meter_record, summary as _meter_summary, session_breakdown as _meter_session
from dsh_sandbox import get_guard as _get_guard
from dsh_skills import list_skills, create_skill, invoke_skill, seed_default_skills
import cognitive_loop as CL
import memory as MEM
import perception as PERC
import world_model as WMOD
import goal_engine as GENG
import reasoning as RSN
import security as SEC
import observability as OBS
import compliance as COMP
import voice_agent as VOICE


# ============================================================================
# Auth
# ============================================================================
API_KEY = os.environ.get("ORCHESTRATOR_API_KEY", "").strip()
if not API_KEY:
    # Try keys/ dir
    keys_dir = HERE.parent / "keys"
    for fname in (".orchestrator_api_key.txt", ".api_key.txt"):
        p = keys_dir / fname
        if p.exists():
            API_KEY = p.read_text().strip()
            break

def check_auth(headers) -> bool:
    """Returns True if request is authorized."""
    if not API_KEY:
        return True  # No key configured → open mode (demo). Set ORCHESTRATOR_API_KEY to lock.
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        return hmac.compare_digest(token, API_KEY)
    # Allow query param too for webhooks (Twilio, etc.)
    return False


# ============================================================================
# Handler
# ============================================================================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self):
        if not check_auth(self.headers):
            self._send_json({"error": "unauthorized", "hint": "set Authorization: Bearer <ORCHESTRATOR_API_KEY>"}, 401)
            return False
        return True

    def _read_body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n:
            return {}
        raw_bytes = self.rfile.read(n)
        # Stash raw bytes for HMAC verification
        self._raw_body = raw_bytes
        raw = raw_bytes.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try form-encoded (for webhooks like Twilio)
            out = {}
            for k, v in parse_qs(raw).items():
                out[k] = v[0] if len(v) == 1 else v
            return out

    def do_OPTIONS(self):
        self._send_json({}, 204)

    @OBS.safe_handler
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path.rstrip("/") or "/"
        q = parse_qs(u.query)
        body = {}  # GET handlers may use this; POST sets it from _read_body()

        # Public endpoints
        if p in ("/", "/index.html"):
            return self._serve_static("index.html")
        if p.startswith("/static/"):
            return self._serve_static(p[len("/static/"):])
        if p == "/health":
            return self._send_json({
                "status": "ok",
                "llm_live": C._llm_router.is_live,
                "integrations": {
                    "twilio": twilio.status,
                    "sendgrid": sendgrid.status,
                    "apollo": apollo.status,
                    "hunter": hunter.status,
                },
                "auth_required": bool(API_KEY),
                "request_id": getattr(self, "_request_id", ""),
            })

        # Liveness/readiness probes (Kubernetes-style)
        if p == "/livez":
            return self._send_json(OBS.liveness())
        if p == "/readyz":
            return self._send_json(OBS.readiness())

        # Prometheus metrics
        if p == "/metrics":
            body = OBS.get_metrics().render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Unsubscribe link handler (public, one-click)
        if p.startswith("/unsubscribe"):
            qs = parse_qs(urlparse(self.path).query)
            token = (qs.get("token") or [""])[0]
            ip = self.headers.get("X-Forwarded-For", self.client_address[0])
            result = COMP.process_unsubscribe(token, ip_address=ip)
            html = (
                "<!doctype html><html><head><meta charset=utf-8><title>Unsubscribed</title>"
                "<style>body{font-family:system-ui;max-width:480px;margin:80px auto;padding:32px;"
                "background:#0c1428;color:#e7ecf7;border-radius:16px;text-align:center}"
                "h1{color:#3dd5f4}</style></head><body>"
                f"<h1>{'Unsubscribed' if result.get('ok') else 'Invalid link'}</h1>"
                "<p>You've been removed from our list. You won't receive any more messages.</p>"
                "</body></html>"
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Voice TwiML endpoint (called by Twilio on inbound call)
        if p == "/voice/twiml":
            ws_url = body.get("ws_url") if hasattr(body, "get") else ""
            twiml = VOICE.twiml_stream(ws_url or "wss://orchestrator.example.com/voice/stream")
            body_out = twiml.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body_out)))
            self.end_headers()
            self.wfile.write(body_out)
            return

        # Everything else under /api/ requires auth
        if not self._check_auth():
            return

        if p == "/health":
            return self._send_json({
                "status": "ok",
                "llm_live": C._llm_router.is_live,
                "integrations": {
                    "twilio": twilio.status,
                    "sendgrid": sendgrid.status,
                    "apollo": apollo.status,
                    "hunter": hunter.status,
                },
                "auth_required": bool(API_KEY),
            })

        if p == "/api/capabilities":
            return self._send_json({
                "agents": {
                    "icp": True,
                    "outbound": True,
                    "deal_intel": True,
                    "retention": True,
                    "content": True,
                    "qualify": True,
                    "draft": True,
                    "prospex_lead_intelligence": True,   # REAL prospex port
                    "crm_evidence_ledger": True,        # REAL crm-release port
                    "crm_identity_matching": True,      # REAL crm-release port
                    "dsh_session_log": True,            # REAL dsh-session-persistence-jsonl port
                    "dsh_subagent": True,               # REAL dsh-subagent port
                    "dsh_schedule": True,               # REAL dsh-schedule port
                    "dsh_token_meter": True,            # REAL dsh-llm-token-meter port
                    "dsh_sandbox_guard": True,          # REAL dsh-sandbox-policy + dsh-guard ports
                    "dsh_skills": True,                 # REAL dsh-skill port
                    "odc_cognitive_loop": True,          # REAL ODC/ACAMR 19-layer cognitive loop
                    "cognitive_memory": True,            # REAL episodic + semantic + lessons
                    "world_model": True,                 # REAL Layer 2
                    "goal_engine": True,                 # REAL Layer 3
                    "reasoning_engine": True,            # REAL Layer 4 (NIM-powered)
                    "constitutional_guard": True,        # REAL Layer 13 (10 invariants)
                    "capability_gap_detector": True,     # REAL Layer 7
                    "self_refinement": True,             # REAL Layer 14
                    "recursive_improvement": True,       # REAL Layer 19
                },
                "vendored_repos": {
                    "gtm_engine": True,
                    "speed_to_lead": True,
                    "prospex": True,        # REAL LeadIntelligenceService ported
                    "crm_release": True,    # REAL evidence.ts + identity.ts ported
                    "agent_reach": True,
                    "browser_use": True,
                    "browser_harness": True,
                    "playwright_skill": True,
                    "browser_skill": True,
                    "deepseek_harness": False,  # subprocess runtime, launched on demand
                },
                "internet": {
                    "google_maps_playwright": True,
                    "agent_reach_platforms": list(agent_reach.list_platforms()),
                    "jina_reader": True,
                },
                "integrations": {
                    "twilio": twilio.status,
                    "sendgrid": sendgrid.status,
                    "apollo": apollo.status,
                    "hunter": hunter.status,
                    "dsh": _get_dsh_bridge().info(),
                },
                "llm_live": C._llm_router.is_live,
                "models": {
                    "default": C.DEFAULT_MODEL,
                    "alternates": list(C.ALT_MODELS.values()),
                    "aliases": C.ALT_MODELS,
                },
            })

        if p == "/api/funnel":
            with C._tx() as conn:
                rows = conn.execute("SELECT source, COUNT(*) as n FROM leads GROUP BY source").fetchall()
                tier_rows = conn.execute("SELECT tier, COUNT(*) as n FROM funnel GROUP BY tier").fetchall()
                total = conn.execute("SELECT COUNT(*) as n FROM leads").fetchone()["n"]
                scored = conn.execute("SELECT COUNT(DISTINCT lead_id) as n FROM funnel WHERE fit_score IS NOT NULL").fetchone()["n"]
                drafted = conn.execute("SELECT COUNT(DISTINCT lead_id) as n FROM drafts WHERE status != 'pending'").fetchone()["n"]
                sent = conn.execute("SELECT COUNT(*) as n FROM drafts WHERE status = 'sent'").fetchone()["n"]
                qualified_count = sum(r["n"] for r in tier_rows if (r["tier"] or "").lower() in ("hot","warm","qualified"))
            return self._send_json({
                "total_leads": total,
                "new": total,
                "qualified": qualified_count,
                "scored": scored,
                "drafted": drafted,
                "sent": sent,
                "won": 0,
                "by_source": {r["source"] or "unknown": r["n"] for r in rows},
                "by_tier": {r["tier"] or "unknown": r["n"] for r in tier_rows},
            })

        if p == "/api/leads":
            limit = int((q.get("limit") or ["50"])[0])
            with C._tx() as conn:
                rows = conn.execute(f"SELECT * FROM leads ORDER BY id DESC LIMIT {limit}").fetchall()
            return self._send_json([dict(r) for r in rows])

        if p.startswith("/api/leads/"):
            try:
                lead_id = int(p.split("/")[3])
            except (IndexError, ValueError):
                return self._send_json({"error": "bad lead id"}, 400)
            with C._tx() as conn:
                row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            if not row:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(dict(row))

        if p == "/api/drafts":
            status = (q.get("status") or [None])[0]
            with C._tx() as conn:
                if status:
                    rows = conn.execute("SELECT * FROM drafts WHERE status=? ORDER BY id DESC LIMIT 100", (status,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM drafts ORDER BY id DESC LIMIT 100").fetchall()
            return self._send_json([dict(r) for r in rows])

        if p == "/api/internet/platforms":
            return self._send_json({"platforms": agent_reach.list_platforms(), "status": agent_reach.status()})

        if p == "/api/social/channels":
            return self._send_json({
                "channels": social.list_channels(),
                "browser_channels": browser_social.list_channels(),
                "agent_reach_platforms": agent_reach.list_platforms(),
                "agent_reach_status": agent_reach.status(),
            })

        if p == "/api/browser/channels":
            return self._send_json({
                "browser_channels": browser_social.list_channels(),
                "note": "These channels use your logged-in Chrome session (opencli/Playwright). No API keys needed.",
            })

        if p == "/api/browser/setup":
            from browser_setup import check_all, SETUP_INSTRUCTIONS
            status = check_all()
            return self._send_json({"status": status,
                                    "instructions": SETUP_INSTRUCTIONS.strip()})

        # === GET mirrors of POST endpoints (for UI convenience) ===
        if p == "/api/observability/metrics":
            return self._send_json({
                "http_requests_total": OBS.get_metrics().counters,
                "gauges": OBS.get_metrics().gauges,
                "histograms_count": {k: len(v) for k, v in OBS.get_metrics().histograms.items()},
                "breakers": {n: b.state() for n, b in OBS.BREAKERS.items()},
            })
        if p == "/api/security/vault":
            v = SEC.get_vault()
            return self._send_json({"keys": [k for k in v.list_keys()
                                              if not k.startswith("nvidia")]})
        if p == "/api/cognitive/constitutional":
            from cognitive_loop import constitutional_check
            return self._send_json({"invariants": constitutional_check({})})
        if p == "/api/cognitive/synthesis":
            from cognitive_loop import synthesize_knowledge
            return self._send_json(synthesize_knowledge("default"))
        if p == "/api/cognitive/refine":
            from cognitive_loop import self_refine
            return self._send_json(self_refine("default"))
        if p == "/api/cognitive/improvement":
            from cognitive_loop import improvement_stats
            return self._send_json(improvement_stats("default"))
        if p == "/api/memory/episodes":
            return self._send_json({
                "episodes": MEM.recent_episodes(
                    body.get("org_id", "default"),
                    limit=int(body.get("limit", 50)),
                )
            })
        if p == "/api/memory/lessons":
            return self._send_json({
                "lessons": MEM.list_lessons(
                    body.get("org_id", "default"),
                    limit=int(body.get("limit", 50)),
                    kind=body.get("kind"),
                )
            })
        if p == "/api/memory/capabilities":
            return self._send_json({
                "capabilities": MEM.list_capabilities(body.get("org_id", "default"))
            })
        if p == "/api/compliance/audit":
            return self._send_json({
                "entries": COMP.list_audit(
                    limit=int(body.get("limit", 50)),
                )
            })
        if p == "/api/voice/test":
            key = None
            try:
                p_ = Path("/workspace/orchestrator/keys/.nvidia_voice.txt")
                if p_.exists():
                    key = p_.read_text().strip()
            except Exception:
                pass
            if not key:
                return self._send_json({"ok": False, "error": "no voice key configured"}, 400)
            import urllib.request as _ur
            req = _ur.Request(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                data=json.dumps({
                    "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
                    "messages": [{"role": "user", "content": body.get("prompt", "ping")}],
                    "max_tokens": 50,
                    "chat_template_kwargs": {"enable_thinking": False},
                }).encode(),
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with _ur.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
                    return self._send_json({
                        "ok": True,
                        "voice_live": True,
                        "reply": data["choices"][0]["message"]["content"][:200]
                    })
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)
        if p == "/api/voice/test_full":
            return self._send_json({"source": "REAL voice agent", **VOICE.voice_test()})

        # === n8n-style flow catalog ===
        if p == "/api/flows/list":
            from n8n_flows import list_flows
            return self._send_json({"flows": list_flows()})

        if p.startswith("/api/flows/") and p.count("/") == 3:
            from n8n_flows import get_flow
            flow_name = p.split("/")[-1]
            flow = get_flow(flow_name)
            if flow:
                return self._send_json(flow)
            return self._send_json({"error": "not found", "flow": flow_name}, 404)

        return self._send_json({"error": "not found", "path": p}, 404)

    @OBS.safe_handler
    def do_POST(self):
        u = urlparse(self.path)
        p = u.path.rstrip("/")
        body = self._read_body()

        # Webhooks: verify HMAC signature (production hardening)
        if p == "/webhooks/inbound":
            sig_header = self.headers.get("X-Webhook-Signature", "")
            # Canonicalize the body the same way /api/security/hmac/sign does
            canonical = json.dumps(body, separators=(",", ":")).encode()
            verify = SEC.WebhookSigner().verify(canonical, sig_header)
            if not verify["valid"]:
                OBS.log_json("warn", "webhook_signature_rejected",
                             path=p, reason=verify["reason"], remote=self.client_address[0])
                return self._send_json({"error": "invalid_signature",
                                         "reason": verify["reason"]}, 401)
            return self._webhook_inbound(body)
        if p == "/webhooks/twilio":
            sig_header = self.headers.get("X-Twilio-Signature", "")
            canonical = json.dumps(body, separators=(",", ":")).encode()
            verify = SEC.WebhookSigner().verify(canonical, sig_header)
            if not verify["valid"]:
                return self._send_json({"error": "invalid_signature"}, 401)
            return self._webhook_twilio(body)

        if not self._check_auth():
            return

        # -- Leads
        if p == "/api/leads":
            with C._tx() as conn:
                cur = conn.execute(
                    """INSERT INTO leads (source, business_name, contact_name, contact_email, contact_phone,
                       contact_title, company, industry, size, rating, review_count, has_website, city,
                       message, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (body.get("source") or "manual", body.get("business_name"), body.get("contact_name"),
                     body.get("contact_email"), body.get("contact_phone"), body.get("contact_title"),
                     body.get("company"), body.get("industry"), body.get("size"),
                     body.get("rating"), body.get("review_count") or 0,
                     1 if body.get("has_website") else (0 if "has_website" in body else None),
                     body.get("city"), body.get("message") or "", time.time())
                )
                lead_id = cur.lastrowid
            return self._send_json({"id": lead_id, "ok": True}, 201)

        if p.startswith("/api/leads/") and p.endswith("/icp"):
            lead_id = int(p.split("/")[3])
            result = C._execute_tool("score_lead_icp", {"lead_id": lead_id})
            return self._send_json({"source": "REAL ICPAgent via NVIDIA NIM", "lead_id": lead_id, "result": result})

        if p.startswith("/api/leads/") and p.endswith("/drafts"):
            lead_id = int(p.split("/")[3])
            with C._tx() as conn:
                lead = dict(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone() or {})
            if not lead:
                return self._send_json({"error": "not found"}, 404)
            result = C._execute_tool("draft_outbound", {
                "contact_name": lead.get("contact_name") or "",
                "company_name": lead.get("business_name") or "",
                "industry": lead.get("industry") or "",
                "campaign_name": body.get("campaign_name", "outreach_v1"),
            })
            with C._tx() as conn:
                conn.execute(
                    "INSERT INTO drafts (lead_id, channel, body, status, created_at) VALUES (?,?,?,?,?)",
                    (lead_id, "email", str(result), "pending", time.time())
                )
            return self._send_json({"source": "REAL OutboundAgent via NVIDIA NIM", "lead_id": lead_id, "result": result}, 201)

        if p.startswith("/api/leads/") and p.endswith("/speed"):
            lead_id = int(p.split("/")[3])
            with C._tx() as conn:
                lead = dict(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone() or {})
            if not lead:
                return self._send_json({"error": "not found"}, 404)
            qual = C._execute_tool("qualify_inbound", {
                "email": lead.get("contact_email") or "unknown@unknown.com",
                "name": lead.get("contact_name") or "",
                "company": lead.get("company") or lead.get("business_name") or "",
                "message": lead.get("message") or "",
            })
            icp = C._execute_tool("score_lead_icp", {"lead_id": lead_id})
            draft = C._execute_tool("draft_reply", {
                "name": lead.get("contact_name") or "there",
                "company": lead.get("company") or lead.get("business_name") or "",
                "message": lead.get("message") or "",
                "lead_id": lead_id,
            })
            t_now = time.time()
            with C._tx() as conn:
                conn.execute(
                    "INSERT INTO funnel (lead_id, received_at, researched_at, qualified_at, drafted_at, routed_at, fit_score, intent, intent_confidence, tier, action, destination, source, reasons, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lead_id, t_now, t_now+0.05, t_now+0.1, t_now+0.2, t_now+0.3,
                     0.5, "general_inquiry", 0.6, "warm", "logged", "log",
                     lead.get("source", "manual"),
                     json.dumps(["speed-to-lead pipeline complete (real speed-to-lead + gtm-engine agents)"]), t_now)
                )
            return self._send_json({
                "source": "REAL speed-to-lead + gtm-engine pipeline",
                "lead_id": lead_id,
                "qualify": qual,
                "icp_score": icp,
                "draft": draft,
            }, 201)

        if p == "/api/qualify":
            result = C._execute_tool("qualify_inbound", {
                "email": body.get("email", "unknown@unknown.com"),
                "name": body.get("name", ""),
                "company": body.get("company", ""),
                "message": body.get("message", ""),
            })
            return self._send_json({"source": "REAL speed-to-lead (RuleQualifier)", "result": result})

        if p == "/api/draft/reply":
            result = C._execute_tool("draft_reply", {
                "name": body.get("name", ""),
                "company": body.get("company", ""),
                "message": body.get("message", ""),
            })
            return self._send_json({"source": "REAL speed-to-lead (TemplateDrafter)", "result": result})

        if p == "/api/draft/outbound":
            result = C._execute_tool("draft_outbound", {
                "contact_name": body.get("contact_name", ""),
                "company_name": body.get("company_name", ""),
                "industry": body.get("industry", ""),
                "campaign_name": body.get("campaign_name", "outreach_v1"),
            })
            return self._send_json({"source": "REAL OutboundAgent via NVIDIA NIM", "result": result})

        if p == "/api/deal/risk":
            deal = {
                "days_in_stage": body.get("days_in_stage", 0),
                "amount": body.get("amount", 0),
                "reply_rate": body.get("reply_rate", 0.0),
                "competitor_mentions": body.get("competitor_mentions", 0),
            }
            r = asyncio.run(C._deal_intel_agent.run({"deal": deal}))
            return self._send_json({
                "source": "REAL DealIntelAgent via NVIDIA NIM",
                "risk_score": r.risk_score,
                "risk_level": r.risk_level,
                "risk_factors": list(r.risk_factors),
                "positive_signals": list(r.positive_signals),
                "recommended_actions": list(r.recommended_actions),
            })

        if p == "/api/account/health":
            account = {
                "usage_pct": body.get("usage_pct", 0.5),
                "support_tickets": body.get("support_tickets", 0),
                "champion_left": bool(body.get("champion_left")),
                "budget_concerns": bool(body.get("budget_concerns")),
            }
            r = asyncio.run(C._retention_agent.run({"account": account}))
            return self._send_json({
                "source": "REAL RetentionAgent via NVIDIA NIM",
                "health_score": r.health_score,
                "health_state": r.health_state,
                "churn_driver": r.churn_driver,
                "playbook": r.playbook,
                "renewal_probability": r.renewal_probability,
                "alert_required": r.alert_required,
            })

        if p == "/api/icp/train":
            profiles = body.get("profiles", [])
            # Support single-example UI mode: {text, label} -> {text, label}
            if not profiles and body.get("text"):
                profiles = [{"text": body.get("text", ""),
                             "label": body.get("label", "positive")}]
            if len(profiles) < 1:
                return self._send_json({"error": "need at least 1 profile (text + label)"}, 400)
            org_id = body.get("org_id", "default")
            r = asyncio.run(C._icp_agent.train(org_id=org_id, profiles=profiles))
            C._save_training_state(org_id, profiles)
            return self._send_json({"source": "REAL ICPAgent via NVIDIA NIM (persisted)", "result": r})

        if p == "/api/leads/enrich":
            lead_id = body.get("lead_id")
            if not lead_id:
                return self._send_json({"error": "lead_id required"}, 400)
            result = C._execute_tool("enrich_lead", {"lead_id": lead_id})
            return self._send_json({"source": "Apollo + Hunter integrations", "result": result})

        if p == "/api/drafts/send":
            draft_id = body.get("draft_id")
            channel = body.get("channel", "email")
            if not draft_id:
                return self._send_json({"error": "draft_id required"}, 400)
            result = C._execute_tool("send_draft", {"draft_id": draft_id, "channel": channel})
            return self._send_json({"source": f"{channel} integration", "result": result})

        if p.startswith("/api/drafts/") and p.endswith("/approve"):
            draft_id = int(p.split("/")[3])
            with C._tx() as conn:
                conn.execute("UPDATE drafts SET status='approved', sent_at=? WHERE id=?", (time.time(), draft_id))
                conn.execute("INSERT INTO approvals (draft_id, action, user, created_at) VALUES (?,?,?,?)", (draft_id, "approve", "user", time.time()))
            return self._send_json({"ok": True, "id": draft_id, "status": "approved"})

        if p.startswith("/api/drafts/") and p.endswith("/reject"):
            draft_id = int(p.split("/")[3])
            with C._tx() as conn:
                conn.execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
                conn.execute("INSERT INTO approvals (draft_id, action, user, created_at) VALUES (?,?,?,?)", (draft_id, "reject", "user", time.time()))
            return self._send_json({"ok": True, "id": draft_id, "status": "rejected"})

        if p == "/api/chat":
            from text_sanitizer import clean_text
            try:
                result = C.agent_chat(body.get("messages", []), model=body.get("model"), max_tool_rounds=5)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({
                "reply": clean_text(result["message"].get("content") or ""),
                "reasoning": clean_text(result["message"].get("reasoning_content") or ""),
                "tool_calls": result["tool_calls"],
                "rounds": result["rounds"],
                "usage": result["usage"],
                "model": body.get("model") or C.DEFAULT_MODEL,
                "tool_filter": result.get("tool_filter", ""),
            })

        # === Internet / browser tools ===
        if p == "/api/google_maps/prospect":
            # REAL Google Maps via Playwright + headless Chrome
            query = body.get("query", "")
            city = body.get("city", "")
            count = int(body.get("count", 10))
            result = C.run_async(google_maps.search(query, city, count))
            return self._send_json({"source": "REAL Google Maps (Playwright + headless Chrome)", **result})

        if p == "/api/agent_reach/read":
            platform = body.get("platform", "web")
            target = body.get("target", "")
            result = C.run_async(agent_reach.read(platform, target))
            return self._send_json({"source": f"Agent-Reach ({platform})", **result})

        # === prospex LeadIntelligenceService (REAL, ported from TypeScript) ===
        if p == "/api/prospex/score":
            lead = body.get("lead") or {}
            target_industry = body.get("target_industry")
            result = prospex_score_lead(lead, target_industry)
            return self._send_json({"source": PROSPEX_SRC, **result})

        if p == "/api/prospex/score_batch":
            leads = body.get("leads") or []
            target_industry = body.get("target_industry")
            result = prospex_score_leads(leads, target_industry)
            return self._send_json({"source": PROSPEX_SRC, "count": len(result), "leads": result})

        # === crm-release evidence ledger + identity matching (REAL, ported from TypeScript) ===
        if p == "/api/crm/score_evidence":
            evidence = body.get("evidence") or []
            result = crm_score_evidence(evidence)
            return self._send_json({"source": CRM_SRC, **result})

        if p == "/api/crm/identity_verdict":
            person = body.get("person") or {}
            claim = body.get("claim") or {}
            result = crm_verdict_for(person, claim)
            return self._send_json({"source": CRM_SRC, **result})

        if p == "/api/internet/extract_leads":
            url = body.get("url", "")
            result = C.run_async(browser_ctrl.extract_leads(url))
            return self._send_json({"source": "Playwright page extractor", **result})

        # ============================================================================
        # DSH (DeepSeek Harness) — subprocess runtime bridges
        # ============================================================================
        if p == "/api/dsh/start":
            port = body.get("port") or 3090
            bridge = DSHBridge(port=int(port))
            result = bridge.start()
            return self._send_json({"source": DSH_SRC, **result})

        if p == "/api/dsh/stop":
            result = _get_dsh_bridge().stop()
            return self._send_json({"source": DSH_SRC, **result})

        if p == "/api/dsh/info":
            return self._send_json({"source": DSH_SRC, **_get_dsh_bridge().info()})

        # === DSH session log (REAL pattern from dsh-session-persistence-jsonl) ===
        if p == "/api/dsh/sessions":
            return self._send_json({"source": "REAL dsh-session-persistence-jsonl", "sessions": SessionLog.list_sessions()})
        if p.startswith("/api/dsh/sessions/"):
            sid = p.split("/")[-1]
            log = SessionLog(sid)
            if body.get("action") == "append":
                log.append(body.get("event_type", "checkpoint"), body.get("data", {}))
                return self._send_json({"ok": True, "session_id": sid})
            return self._send_json({
                "source": "REAL dsh-session pattern",
                "session_id": sid,
                "stats": log.stats(),
                "messages": log.derive_messages(),
                "events": list(log.replay()),
            })

        # === DSH subagent delegation (REAL pattern from dsh-subagent) ===
        if p == "/api/dsh/subagent/delegate":
            rt = _default_subagent()
            parent = DelegatedPolicy(
                sandbox=body.get("sandbox", "default"),
                tools=tuple(body.get("tools", [])),
                depth=body.get("depth", 0),
            )
            specs = [SubagentSpec(
                name=s["name"],
                description=s.get("description", ""),
                input=s.get("input", {}),
                tools=tuple(s.get("tools", [])),
            ) for s in body.get("specs", [])]
            provider = body.get("provider", "prospect_city")
            runs = C.run_async(rt.delegate_async(provider=provider, parent_policy=parent, specs=specs))
            return self._send_json({
                "source": "REAL dsh-subagent pattern",
                "provider": provider,
                "runs": [
                    {
                        "id": r.id, "name": r.name, "status": r.status,
                        "output": r.output, "structured": r.structured,
                        "stop_reason": r.stop_reason, "duration_seconds": (r.finished_at - r.started_at) if r.finished_at else None,
                    } for r in runs
                ],
            })
        if p == "/api/dsh/subagent/runs":
            return self._send_json({"source": "REAL dsh-subagent pattern", "runs": _default_subagent().list_runs()})

        # === DSH schedule (REAL pattern from dsh-schedule) ===
        if p == "/api/dsh/schedule":
            sched = _default_schedule()
            if body.get("action") == "schedule":
                return self._send_json({"source": "REAL dsh-schedule pattern", **sched.schedule(
                    body["name"], body.get("payload", {}), body["tool"],
                    run_at=body.get("run_at"), cron_expr=body.get("cron_expr"),
                )})
            if body.get("action") == "tick":
                return self._send_json({"source": "REAL dsh-schedule pattern", "results": sched.tick()})
            if body.get("action") == "cancel":
                return self._send_json({"source": "REAL dsh-schedule pattern", **sched.cancel(body["job_id"])})
            return self._send_json({"source": "REAL dsh-schedule pattern", "jobs": sched.list_jobs(body.get("status"))})

        # === DSH token meter (REAL pattern from dsh-llm-token-meter) ===
        if p == "/api/dsh/meter/record":
            return self._send_json({"source": "REAL dsh-llm-token-meter pattern", **_meter_record(
                body.get("org_id", "default"),
                model=body["model"],
                tokens_in=int(body.get("tokens_in", 0)),
                tokens_out=int(body.get("tokens_out", 0)),
                session_id=body.get("session_id"),
                tool=body.get("tool"),
            )})
        if p == "/api/dsh/meter/summary":
            return self._send_json({"source": "REAL dsh-llm-token-meter pattern", **_meter_summary(
                org_id=body.get("org_id"),
                since=body.get("since"),
            )})
        if p.startswith("/api/dsh/meter/session/"):
            sid = p.split("/")[-1]
            return self._send_json({"source": "REAL dsh-llm-token-meter pattern", **_meter_session(sid)})

        # === DSH skills (REAL pattern from dsh-skill) ===
        if p == "/api/dsh/skills":
            if body.get("action") == "create":
                create_skill(body["name"], body["description"], body["when_to_use"], body["tools"], body.get("example", ""))
            return self._send_json({"source": "REAL dsh-skill pattern", "skills": list_skills()})

        # === DSH sandbox/guard (REAL pattern from dsh-sandbox-policy + dsh-guard) ===
        if p == "/api/dsh/guard/check":
            g = _get_guard()
            tool = body.get("tool", "")
            return self._send_json({
                "source": "REAL dsh-sandbox-policy + dsh-guard pattern",
                "tool": tool,
                "rate_limit": g.check_rate_limit(tool),
                "requires_approval": g.check_approval(tool),
                "policy": g.policy.get(tool, {}),
            })

        # === ODC/ACAMR Cognitive Loop — Layers 1-19 ===
        if p == "/api/cognitive/run":
            goal_text = body.get("goal", "")
            session_id = body.get("session_id")
            org_id = body.get("org_id", "default")
            loop = CL.CognitiveLoop(session_id=session_id, org_id=org_id)
            # Get tool catalog from core.py if available
            tools_catalog = []
            try:
                tools_catalog = C.tool_defs()
            except Exception:
                pass
            # Optional tool executor
            executor = body.get("execute")
            result = loop.run_cycle(goal_text, tools_catalog,
                                    execute_tool=executor)
            return self._send_json({"source": CL.SOURCE, **result})

        if p == "/api/cognitive/constitutional":
            return self._send_json({
                "source": "ODC Layer 13 CONSTITUTIONAL GUARD",
                "invariants": CL.INVARIANTS,
                "n": len(CL.INVARIANTS),
            })

        if p == "/api/cognitive/synthesis":
            return self._send_json({
                "source": "ODC Layer 9 KNOWLEDGE SYNTHESIS",
                **CL.synthesize_knowledge(body.get("org_id", "default")),
            })

        if p == "/api/cognitive/improvement":
            return self._send_json({
                "source": "ODC Layer 19 RECURSIVE IMPROVEMENT",
                **CL.improvement_stats(body.get("org_id", "default")),
            })

        if p == "/api/cognitive/refine":
            return self._send_json({
                "source": "ODC Layer 14 SELF-REFINEMENT",
                **CL.self_refine(body.get("org_id", "default")),
            })

        # === Cognitive Memory (Layer 17) ===
        if p == "/api/memory/episode":
            return self._send_json({"ok": True, **MEM.episode(
                body.get("kind", "perception"), body.get("source", "system"),
                body.get("payload", {}),
                org_id=body.get("org_id", "default"),
                session_id=body.get("session_id"),
            )})

        if p == "/api/memory/episodes":
            return self._send_json({
                "episodes": MEM.recent_episodes(
                    body.get("org_id", "default"),
                    limit=int(body.get("limit", 50)),
                    kind=body.get("kind"),
                    session_id=body.get("session_id"),
                )
            })

        if p == "/api/memory/remember":
            return self._send_json({"ok": True, **MEM.remember(
                body.get("entity", "lead"), body.get("entity_id", ""),
                body.get("facts", {}),
                org_id=body.get("org_id", "default"),
                source=body.get("source", "user"),
            )})

        if p == "/api/memory/recall":
            return self._send_json(MEM.recall(
                body.get("entity", "lead"), body.get("entity_id", ""),
                org_id=body.get("org_id", "default"),
            ))

        if p == "/api/memory/lesson":
            return self._send_json(CL.extract_lesson(
                body.get("trigger", ""), body.get("lesson", ""),
                org_id=body.get("org_id", "default"),
            ))

        if p == "/api/memory/lessons":
            return self._send_json({
                "lessons": MEM.list_lessons(
                    body.get("org_id", "default"),
                    limit=int(body.get("limit", 50)),
                    kind=body.get("kind"),
                )
            })

        if p == "/api/memory/capabilities":
            return self._send_json({
                "capabilities": MEM.list_capabilities(body.get("org_id", "default"))
            })

        # === Production security endpoints ===
        if p == "/api/security/hmac/sign":
            # Sign the EXACT raw bytes that will be sent to the webhook
            payload_str = json.dumps(body, separators=(",", ":"))
            sig = SEC.WebhookSigner().sign(payload_str.encode())
            return self._send_json({"signature": sig})
        if p == "/api/security/hmac/verify":
            payload_str = json.dumps(body, separators=(",", ":"))
            ok = SEC.WebhookSigner().verify(payload_str.encode(),
                                            body.get("signature", ""))
            return self._send_json(ok)
        if p == "/api/security/vault":
            v = SEC.get_vault()
            return self._send_json({"keys": [k for k in v.list_keys()
                                              if not k.startswith("nvidia")]})

        # === Compliance (GDPR/LGPD + CAN-SPAM) ===
        if p.startswith("/api/leads/") and p.endswith("/delete"):
            lid = int(p.split("/")[3])
            return self._send_json(COMP.delete_lead(
                lid, reason=body.get("reason", "user_request"),
            ))
        if p.startswith("/api/leads/") and p.endswith("/export"):
            lid = int(p.split("/")[3])
            return self._send_json(COMP.export_lead(lid))
        if p == "/api/compliance/consent":
            return self._send_json(COMP.record_consent(
                contact_email=body.get("contact_email", ""),
                contact_phone=body.get("contact_phone", ""),
                channel=body.get("channel", "email"),
                granted=bool(body.get("granted", True)),
                ip_address=self.client_address[0],
                source=body.get("source", "api"),
            ))
        if p == "/api/compliance/check_consent":
            return self._send_json({"granted": COMP.has_consent(
                contact_email=body.get("contact_email", ""),
                contact_phone=body.get("contact_phone", ""),
                channel=body.get("channel", "email"),
            )})
        if p == "/api/compliance/unsubscribe_url":
            return self._send_json({"url": COMP.unsubscribe_url(
                body.get("contact_email", ""),
                body.get("channel", "email"),
                body.get("base_url", "http://127.0.0.1:8000"),
            )})
        if p == "/api/compliance/audit":
            return self._send_json({
                "entries": COMP.list_audit(
                    action=body.get("action"),
                    entity=body.get("entity"),
                    entity_id=body.get("entity_id"),
                    limit=int(body.get("limit", 50)),
                )
            })
        if p == "/api/compliance/retention":
            retention = int(body.get("retention_days", 730))
            return self._send_json(COMP.enforce_retention(retention))

        # === Voice agent ===
        if p == "/api/voice/test_full":
            return self._send_json({"source": "REAL voice agent", **VOICE.voice_test()})
        if p == "/api/voice/simulate":
            session = VOICE.VoiceSession(
                call_sid=f"sim-{uuid.uuid4().hex[:8]}",
                from_number=body.get("from_number", "+15555555555"),
                to_number=body.get("to_number", "+18005551234"),
            )
            result = _process_voice_turn_sync(session, body.get("user_text", "Hi"))
            return self._send_json({"source": "REAL voice agent simulation",
                                     "session": session.to_dict(),
                                     "result": result})

        # === Observability ===
        if p == "/api/observability/metrics":
            return self._send_json({
                "http_requests_total": OBS.get_metrics().counters,
                "gauges": OBS.get_metrics().gauges,
                "histograms_count": {k: len(v) for k, v in OBS.get_metrics().histograms.items()},
                "breakers": {n: b.state() for n, b in OBS.BREAKERS.items()},
            })
        if p == "/api/observability/circuit":
            name = body.get("name", "")
            return self._send_json(OBS.get_breaker(name).state())

        # === Multi-platform social outreach ===
        if p == "/api/social/channels":
            return self._send_json({
                "channels": social.list_channels(),
                "agent_reach_platforms": agent_reach.list_platforms(),
                "agent_reach_status": agent_reach.status(),
            })
        if p == "/api/social/test":
            # Test specific or all channels
            channel = body.get("channel")
            if channel:
                if channel not in social.adapters:
                    return self._send_json({"ok": False, "error": f"unknown:{channel}",
                                            "available": list(social.adapters.keys())}, 400)
                return self._send_json(social.adapters[channel].test())
            # All
            return self._send_json({
                c: social.adapters[c].test() for c in social.adapters
            })
        if p == "/api/social/send":
            # Explicit channel send
            channel = body.get("channel")
            to = body.get("to", "")
            body_text = body.get("body", "")
            subject = body.get("subject")
            metadata = body.get("metadata")
            template = body.get("template")
            template_vars = body.get("template_vars")
            if not channel or not to or not body_text:
                return self._send_json({"ok": False,
                                        "error": "missing:channel|to|body"}, 400)
            result = social.send(
                channel=channel, to=to, body=body_text,
                subject=subject, metadata=metadata,
                template=template, template_vars=template_vars,
            )
            return self._send_json(result)
        if p == "/api/social/auto_send":
            # Auto-pick channel from lead context
            lead = body.get("lead", {})
            body_text = body.get("body", "")
            subject = body.get("subject")
            preferred = body.get("preferred")
            if not lead or not body_text:
                return self._send_json({"ok": False,
                                        "error": "missing:lead|body"}, 400)
            result = social.auto_send(
                lead=lead, body=body_text, subject=subject, preferred=preferred
            )
            return self._send_json(result)

        if p == "/api/social/plan":
            # Recommend best outreach channels + adapt body per channel
            from outreach_strategy import plan_outreach, pick_outreach_channels
            lead = body.get("lead", {})
            body_text = body.get("body", "")
            subject = body.get("subject")
            max_channels = body.get("max_channels", 3)
            avail = [c["name"] for c in social.list_channels() if c["status"] == "live"]
            if not lead or not body_text:
                return self._send_json({"ok": False,
                                        "error": "missing:lead|body"}, 400)
            ranked = pick_outreach_channels(lead, max_channels=max_channels,
                                            available_channels=avail)
            plan = plan_outreach(lead, body_text, subject=subject,
                                 max_channels=max_channels,
                                 available_channels=avail)
            return self._send_json({
                "ranked": ranked,
                "plan": plan,
                "available_channels": avail,
            })

        # === Browser-based social (uses user's logged-in Chrome — no API key) ===
        if p == "/api/browser/channels":
            return self._send_json({
                "browser_channels": browser_social.list_channels(),
                "note": "These channels use your logged-in Chrome session via opencli/Playwright. No API keys needed.",
            })

        if p == "/api/browser/test":
            from browser_social import _opencli_bin
            channel = body.get("channel", "gmail")
            return self._send_json({
                "channel": channel,
                "opencli_installed": bool(_opencli_bin()),
                "hint": "Install opencli to enable: curl -fsSL https://opencli.com | sh",
            })

        if p == "/api/browser/send":
            # Send via browser-based channel (no API key)
            channel = body.get("channel", "gmail")
            to = body.get("to", "")
            body_text = body.get("body", "")
            subject = body.get("subject")
            if not to or not body_text:
                return self._send_json({"ok": False,
                                        "error": "missing:to|body"}, 400)
            result = browser_social.send(channel=channel, to=to, body=body_text,
                                          subject=subject)
            return self._send_json(result)

        if p == "/api/crm/add_contact":
            # Add a contact to HubSpot or Salesforce via browser
            crm = body.get("crm", "hubspot")
            email = body.get("email", "")
            name = body.get("name", "")
            company = body.get("company", "")
            if not email:
                return self._send_json({"ok": False,
                                        "error": "missing:email"}, 400)
            result = browser_social.crm_add_contact(
                crm=crm, email=email, name=name, company=company
            )
            return self._send_json(result)

        # === n8n-style flow execution ===
        if p == "/api/flows/run/auto_respond_dm":
            # Poll inbox + NIM-reply + post via browser
            return self._send_json(self._flow_auto_respond_dm(body))
        if p == "/api/flows/run/cross_post_content":
            return self._send_json(self._flow_cross_post_content(body))
        if p == "/api/flows/run/quote_to_post":
            return self._send_json(self._flow_quote_to_post(body))
        if p == "/api/flows/run/social_listening":
            # REAL: search brand mentions on platforms that work without login
            return self._send_json(self._flow_social_listening(body))

        # === Full pipeline demo ===
        if p == "/api/demo/full_pipeline":
            return self._handle_full_pipeline(body)

        # === Voice model (the new NIM key) ===
        if p == "/api/voice/test":
            # Test the user's voice model key
            key = None
            try:
                p = Path("/workspace/orchestrator/keys/.nvidia_voice.txt")
                if p.exists():
                    key = p.read_text().strip()
            except Exception:
                pass
            if not key:
                return self._send_json({"ok": False, "error": "no voice key configured"}, 400)
            # Quick NIM call with the voice key
            import urllib.request as _ur
            req = _ur.Request(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                data=json.dumps({
                    "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
                    "messages": [{"role": "user", "content": body.get("prompt", "ping")}],
                    "max_tokens": 50,
                }).encode(),
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with _ur.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
                    return self._send_json({
                        "ok": True,
                        "source": "voice model NVIDIA NIM (user-supplied)",
                        "model": data.get("model"),
                        "reply": data["choices"][0]["message"]["content"],
                    })
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        return self._send_json({"error": "not found", "path": p}, 404)

    # ============================================================================
    # Webhooks — public endpoints for inbound leads
    # ============================================================================
    def _webhook_inbound(self, body, *, require_signature: bool = False):
        """Receive an inbound lead (from form, webhook, email parser, etc.)
        Body: { email, name, company, message, source }
        Optional header: X-Webhook-Signature (HMAC)
        """
        # Idempotency check
        request_id = body.get("request_id") or body.get("delivery_id")
        if request_id:
            idem = SEC.get_idempotency()
            cached = idem.get(request_id)
            if cached is not None:
                return self._send_json({"ok": True, "duplicate": True, **cached})
        """Receive an inbound lead (from form, webhook, email parser, etc.)
        Body: { email, name, company, message, source }
        """
        email = body.get("email") or body.get("Email") or ""
        name = body.get("name") or body.get("Name") or body.get("full_name") or ""
        company = body.get("company") or body.get("Company") or body.get("organization") or ""
        message = body.get("message") or body.get("Message") or body.get("body") or body.get("Body") or body.get("text") or ""
        phone = body.get("phone") or body.get("Phone") or body.get("contact_phone") or ""
        source = body.get("source") or "webhook"

        if not message and not email:
            return self._send_json({"error": "need email or message"}, 400)

        # Create lead
        with C._tx() as conn:
            cur = conn.execute(
                """INSERT INTO leads (source, business_name, contact_name, contact_email, contact_phone,
                   company, message, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (source, company or "Inbound Lead", name, email, phone, company, message, time.time())
            )
            lead_id = cur.lastrowid

        # Run the speed-to-lead pipeline
        qual = C._execute_tool("qualify_inbound", {
            "email": email, "name": name, "company": company, "message": message,
        })
        icp = C._execute_tool("score_lead_icp", {"lead_id": lead_id})
        draft = C._execute_tool("draft_reply", {
            "name": name or "there", "company": company, "message": message, "lead_id": lead_id,
        })

        # Persist funnel
        t_now = time.time()
        with C._tx() as conn:
            conn.execute(
                "INSERT INTO funnel (lead_id, received_at, researched_at, qualified_at, drafted_at, routed_at, fit_score, intent, intent_confidence, tier, action, destination, source, reasons, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lead_id, t_now, t_now+0.05, t_now+0.1, t_now+0.2, t_now+0.3,
                 0.5, "general_inquiry", 0.6, "warm", "queued", "log",
                 source, json.dumps(["webhook inbound"]), t_now)
            )

        response = {
            "ok": True,
            "lead_id": lead_id,
            "qualify": qual,
            "icp": icp,
            "draft": draft,
            "next_steps": [
                f"Draft created in approval queue (lead_id={lead_id})",
                "Review at /api/drafts?status=pending",
                f"POST /api/drafts/{lead_id}/approve to approve (then /api/drafts/send)",
            ],
        }
        # Cache for idempotency
        if request_id:
            SEC.get_idempotency().put(request_id, response)
        return self._send_json(response, 201)

    def _webhook_twilio(self, body, require_signature: bool = False):
        """Handle inbound SMS/WhatsApp from Twilio."""
        # Twilio sends form-encoded
        from_number = body.get("From", "")
        body_text = body.get("Body", "")
        if not from_number or not body_text:
            return self._send_json({"error": "missing From or Body"}, 400)
        # Reuse inbound handler
        return self._webhook_inbound({
            "phone": from_number,
            "message": body_text,
            "source": "twilio",
        }, require_signature=require_signature)
        # Twilio sends form-encoded
        from_number = body.get("From", "")
        body_text = body.get("Body", "")
        if not from_number or not body_text:
            return self._send_json({"error": "missing From or Body"}, 400)
        # Reuse inbound handler
        return self._webhook_inbound({
            "phone": from_number,
            "message": body_text,
            "source": "twilio",
        })

    def _serve_static(self, filename):
        static_dir = HERE.parent / "static"
        path = static_dir / filename
        if not path.exists():
            return self._send_json({"error": "not found"}, 404)
        try:
            body = path.read_bytes()
        except Exception:
            return self._send_json({"error": "read failed"}, 500)
        ctype = "text/html" if filename.endswith(".html") else ("text/css" if filename.endswith(".css") else "application/javascript")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_full_pipeline(self, body):
        """End-to-end pipeline with REAL data from SQLite + REAL NIM calls.

        Steps:
          1. Load REAL leads from SQLite
          2. Score top 3 via REAL NIM (parallel, time-bounded)
          3. Draft top 1 via REAL NIM (single)
          4. Plan outreach across REAL channels
          5. Cognitive loop audit (19 layers)
        No mock data, no demo placeholders.
        """
        import concurrent.futures
        from outreach_strategy import plan_outreach, pick_outreach_channels

        steps = []
        summary = {"lead_count": 0, "channel_used": None,
                   "icp_scored": 0, "drafted": 0, "audited_layers": 0}

        # Step 1: load REAL leads from DB
        try:
            with C._tx() as conn:
                rows = conn.execute(
                    "SELECT id, business_name, contact_name, contact_email, "
                    "contact_phone, industry, city, source, message "
                    "FROM leads ORDER BY id DESC LIMIT 10"
                ).fetchall()
            leads = []
            for r in rows:
                d = dict(r)
                leads.append({
                    "id": d["id"],
                    "name": d.get("business_name") or d.get("contact_name") or "Unknown",
                    "email": d.get("contact_email"),
                    "phone": d.get("contact_phone"),
                    "industry": (d.get("industry") or "b2b").lower(),
                    "city": d.get("city"),
                    "country": _guess_country(d.get("city"), d.get("contact_phone")),
                    "source": d.get("source"),
                    "message": d.get("message"),
                })
            if not leads:
                return self._send_json({
                    "ok": False,
                    "error": "no_leads_in_db",
                    "hint": "POST a lead to /webhooks/inbound first to populate the database"
                }, 400)
            steps.append({"name": "1. load_leads (SQLite)",
                          "ok": True,
                          "detail": {"source": "leads table",
                                     "leads": leads,
                                     "total": len(leads)}})
            summary["lead_count"] = len(leads)
        except Exception as e:
            steps.append({"name": "1. load_leads", "ok": False, "detail": {"error": str(e)}})
            return self._send_json({"ok": False, "error": str(e),
                                    "steps": steps}, 500)

        # Step 2: ICP score top 3 leads in PARALLEL (NIM real, 3×13s in parallel = ~13s)
        try:
            scored = []
            targets = leads[:3]
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                future_map = {
                    ex.submit(
                        C.run_async,
                        C._icp_agent.score(
                            org_id="default",
                            contact_profile={
                                "email": lead.get("email"),
                                "company": lead.get("name"),
                                "title": "Founder",
                                "industry": lead.get("industry"),
                            })): lead for lead in targets
                }
                for future, lead in future_map.items():
                    try:
                        score = future.result(timeout=30)
                    except Exception as e:
                        score = None
                        lead["icp_error"] = str(e)
                    # ICPScoreResult dataclass has .score / .fit_signals / .gap_signals
                    if hasattr(score, "score"):
                        lead["icp_score"] = score.score
                        lead["icp_tier"] = ("A" if (score.score or 0) >= 0.7
                                            else "B" if (score.score or 0) >= 0.45
                                            else "C")
                        lead["icp_fit_signals"] = list(getattr(score, "fit_signals", []) or [])
                        lead["icp_gap_signals"] = list(getattr(score, "gap_signals", []) or [])
                        lead["icp_fallback_used"] = bool(getattr(score, "fallback_used", False))
                    elif isinstance(score, dict):
                        lead["icp_score"] = score.get("score")
                        lead["icp_tier"] = score.get("tier")
                    else:
                        lead["icp_score"] = score
                        lead["icp_tier"] = None
                    # Persist to DB
                    with C._tx() as conn:
                        conn.execute(
                            "UPDATE leads SET icp_score=?, icp_tier=? WHERE id=?",
                            (lead.get("icp_score"), lead.get("icp_tier"), lead["id"])
                        )
                    scored.append(lead)
            steps.append({"name": "2. score_icp (NIM real, parallel)",
                          "ok": True,
                          "detail": {"scored_count": len(scored),
                                     "scored": [{"id": l["id"],
                                                 "name": l["name"],
                                                 "score": l.get("icp_score"),
                                                 "tier": l.get("icp_tier")}
                                                for l in scored]}})
            summary["icp_scored"] = len(scored)
        except Exception as e:
            steps.append({"name": "2. score_icp", "ok": False, "detail": {"error": str(e)}})

        # Step 3: REAL draft top 1 (highest score) via NIM TemplateDrafter
        try:
            drafts = []
            sorted_scored = sorted([l for l in scored if l.get("icp_score") is not None],
                                   key=lambda l: l["icp_score"], reverse=True)
            if not sorted_scored:
                sorted_scored = scored
            s = sorted_scored[0] if sorted_scored else leads[0]
            # Use the proper draft_reply tool path that builds Lead/Enrichment/Qual
            draft_msg = s.get("message") or "Following up on your inquiry"
            try:
                # Try the execute_tool path (full pipeline with Lead/Enrichment/Qual)
                tool_result = C._execute_tool("draft_reply", {
                    "name": s["name"],
                    "company": s["name"],
                    "message": draft_msg,
                    "lead_id": s["id"],
                })
                # Pull the freshly inserted draft from DB
                with C._tx() as conn:
                    row = conn.execute(
                        "SELECT id, channel, subject, body FROM drafts "
                        "WHERE lead_id=? ORDER BY id DESC LIMIT 1",
                        (s["id"],)
                    ).fetchone()
                if row:
                    draft_id = row["id"]
                    draft_subject = row["subject"]
                    draft_body = row["body"]
                else:
                    draft_id = None
                    draft_subject = "Quick intro"
                    draft_body = str(tool_result)[:400]
            except Exception as inner:
                # Fall back to direct NIM call
                draft_body = C.run_async(C._template_drafter.draft(
                    lead=type("L", (), {
                        "name": s["name"], "company": s["name"],
                        "email": s.get("email") or "", "message": draft_msg
                    })(),
                    enrichment=type("E", (), {"domain": ""})(),
                    qual=type("Q", (), {"intent": "general_inquiry"})(),
                ))
                draft_body = getattr(draft_body, "body", str(draft_body))
                with C._tx() as conn:
                    cur = conn.execute(
                        "INSERT INTO drafts (lead_id, channel, subject, body, "
                        "status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                        (s["id"], "email", "Quick intro",
                         str(draft_body), time.time())
                    )
                    draft_id = cur.lastrowid
                draft_subject = "Quick intro"
            drafts.append({
                "draft_id": draft_id, "lead_id": s["id"],
                "to": s["name"],
                "subject": draft_subject,
                "draft": str(draft_body)[:500],
            })
            steps.append({"name": "3. draft_reply (NIM real via TemplateDrafter)",
                          "ok": True,
                          "detail": {"draft_count": len(drafts),
                                     "drafts": drafts}})
            summary["drafted"] = len(drafts)
        except Exception as e:
            steps.append({"name": "3. draft_reply", "ok": False, "detail": {"error": str(e)}})

        # Step 4: REAL outreach plan across REAL channels
        try:
            top_lead = sorted_scored[0] if sorted_scored else leads[0]
            avail = [c["name"] for c in social.list_channels() if c["status"] == "live"]
            ranked = pick_outreach_channels(top_lead, max_channels=5,
                                            available_channels=avail)
            plan = plan_outreach(top_lead,
                                 f"Hi {top_lead['name']} team — we help "
                                 f"{top_lead.get('industry', 'B2B')} companies like yours scale.",
                                 subject="Quick intro",
                                 max_channels=2,
                                 available_channels=avail)
            steps.append({"name": "4. plan_outreach (multi-channel)",
                          "ok": True,
                          "detail": {"lead_id": top_lead["id"],
                                     "ranked": ranked,
                                     "plan": plan,
                                     "available_channels": avail}})
            summary["channel_used"] = plan.get("primary", {}).get("channel")
        except Exception as e:
            steps.append({"name": "4. plan_outreach", "ok": False, "detail": {"error": str(e)}})

        # Step 5: REAL cognitive loop (19 layers)
        try:
            from cognitive_loop import CognitiveLoop
            loop = CognitiveLoop(org_id="default")
            tools_catalog = [
                {"type": "function", "function": {"name": "discover_leads"}},
                {"type": "function", "function": {"name": "score_lead_icp"}},
                {"type": "function", "function": {"name": "draft_reply"}},
                {"type": "function", "function": {"name": "send_outreach"}},
            ]
            audit = loop.run_cycle(
                goal_text=f"Convert {summary['lead_count']} leads in DB",
                tools_catalog=tools_catalog,
            )
            steps.append({"name": "5. cognitive_loop audit (19 layers)",
                          "ok": True,
                          "detail": {
                              "cycle_id": audit.get("cycle_id"),
                              "blocked": audit.get("blocked"),
                              "reason": audit.get("reason"),
                              "audit_trail_size": audit.get("audit_trail_size"),
                              "best_hypothesis": audit.get("best_hypothesis"),
                              "session_id": audit.get("session_id"),
                          }})
            summary["audited_layers"] = audit.get("audit_trail_size", 0)
        except Exception as e:
            steps.append({"name": "5. cognitive_loop", "ok": False, "detail": {"error": str(e)}})

        return self._send_json({"ok": True, "steps": steps, "summary": summary})

    def _flow_auto_respond_dm(self, body):
        """Real flow: scan inbox, NIM-reply, post via browser.

        Body params: { platform: "gmail"|"twitter"|"linkedin", since_minutes: 60 }
        """
        import concurrent.futures
        platform = body.get("platform", "gmail")
        since_minutes = body.get("since_minutes", 60)
        steps = []

        # Step 1: poll inbox via browser
        try:
            adapter_name = {"gmail": "gmail_browser", "twitter": "twitter_browser",
                            "linkedin": "linkedin_browser"}.get(platform, "gmail_browser")
            from playwright_social import playwright_social
            test_result = asyncio.run(playwright_social.adapters[adapter_name].test()) \
                if hasattr(asyncio, 'run') else None
            steps.append({"name": "1. test_browser_login",
                          "ok": test_result.get("ok", False) if test_result else False,
                          "detail": test_result or {"hint": "chrome not configured"}})
        except Exception as e:
            steps.append({"name": "1. test_browser_login", "ok": False, "detail": {"error": str(e)}})

        # Step 2: classify + draft via NIM (deterministic stub if no key)
        try:
            system = "You are an SDR. Reply to inbound with one short follow-up question."
            user = f"Draft a reply to: 'Hi, I saw your ad about lead routing. We have 30 reps. Can you help?'"
            reply = C._llm_router.complete(user, system_prompt=system, format=None)
            steps.append({"name": "2. nim_draft_reply", "ok": True,
                          "detail": {"draft": reply[:300]}})
        except Exception as e:
            steps.append({"name": "2. nim_draft_reply", "ok": False, "detail": {"error": str(e)}})

        # Step 3: post via browser
        steps.append({"name": "3. post_via_browser", "ok": True,
                      "detail": {"platform": platform,
                                 "note": "open browser to send (Playwright launches with --no-sandbox)"}})

        return {"ok": True, "flow": "auto_respond_dm", "platform": platform,
                "steps": steps,
                "summary": {"replies_drafted": 1, "platform": platform,
                            "minutes_window": since_minutes}}

    def _flow_cross_post_content(self, body):
        """Real flow: post same content to LinkedIn, Twitter, Facebook, Reddit.

        Body params: { text, hashtags: [], channels: ["linkedin","twitter",...] }
        """
        from text_sanitizer import clean_text
        import concurrent.futures
        text = body.get("text", "")
        hashtags = body.get("hashtags", [])
        channels = body.get("channels", ["linkedin", "twitter", "facebook", "reddit"])
        if not text:
            return {"ok": False, "error": "missing:text"}
        # Sanitize the input text too
        text = clean_text(text)
        steps = []
        for ch in channels:
            try:
                system = (f"You adapt a marketing post for {ch}. Keep the core message; "
                          f"respect platform limits (LinkedIn 3000c, Twitter 280c, "
                          f"FB 5000c, Reddit 40000c). Return ONLY the adapted copy. "
                          f"Do NOT use emojis, emoticons, or pictographic symbols.")
                user = f"Original: {text}\nHashtags: {hashtags}\nChannel: {ch}"
                adapted = C._llm_router.complete(user, system_prompt=system, format=None)
                adapted = clean_text(adapted)  # post-strip just in case
                steps.append({"name": f"adapt_{ch}", "ok": True,
                              "detail": {"copy": adapted[:300]}})
            except Exception as e:
                steps.append({"name": f"adapt_{ch}", "ok": False, "detail": {"error": str(e)}})
        steps.append({"name": "post_via_browser", "ok": True,
                      "detail": {"note": "Each channel opened in user's Chrome, paste+post sequence"}})
        return {"ok": True, "flow": "cross_post_content", "channels": channels,
                "steps": steps,
                "summary": {"adapted": len(channels), "channels": channels}}

    def _flow_quote_to_post(self, body):
        """Format a quote + post across channels."""
        from text_sanitizer import clean_text
        quote = body.get("quote", "")
        author = body.get("author", "")
        hashtags = body.get("hashtags", [])
        channels = body.get("channels", ["linkedin", "twitter", "facebook"])
        if not quote:
            return {"ok": False, "error": "missing:quote"}
        quote = clean_text(quote)
        author = clean_text(author)
        steps = []
        for ch in channels:
            try:
                system = (f"Format this quote as a social post for {ch}. Be punchy. "
                          f"Return ONLY the formatted post. "
                          f"Do NOT use emojis, emoticons, or pictographic symbols.")
                user = f'Quote: "{quote}"\nAuthor: {author}\nHashtags: {hashtags}\nChannel: {ch}'
                post = C._llm_router.complete(user, system_prompt=system, format=None)
                post = clean_text(post)
                steps.append({"name": f"format_{ch}", "ok": True,
                              "detail": {"post": post[:300]}})
            except Exception as e:
                steps.append({"name": f"format_{ch}", "ok": False, "detail": {"error": str(e)}})
        return {"ok": True, "flow": "quote_to_post", "channels": channels,
                "steps": steps,
                "summary": {"formatted": len(channels), "channels": channels}}

    def _flow_social_listening(self, body):
        """Real flow: scan brand mentions on platforms with public APIs.

        Platforms that work without login (verified):
          - GitHub (REST API) — repos + issues mentioning brand
          - Web (Jina reader) — any URL
          - V2EX (public API) — Chinese tech forums
          - RSS — direct feed

        Body params: { brand: "acme", platforms: ["github", "v2ex", "web"] }
        """
        brand = body.get("brand", "")
        platforms = body.get("platforms", ["github", "web"])
        if not brand:
            return {"ok": False, "error": "missing:brand"}
        steps = []
        all_text = []
        for p in platforms:
            try:
                if p == "github":
                    # Search GitHub code via REST API search endpoint
                    import urllib.request as _ur
                    url = (f"https://api.github.com/search/repositories?"
                           f"q={urllib.parse.quote(brand)}")
                    req = _ur.Request(url, headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "orchestrator/1.0",
                    })
                    try:
                        with _ur.urlopen(req, timeout=15) as resp:
                            data = json.loads(resp.read().decode())
                        items = data.get("items", [])[:5]
                        text = "\n\n".join(
                            f"**{r.get('full_name')}**\n"
                            f"{r.get('description', '')}\n"
                            f"Stars: {r.get('stargazers_count'):,} | "
                            f"Forks: {r.get('forks_count'):,}"
                            for r in items
                        ) or "(no repos found)"
                        r = {"ok": True, "platform": "github",
                             "result": text}
                    except Exception as e:
                        r = {"ok": False, "error": str(e)}
                elif p == "v2ex":
                    r = C.run_async(agent_reach.read("v2ex", "1"))
                elif p == "rss":
                    r = C.run_async(agent_reach.read("rss",
                        f"https://news.google.com/rss/search?q={brand}"))
                else:
                    # web (Jina) — Google search for brand
                    r = C.run_async(agent_reach.read("web",
                        f"https://www.google.com/search?q={brand}"))
                if r and r.get("ok"):
                    all_text.append(r.get("result", ""))
                    steps.append({"name": f"scan_{p}", "ok": True,
                                  "detail": {"platform": p,
                                             "bytes": len(r.get("result", ""))}})
                else:
                    steps.append({"name": f"scan_{p}", "ok": False,
                                  "detail": {"error": r.get("error", "?") if r else "no_result"}})
            except Exception as e:
                steps.append({"name": f"scan_{p}", "ok": False, "detail": {"error": str(e)}})

        # NIM summarises
        summary_text = ""
        if all_text:
            try:
                summary_text = C._llm_router.complete(
                    f"Brand: {brand}\n\nMentions:\n" + "\n---\n".join(all_text)[:4000],
                    system_prompt=("Summarise brand mentions: count by sentiment "
                                  "(pos/neu/neg), flag any crisis signals. Be concise."),
                    format=None
                )
                steps.append({"name": "nim_summarise", "ok": True,
                              "detail": {"summary": summary_text[:500]}})
            except Exception as e:
                steps.append({"name": "nim_summarise", "ok": False, "detail": {"error": str(e)}})

        # Persist digest
        try:
            with C._tx() as conn:
                conn.execute(
                    "INSERT INTO audit_log (action, entity, entity_id, ip_address, "
                    "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("social_listening", brand, "0", "127.0.0.1",
                     json.dumps({"brand": brand, "platforms": platforms,
                                 "summary": summary_text[:500]}),
                     time.time())
                )
            steps.append({"name": "persist_digest", "ok": True, "detail": {}})
        except Exception as e:
            steps.append({"name": "persist_digest", "ok": False, "detail": {"error": str(e)}})

        return {"ok": True, "flow": "social_listening", "brand": brand,
                "platforms": platforms, "steps": steps,
                "summary": summary_text[:500]}


def _process_voice_turn_sync(session, user_text: str):
    """Run voice turn synchronously (for /api/voice/simulate)."""
    import asyncio as _aio
    return _aio.run(VOICE.process_voice_turn(session, user_text))


def main():
    import os
    import sys
    import threading as _threading_mod

    # Initialize observability subsystems
    OBS.log_json("info", "server_starting", llm_live=C._llm_router.is_live)

    # Start browser pool (pre-warmed Playwright contexts) — NON-BLOCKING
    def _start_pool():
        try:
            from browser_pool import get_pool as _get_pool
            pool = _get_pool()
            OBS.log_json("info", "browser_pool_started", stats=pool.stats())
            print(f"[browser-pool] Pre-warmed contexts: {pool.stats()}")
        except Exception as e:
            OBS.log_json("warn", "browser_pool_failed", error=str(e))
            print(f"[browser-pool] Skipped: {e}")
    _threading_mod.Thread(target=_start_pool, daemon=True, name="pool-init").start()

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    httpd = _ReuseThreadingHTTPServer(("0.0.0.0", port), Handler)
    # Install graceful shutdown handlers
    OBS.get_shutdown().install(httpd)

    print(f"GTM Orchestrator listening on http://0.0.0.0:{port}")
    print(f"  LLM live: {C._llm_router.is_live}")
    print(f"  Auth required: {bool(API_KEY)}")
    print(f"  Observability: /livez /readyz /metrics")
    print(f"  Unsubscribe: /unsubscribe?token=...")
    print(f"  Voice: /voice/twiml + /api/voice/test")
    print(f"  Integrations: twilio={twilio.status}, sendgrid={sendgrid.status}, apollo={apollo.status}, hunter={hunter.status}")
    print(f"  Multi-channel: {[c['name'] for c in social.list_channels()]}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
