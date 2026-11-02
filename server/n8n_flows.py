"""
N8N-style automation flow patterns — adapted for browser-based automation.

These are the canonical flows that n8n users build to replace API integrations
with browser automation. We expose them as orchestrator endpoints so the user
can trigger any flow with one click.

Flows:

  1. Auto-respond to inbound DMs
     - Trigger: Gmail/Twitter/LinkedIn message received
     - Action: NIM classifies intent → reply drafted → posted via browser

  2. Lead capture from social → CRM
     - Trigger: Twitter mention / LinkedIn comment / Instagram DM
     - Action: extract contact info → add to HubSpot/Salesforce via browser

  3. Cross-post content
     - Trigger: new blog post / case study
     - Action: format per channel → post to LinkedIn, Twitter, FB, Reddit

  4. Auto-engage with target accounts
     - Trigger: daily cron
     - Action: for each target, like/retweet/comment their last post

  5. Inbound lead → multi-channel outbound
     - Trigger: webhook /webhooks/inbound
     - Action: qualify → score → draft → send via best channel

  6. CRM hygiene
     - Trigger: cron weekly
     - Action: scan Gmail for unanswered leads → log to CRM

  7. Social listening
     - Trigger: cron hourly
     - Action: search brand mentions → NIM summarises → alerts

  8. Quote-to-post
     - Trigger: user submits a quote
     - Action: format per channel → schedule posts via buffer-style browser
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from observability import log_json as _log


# Flow catalog — exposed via /api/flows/list and /api/flows/run/{name}
FLOWS = {
    "auto_respond_dm": {
        "name": "auto_respond_dm",
        "description": "Auto-reply to inbound DMs across Gmail/Twitter/LinkedIn/Instagram",
        "trigger": "new_message",
        "platforms": ["gmail_browser", "linkedin_browser", "twitter_browser"],
        "requires": ["logged_in_browser", "nim_key"],
        "average_runtime_seconds": 8,
        "steps": [
            "1. Poll inbox (Gmail/LinkedIn/Twitter/Instagram)",
            "2. For each unread message: NIM classifies intent + sentiment",
            "3. Draft a contextual reply (NIM)",
            "4. Post reply via browser (click Send button)",
            "5. Log to audit table",
        ],
    },
    "lead_capture_to_crm": {
        "name": "lead_capture_to_crm",
        "description": "Extract contact info from social mentions, push to HubSpot/Salesforce",
        "trigger": "social_mention",
        "platforms": ["twitter_browser", "linkedin_browser", "instagram_browser"],
        "requires": ["logged_in_browser", "nim_key", "crm_logged_in"],
        "average_runtime_seconds": 12,
        "steps": [
            "1. Search @mentions / DMs for 'pricing', 'demo', 'contact'",
            "2. NIM extracts {name, email, phone, company} from each match",
            "3. Open HubSpot Contacts → New → fill form → Save (browser)",
            "4. Log to leads table",
        ],
    },
    "cross_post_content": {
        "name": "cross_post_content",
        "description": "Post same content to LinkedIn, Twitter, FB, Reddit with per-platform formatting",
        "trigger": "new_content",
        "platforms": ["linkedin_browser", "twitter_browser", "facebook_browser", "reddit_browser"],
        "requires": ["logged_in_browser"],
        "average_runtime_seconds": 20,
        "steps": [
            "1. NIM adapts copy per channel (LinkedIn <3000c, Twitter <280c, etc.)",
            "2. Open LinkedIn → click Start a post → paste → Post",
            "3. Open Twitter → click Post → paste → Post",
            "4. Open Facebook → Create Post → paste → Post",
            "5. Open Reddit → submit to relevant subreddit → Post",
            "6. Return URLs of all posts",
        ],
    },
    "auto_engage_targets": {
        "name": "auto_engage_targets",
        "description": "Daily like/retweet/comment on posts from target accounts",
        "trigger": "cron:daily",
        "platforms": ["twitter_browser", "linkedin_browser"],
        "requires": ["logged_in_browser", "target_accounts_list"],
        "average_runtime_seconds": 60,
        "steps": [
            "1. Load target_accounts from memory table",
            "2. For each target: open profile, scan last 5 posts",
            "3. NIM picks 1 post to engage with (highest relevance)",
            "4. Like + retweet/comment via browser",
            "5. Log engagement to memory table",
        ],
    },
    "inbound_to_outbound": {
        "name": "inbound_to_outbound",
        "description": "Webhook inbound → qualify → score → draft → multi-channel send",
        "trigger": "webhook:/webhooks/inbound",
        "platforms": ["speed_to_lead"],
        "requires": ["nim_key"],
        "average_runtime_seconds": 5,
        "steps": [
            "1. POST /webhooks/inbound (HMAC-verified)",
            "2. qualify_inbound (RuleQualifier)",
            "3. score_lead_icp (NIM)",
            "4. draft_reply (NIM)",
            "5. plan_outreach (geo+industry scoring)",
            "6. social.auto_send or browser.playwright.send",
            "7. Log to audit table",
        ],
    },
    "crm_hygiene": {
        "name": "crm_hygiene",
        "description": "Weekly: find unanswered inbound leads, log to CRM",
        "trigger": "cron:weekly",
        "platforms": ["gmail_browser", "hubspot_browser"],
        "requires": ["logged_in_browser"],
        "average_runtime_seconds": 30,
        "steps": [
            "1. Open Gmail → search 'is:unread newer_than:7d'",
            "2. For each match: NIM checks if it's a sales lead",
            "3. Open HubSpot → search contact → if missing, create",
            "4. Add note 'Unanswered email from {date}'",
        ],
    },
    "social_listening": {
        "name": "social_listening",
        "description": "Hourly brand mention scan + NIM summary + alerts",
        "trigger": "cron:hourly",
        "platforms": ["twitter_browser", "linkedin_browser", "reddit_browser"],
        "requires": ["logged_in_browser", "nim_key"],
        "average_runtime_seconds": 45,
        "steps": [
            "1. Search brand keywords on Twitter/LinkedIn/Reddit",
            "2. NIM summarises sentiment + key themes",
            "3. If urgent (crude, intent), send Telegram alert",
            "4. Save digest to memory table",
        ],
    },
    "quote_to_post": {
        "name": "quote_to_post",
        "description": "Format a quote as social post across channels",
        "trigger": "user_submit",
        "platforms": ["linkedin_browser", "twitter_browser", "facebook_browser"],
        "requires": ["logged_in_browser"],
        "average_runtime_seconds": 25,
        "steps": [
            "1. User POSTs {quote, author, hashtags}",
            "2. NIM formats per channel",
            "3. Post to all 3 channels via browser",
        ],
    },
}


def list_flows() -> list[dict]:
    """Return all flows with metadata."""
    return list(FLOWS.values())


def get_flow(name: str) -> Optional[dict]:
    return FLOWS.get(name)


def run_flow(name: str, params: dict | None = None) -> dict:
    """Execute a flow by name.

    This is a stub — actual execution is wired through specific endpoints
    that combine browser_social + playwright_social + social_adapters + LLM.
    """
    flow = FLOWS.get(name)
    if not flow:
        return {"ok": False, "error": f"unknown_flow:{name}",
                "available": list(FLOWS.keys())}
    _log("info", "flow_started", flow=name, params=params or {})
    # Real flows are exposed via dedicated endpoints:
    return {
        "ok": True,
        "flow": name,
        "hint": f"Use POST /api/flows/{name} with flow params",
        "steps": flow["steps"],
        "platforms": flow["platforms"],
    }