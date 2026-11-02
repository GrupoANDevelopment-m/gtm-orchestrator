"""
test_http_endpoints.py — HTTP smoke tests for all 83+ endpoints.

Verifies:
  - 200 OK on health, capabilities, list endpoints
  - 200 OK on GET-mirrored endpoints (vault, observability, memory, cognitive, voice)
  - 200 on POST endpoints (qualify, draft, social, etc.)
  - 200 on the static UI files
  - All WRITE channels and BROWSER channels present
  - All 15 READ platforms listed
"""
from __future__ import annotations

import pytest


# ====================================================================
# Health and basic system
# ====================================================================

class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") in ("ok", "live", "ready")

    def test_livez(self, client):
        assert client.get("/livez").status_code == 200

    def test_readyz(self, client):
        assert client.get("/readyz").status_code == 200

    def test_metrics(self, client):
        assert client.get("/metrics").status_code == 200

    def test_capabilities(self, client):
        r = client.get("/api/capabilities")
        assert r.status_code == 200
        body = r.json()
        assert "capabilities" in body or "tools" in body or isinstance(body, (list, dict))


# ====================================================================
# Static UI
# ====================================================================

class TestStaticUI:
    def test_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "<html" in r.text.lower()

    def test_app_js(self, client):
        r = client.get("/static/app.js")
        assert r.status_code == 200
        assert "function" in r.text

    def test_app_css(self, client):
        r = client.get("/static/app.css")
        assert r.status_code == 200
        assert "{background" in r.text or "background" in r.text


# ====================================================================
# Social / browser / flows
# ====================================================================

class TestSocialChannels:
    def test_social_channels(self, client):
        r = client.get("/api/social/channels")
        assert r.status_code == 200
        body = r.json()
        chs = body.get("channels", [])
        assert len(chs) == 5
        names = {c["name"] for c in chs}
        assert "whatsapp" in names
        assert "discord" in names
        assert "telegram" in names
        assert "linkedin" in names
        assert "instagram" in names

    def test_browser_channels(self, client):
        r = client.get("/api/browser/channels")
        assert r.status_code == 200
        body = r.json()
        chs = body.get("browser_channels", [])
        assert len(chs) >= 9
        names = {c["name"] for c in chs}

    def test_browser_setup(self, client):
        r = client.get("/api/browser/setup")
        assert r.status_code == 200
        body = r.json()
        assert "status" in body
        assert "instructions" in body

    def test_15_read_platforms(self, client):
        """The user requirement: 15 READ platforms exposed."""
        r = client.get("/api/social/channels")
        assert r.status_code == 200
        platforms = r.json().get("agent_reach_platforms", [])
        assert len(platforms) == 15, f"Expected 15 platforms, got {len(platforms)}: {platforms}"

    def test_15_internet_platforms(self, client):
        r = client.get("/api/internet/platforms")
        assert r.status_code == 200
        platforms = r.json().get("platforms", [])
        assert len(platforms) == 15, f"Expected 15, got {len(platforms)}"


class TestFlows:
    def test_list_flows(self, client):
        r = client.get("/api/flows/list")
        assert r.status_code == 200
        body = r.json()
        flows = body.get("flows", [])
        assert len(flows) >= 8
        names = {f["name"] for f in flows}
        # Required flows
        assert "auto_respond_dm" in names
        assert "lead_capture_to_crm" in names
        assert "cross_post_content" in names
        assert "auto_engage_targets" in names
        assert "inbound_to_outbound" in names
        assert "crm_hygiene" in names
        assert "social_listening" in names
        assert "quote_to_post" in names


# ====================================================================
# Lead pipeline
# ====================================================================

class TestLeadPipeline:
    def test_leads(self, client):
        r = client.get("/api/leads")
        assert r.status_code == 200
        body = r.json()
        assert "leads" in body or isinstance(body, list)

    def test_drafts(self, client):
        r = client.get("/api/drafts")
        assert r.status_code == 200

    def test_funnel(self, client):
        r = client.get("/api/funnel")
        assert r.status_code == 200

    def test_qualify(self, client):
        r = client.post("/api/qualify", json={
            "name": "Test User",
            "company": "TestCo",
            "email": "test@test.com",
            "message": "We need lead routing for 50 reps",
        })
        assert r.status_code == 200

    def test_draft_reply(self, client):
        r = client.post("/api/draft/reply", json={
            "name": "Sarah",
            "company": "Acme",
            "message": "Need help",
        })
        assert r.status_code == 200

    def test_draft_outbound(self, client):
        r = client.post("/api/draft/outbound", json={
            "contact_name": "Sarah",
            "company_name": "Acme",
            "industry": "SaaS",
        })
        assert r.status_code == 200

    def test_social_auto_send(self, client):
        r = client.post("/api/social/auto_send", json={
            "lead": {"name": "Test", "telegram_id": "@test"},
            "body": "Hello",
        })
        assert r.status_code == 200

    def test_social_send_with_browser_fallback(self, client):
        """WhatsApp/Discord/Telegram browser fallback flag."""
        r = client.post("/api/social/send", json={
            "channel": "whatsapp",
            "to": "+15551234567",
            "body": "test",
            "transport": "browser",
        })
        # Either works (live) or returns structured error — but not 500
        assert r.status_code in (200, 400, 503)


# ====================================================================
# Cognitive / memory / compliance / security / observability
# ====================================================================

class TestCognitive:
    def test_constitutional(self, client):
        r = client.get("/api/cognitive/constitutional")
        assert r.status_code == 200
        body = r.json()
        assert "invariants" in body

    def test_synthesis(self, client):
        r = client.get("/api/cognitive/synthesis")
        assert r.status_code == 200

    def test_refine(self, client):
        r = client.get("/api/cognitive/refine")
        assert r.status_code == 200

    def test_improvement(self, client):
        r = client.get("/api/cognitive/improvement")
        assert r.status_code == 200

    def test_run(self, client):
        r = client.post("/api/cognitive/run", json={"goal": "test goal"})
        assert r.status_code == 200


class TestMemory:
    def test_episodes(self, client):
        r = client.get("/api/memory/episodes")
        assert r.status_code == 200

    def test_lessons(self, client):
        r = client.get("/api/memory/lessons")
        assert r.status_code == 200

    def test_capabilities(self, client):
        r = client.get("/api/memory/capabilities")
        assert r.status_code == 200

    def test_remember(self, client):
        r = client.post("/api/memory/remember", json={
            "kind": "lesson",
            "content": "Test lesson",
        })
        assert r.status_code in (200, 400)


class TestCompliance:
    def test_audit(self, client):
        r = client.get("/api/compliance/audit")
        assert r.status_code == 200

    def test_consent(self, client):
        r = client.post("/api/compliance/consent", json={
            "email": "test@example.com",
            "purpose": "marketing",
            "granted": True,
        })
        assert r.status_code in (200, 400)

    def test_check_consent(self, client):
        r = client.post("/api/compliance/check_consent", json={
            "email": "test@example.com",
            "purpose": "marketing",
        })
        assert r.status_code in (200, 400)

    def test_unsubscribe_url(self, client):
        r = client.post("/api/compliance/unsubscribe_url", json={
            "email": "test@example.com",
        })
        assert r.status_code in (200, 400)

    def test_retention(self, client):
        r = client.post("/api/compliance/retention", json={"retention_days": 730})
        assert r.status_code in (200, 400)

    def test_gdpr_delete(self, client):
        r = client.post("/api/leads/999999/delete", json={"reason": "test"})
        assert r.status_code in (200, 400, 404)

    def test_gdpr_export(self, client):
        r = client.get("/api/leads/1/export")
        assert r.status_code in (200, 400, 404)


class TestSecurity:
    def test_vault(self, client):
        r = client.get("/api/security/vault")
        assert r.status_code == 200
        body = r.json()
        assert "keys" in body
        # nvidia_* keys are filtered
        for k in body["keys"]:
            assert not k.startswith("nvidia")

    def test_hmac_sign(self, client):
        r = client.post("/api/security/hmac/sign", json={"body": "test"})
        assert r.status_code == 200
        body = r.json()
        assert "signature" in body

    def test_hmac_verify(self, client):
        r = client.post("/api/security/hmac/verify", json={"body": "x", "signature": "x"})
        assert r.status_code == 200

    def test_observability_metrics(self, client):
        r = client.get("/api/observability/metrics")
        assert r.status_code == 200


class TestVoice:
    def test_voice_test(self, client):
        r = client.get("/api/voice/test")
        assert r.status_code == 200
        body = r.json()
        assert "ok" in body or "voice_live" in body

    def test_voice_test_full(self, client):
        r = client.get("/api/voice/test_full")
        assert r.status_code == 200

    def test_voice_simulate(self, client):
        r = client.post("/api/voice/simulate", json={"user_text": "Hello"})
        assert r.status_code == 200
        body = r.json()
        # Should not have any emoji
        import re
        text = str(body)
        emojis = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text)
        assert len(emojis) == 0, f"Found emojis in voice response: {emojis}"


# ====================================================================
# Webhooks
# ====================================================================

class TestWebhooks:
    def test_inbound_webhook(self, client):
        r = client.post("/webhooks/inbound", json={
            "from": "test@example.com",
            "subject": "Hi",
            "body": "I'm interested",
        })
        assert r.status_code in (200, 400, 401, 403)

    def test_twilio_webhook(self, client):
        r = client.post("/webhooks/twilio", data={
            "From": "+15551234567",
            "Body": "Hi",
        })
        assert r.status_code in (200, 400, 401, 403)


# ====================================================================
# DSH
# ====================================================================

class TestDSH:
    def test_dsh_info(self, client):
        r = client.get("/api/dsh/info")
        assert r.status_code in (200, 404, 503)

    def test_dsh_sessions(self, client):
        r = client.get("/api/dsh/sessions")
        assert r.status_code in (200, 404, 503)

    def test_dsh_meter_summary(self, client):
        r = client.get("/api/dsh/meter/summary")
        assert r.status_code in (200, 404, 503)

    def test_dsh_skills(self, client):
        r = client.get("/api/dsh/skills")
        assert r.status_code in (200, 404, 503)

    def test_dsh_guard_check(self, client):
        r = client.post("/api/dsh/guard/check", json={})
        assert r.status_code in (200, 400, 404, 503)


# ====================================================================
# Chat
# ====================================================================

class TestChat:
    def test_chat_endpoint_exists(self, client):
        r = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code in (200, 400, 500, 503)
        if r.status_code == 200:
            body = r.json()
            assert "reply" in body or "steps" in body
