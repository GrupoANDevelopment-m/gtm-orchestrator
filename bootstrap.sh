#!/bin/bash
# Bootstrap — installs ALL deps and starts the orchestrator.
# Run from /workspace/orchestrator/

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create keys/ dir if missing
mkdir -p keys

# Generate API key if not set
if [ ! -f keys/.orchestrator_api_key.txt ] && [ -z "$ORCHESTRATOR_API_KEY" ]; then
    KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "$KEY" > keys/.orchestrator_api_key.txt
    chmod 600 keys/.orchestrator_api_key.txt
    echo "[bootstrap] Generated new API key: $KEY"
    echo "[bootstrap] Saved to keys/.orchestrator_api_key.txt (chmod 600)"
fi

# Generate HMAC webhook signing key if not set
if [ ! -f keys/.webhook_signing_key.txt ] && [ -z "$WEBHOOK_SIGNING_KEY" ]; then
    WKEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    echo "$WKEY" > keys/.webhook_signing_key.txt
    chmod 600 keys/.webhook_signing_key.txt
    echo "[bootstrap] Generated new HMAC webhook signing key (chmod 600)"
fi

# Install ALL Python deps (single shot, idempotent)
echo "[bootstrap] Installing Python dependencies..."
pip install --break-system-packages --index-url https://pypi.org/simple \
    pydantic pydantic-settings email-validator httpx structlog \
    psutil bubus cdp-use pyotp tenacity 2>&1 | tail -3 || true
pip install --break-system-packages -q -r requirements.txt 2>&1 | tail -3 || true

# Kill any old server
pkill -9 -f "server/api.py" 2>/dev/null || true
sleep 1

# Wipe pycache
rm -rf server/__pycache__ upstream/*/__pycache__ 2>/dev/null || true

# === Install DeepSeek Harness (DSH) — subprocess agent runtime ===
# 9162-file open-source agent harness with Cordis plugin framework,
# 100+ packages (tools, sandbox, subagent, session log), web UI on its own port.
# We run it as a subprocess and bridge to it; the user gets the full DSH
# agent UX in a separate tab. This is OPTIONAL — orchestrator works without.
if ! command -v dsh >/dev/null 2>&1; then
    echo "[bootstrap] Installing DeepSeek Harness (DSH) — npm install -g @deepseek-ai/dsh"
    npm install -g @deepseek-ai/dsh 2>&1 | tail -3 || \
        echo "[bootstrap] DSH install failed; orchestrator will run without it"
fi
if command -v dsh >/dev/null 2>&1; then
    echo "[bootstrap] DSH installed: $(dsh --version 2>&1 | head -1)"
    # Spawn DSH web on :3090 in the background
    echo "[bootstrap] Spawning dsh web on :3090 (separate agent runtime)"
    pkill -f "dsh web" 2>/dev/null || true
    sleep 1
    nohup dsh web --host 127.0.0.1 --port 3090 --no-open > dsh.log 2>&1 &
    disown
    echo "[bootstrap] DSH boot logs: tail -f dsh.log"
fi

# === Voice model key check ===
if [ ! -f keys/.nvidia_voice.txt ] && [ -z "$NVIDIA_VOICE_KEY" ]; then
    echo "[bootstrap] NOTE: No voice model key in keys/.nvidia_voice.txt"
    echo "              Drop your nvapi-... key there to enable voice-model testing at /api/voice/test"
fi

# Start server
echo ""
echo "[bootstrap] Starting GTM Orchestrator on port 8000..."
nohup python3 -B server/api.py 8000 > server.log 2>&1 &
disown
sleep 4

# Verify
if curl -sf -o /dev/null http://127.0.0.1:8000/health; then
    echo ""
    echo "[bootstrap] OK — server up at http://127.0.0.1:8000"
    echo ""
    echo "Quick health check:"
    curl -s http://127.0.0.1:8000/health | python3 -m json.tool
    echo ""
    echo "Try:"
    echo "  curl http://127.0.0.1:8000/api/capabilities"
    echo "  curl http://127.0.0.1:8000/api/leads"
    API_KEY=$(cat keys/.orchestrator_api_key.txt 2>/dev/null)
    if [ -n "$API_KEY" ]; then
        echo "  curl -H 'Authorization: Bearer $API_KEY' http://127.0.0.1:8000/api/leads"
    fi
else
    echo "[bootstrap] FAILED — see server.log"
    tail -20 server.log
    exit 1
fi
