# GTM Orchestrator v12

A fused AI agent for GTM/CRM that imports **real production code** from 10 vendored
open-source repos and wires them together with a live NVIDIA NIM LLM, a 19-layer
cognitive loop, multi-channel social automation, and GDPR/LGPD compliance.

**No mocks. No stubs. No demo mode. No placeholder.**

---

## Quick start

```bash
git clone https://github.com/GrupoANDevelopment-m/gtm-orchestrator.git
cd gtm-orchestrator

pip install -r requirements.txt
make server          # boots on :8002 (override with PORT=9000 make server)

# Auth header
KEY=$(cat keys/.orchestrator_api_key.txt)
curl http://127.0.0.1:8002/health -H "Authorization: Bearer $KEY"
```

Open <http://127.0.0.1:8002> for the full UI (30+ tabs, every endpoint exposed).

### Secrets

API keys live in `keys/` as `chmod 600` files and are **git-ignored**:

| File | Purpose |
|---|---|
| `keys/.nvidia_main.txt` | NVIDIA NIM key (nemotron-3.5-lightning-30b-a3b) |
| `keys/.nvidia_voice.txt` | NVIDIA NIM voice-model key |
| `keys/.nvidia_m2.txt` / `.nvidia_step.txt` | Alternative models |
| `keys/.orchestrator_api_key.txt` | Bearer token for the HTTP API |
| `keys/.telegram_bot_token.txt` | Telegram Bot API token |
| `keys/.webhook_signing_key.txt` | HMAC webhook signing secret |
| `keys/.twilio_*.txt`, `.sendgrid_*.txt`, `.apollo_*.txt`, `.hunter_*.txt` | Outbound integrations |

---

## What it does end-to-end

```
Google Maps prospect (Playwright + headless Chrome)
  ↓
Real businesses: name, address, phone, rating, website
  ↓
Persist to SQLite (leads table with icp_score / icp_tier)
  ↓
Score ICP      → gtm-engine ICPAgent (NVIDIA NIM)
  ↓
Qualify inbound → speed-to-lead RuleQualifier
  ↓
Draft reply    → speed-to-lead TemplateDrafter / LlmDrafter (NVIDIA NIM)
  ↓
Draft outbound → gtm-engine OutboundAgent (3 variations, NVIDIA NIM)
  ↓
Pick channel   → outreach_strategy (geo × industry affinity scoring)
  ↓
Send           → social_adapters (5 API) or playwright_social (9 browser)
  ↓
Audit + persist → audit_log table (SOC2 / GDPR Art. 30)
```

---

## Architecture

### Fused upstream repos (`upstream/`)

| Repo | What we use |
|---|---|
| **gtm-engine** | ICPAgent, OutboundAgent, DealIntelAgent, RetentionAgent, ContentAgent (real production Python) |
| **speed-to-lead** | RuleQualifier, TemplateDrafter, LlmDrafter, normalize, ICP similarity |
| **prospex** | `LeadIntelligenceService` — Bayesian scoring + city priors (ported from the TypeScript original) |
| **crm-release** | CRM schema + tRPC contract reference (`crm_adapter.py`) |
| **agent-reach** | 15 platform readers (Twitter, Reddit, GitHub, YouTube, Bilibili, RSS, …) |
| **browser-use** | LLM-driven browser agent (fallback) |
| **browser-harness** | CDP direct browser control |
| **playwright-skill** | Playwright CLI driver |
| **browser-skill** | Rust-based browser bridge |
| **dsh-skills** | DeepSeek Harness skill packs |

### Modules (`server/`, 33 files)

| Module | Role |
|---|---|
| `api.py` | 83+ HTTP endpoints, auth, HMAC, idempotency, all UI tab handlers |
| `core.py` | Wires the real upstream agents; SQLite schema + migrations |
| `cognitive_loop.py` | 19-layer ODC/ACAMR loop + 10 constitutional invariants |
| `llm_adapter.py` | NVIDIA NIM router (thinking mode disabled for latency) |
| `text_sanitizer.py` | Strips emojis from every LLM output (user requirement) |
| `paths.py` | Portable path resolution — works in CI, Docker, any checkout path |
| `security.py` | HMAC signer, OAuth scaffold, rate limiter, secrets vault |
| `compliance.py` | GDPR/LGPD, CAN-SPAM, TCPA, consent, audit, retention |
| `observability.py` | JSON logs, Prometheus metrics, circuit breakers, graceful shutdown |
| `memory.py` | Episodic + semantic memory, lessons, capability index |
| `perception.py` | 9-source input adapter (unified `Percept` shape) |
| `world_model.py` | Context + state |
| `goal_engine.py` | priority × urgency × impact / cost |
| `reasoning.py` | NIM-powered chain-of-thought with function calling |
| `social_adapters.py` | 5 WRITE channels (WhatsApp Cloud, Discord, Telegram, LinkedIn, Instagram) |
| `browser_social.py` | 9 BROWSER channels via opencli subprocess |
| `playwright_social.py` | 6 Playwright adapters using your logged-in Chrome |
| `outreach_strategy.py` | Geo + industry channel scoring (16 countries, 10 industries) |
| `n8n_flows.py` | 8 n8n-style automation flows |
| `browser.py` | Agent-Reach (15 platforms) + Google Maps + direct REST APIs |
| `voice_agent.py` | Twilio TwiML + NIM voice |
| `integrations.py` | Twilio, SendGrid, Apollo, Hunter |
| `prospex_adapter.py` / `crm_adapter.py` | Ported scoring / identity services |

---

## Channels (29 total)

**5 WRITE (API)** — `whatsapp` · `discord` · `telegram` · `linkedin` · `instagram`
Each accepts `transport=browser` to fall back to your logged-in Chrome session
(no API key needed).

**9 BROWSER (no API keys)** — Gmail · Twitter · LinkedIn · Instagram · Facebook ·
Reddit · WhatsApp Web · HubSpot · Salesforce

**15 READ (public APIs, no auth)** — web · github · github_trending · v2ex · rss ·
youtube · hackernews · twitter · reddit · linkedin · facebook · instagram ·
mastodon · threads · bilibili

---

## 8 n8n-style flows

| Flow | Trigger | Runtime |
|---|---|---|
| `auto_respond_dm` | inbound DM | ~8s |
| `lead_capture_to_crm` | social mention | ~12s |
| `cross_post_content` | new content | ~20s |
| `auto_engage_targets` | daily cron | ~60s |
| `inbound_to_outbound` | webhook | ~5s |
| `crm_hygiene` | weekly cron | ~30s |
| `social_listening` | hourly | ~45s |
| `quote_to_post` | user submit | ~25s |

---

## Constitutional invariants (I1–I10)

I1 approval gate · I2 PII consent before egress · I3 token budget ·
I4 per-call timeout · I5 audit trail for lead mutations · I6 subagent depth ≤ 3 ·
I7 destructive ops need approval · I8 capability failure rate ≤ 50% ·
I9 rate limits surfaced to operator · I10 skill promotion needs ≥ 3 lessons

---

## Testing & CI

```bash
make test-unit         # 24 unit tests, no server needed
make test-integration  # 70 integration tests, server must be running
make test              # everything
make lint              # syntax check (33 modules) + emoji scan on UI
make ci                # lint + unit + integration
```

GitHub Actions runs on every push and PR (`.github/workflows/ci.yml`):
import smoke over 18 modules → unit tests → boot server → integration tests.

**94 tests total.** Coverage includes every endpoint group, the 15 read platforms,
the 5 write channels, the 8 flows, and the no-emoji guarantee across both the
server output and the static UI.

---

## Design rules

- **No emojis.** Enforced in `text_sanitizer.py` (server) and `stripEmojis()` (UI).
  CJK, accented Latin and punctuation are preserved.
- **No mocks.** Every endpoint hits real code — real SQLite, real NIM, real HTTP.
- **No paid APIs required.** Browser channels drive your own logged-in Chrome.
- **Paths are portable.** `server/paths.py` resolves everything relative to the
  checkout, so CI, Docker and local all work.

---

## License

Vendored upstream repos retain their original licenses (see each `upstream/*/LICENSE`).
