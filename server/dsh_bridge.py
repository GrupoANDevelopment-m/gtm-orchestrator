"""
DSH (DeepSeek Harness) subprocess bridge.

DSH is a sophisticated agent runtime with:
- Cordis plugin framework (100+ packages)
- React/Vite web UI on its own port (default 3080)
- Real session log, tool sandbox, subagent delegation
- Token auth via URL query + HttpOnly cookie

We run it as a subprocess and provide HTTP access from our Python orchestrator.
The user gets the full DSH agent UX in a separate tab, while our orchestrator
acts as the data plane (leads, drafts, integrations, Google Maps, etc).

DSH is a runtime, not a library — it ships as `npm install -g @deepseek-ai/dsh`
and runs via `dsh web`. We do NOT port it. We launch it and bridge to it.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional


# === DSH lifecycle ===


class DSHBridge:
    """Manages a DSH subprocess + exposes its boot URL/cookie to the orchestrator.

    Usage:
        bridge = DSHBridge(port=3090)
        bridge.start()
        ...
        info = bridge.info()  # {"running": True, "url": "...", "port": 3090, "pid": ...}
    """

    def __init__(self, *, port: int = 3090, host: str = "127.0.0.1", log_dir: str = "/workspace"):
        self.port = port
        self.host = host
        self.log_dir = Path(log_dir)
        self.log_path = self.log_dir / "dsh.log"
        self.proc: Optional[subprocess.Popen] = None
        self.url: Optional[str] = None
        self.token: Optional[str] = None
        self.cookie: Optional[str] = None
        self._started_at: Optional[float] = None
        # Try to attach to an existing DSH process if one is already running
        self._attach_existing()

    def _attach_existing(self):
        """If dsh web is already running (e.g. spawned by bootstrap.sh), capture its
        boot URL/token from the log so the bridge reflects reality."""
        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                cmdline = p.info.get('cmdline') or []
                if not cmdline:
                    continue
                if not any('dsh' in c for c in cmdline):
                    continue
                if not any('web' in c for c in cmdline):
                    continue
                # Found a dsh web process
                pid = p.info['pid']
                try:
                    proc = psutil.Process(pid)
                    if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                        continue
                except psutil.NoSuchProcess:
                    continue
                self.proc = proc
                if not self.log_path.exists():
                    return
                try:
                    txt = self.log_path.read_text(errors="ignore")
                    m = re.search(r"http://127\.0\.0\.1:\d+/\?token=([A-Za-z0-9_-]+)", txt)
                    if m:
                        self.token = m.group(1)
                        self.url = m.group(0)
                        try:
                            self._started_at = proc.create_time()
                        except Exception:
                            self._started_at = None
                        try:
                            self.cookie = self._fetch_auth_cookie(self.url)
                        except Exception:
                            pass
                except Exception:
                    pass
                return
        except Exception:
            pass

    @property
    def running(self) -> bool:
        if self.proc is None:
            return False
        # psutil.Process has no .poll(); check .is_running() and status
        try:
            import psutil
            if isinstance(self.proc, psutil.Process):
                return self.proc.is_running() and self.proc.status() != psutil.STATUS_ZOMBIE
        except Exception:
            pass
        # subprocess.Popen: check .poll()
        try:
            return self.proc.poll() is None
        except Exception:
            return False

    def start(self, *, wait_seconds: int = 90) -> dict:
        """Spawn dsh web and wait until it prints the boot URL.

        Returns {"running": bool, "url": ..., "token": ..., "port": ...}
        """
        if self.running:
            return self.info()

        # Make sure no stale process on the port
        try:
            subprocess.run(["pkill", "-f", "dsh web"], capture_output=True, timeout=5)
        except Exception:
            pass
        time.sleep(1)

        log_fh = open(self.log_path, "wb")
        env = os.environ.copy()
        env.setdefault("DSH_HOME", str(self.log_dir / ".dsh"))
        self.proc = subprocess.Popen(
            ["dsh", "web", "--host", self.host, "--port", str(self.port), "--no-open"],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=os.setsid,  # new process group, easy to kill
        )
        self._started_at = time.time()

        url_re = re.compile(r"http://127\.0\.0\.1:\d+/\?token=([A-Za-z0-9_-]+)")
        # The boot URL is printed once DSH is ready. Poll the log.
        deadline = time.time() + wait_seconds
        self.url = None
        self.token = None
        while time.time() < deadline:
            try:
                txt = self.log_path.read_text(errors="ignore")
                m = url_re.search(txt)
                if m:
                    self.token = m.group(1)
                    self.url = m.group(0)
                    break
            except FileNotFoundError:
                pass
            # If the process died, bail early
            if not self.running:
                break
            time.sleep(2)

        if not self.url:
            # Boot didn't finish in time; surface the log tail in the error
            tail = ""
            try:
                tail = self.log_path.read_text(errors="ignore")[-2000:]
            except Exception:
                pass
            return {
                "running": self.running,
                "port": self.port,
                "pid": self.proc.pid if self.proc else None,
                "started_at": self._started_at,
                "boot_url": None,
                "token": None,
                "cookie": None,
                "error": "DSH boot timed out",
                "log_tail": tail,
            }

        # Hit the URL to set the auth cookie
        self.cookie = self._fetch_auth_cookie(self.url)
        return self.info()

    def _fetch_auth_cookie(self, url: str) -> Optional[str]:
        """Hit DSH's URL-with-token page to capture the HttpOnly cookie.

        DSH sets dsh-auth-... cookie on first visit. We use python urllib instead
        of curl because HttpOnly cookies require explicit cookie-jar handling.
        """
        try:
            import http.cookiejar
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            opener.open(url, timeout=15).read()
            for c in cj:
                if c.name.startswith("dsh-auth-"):
                    return f"{c.name}={c.value}"
        except Exception:
            pass
        return None

    def info(self) -> dict:
        return {
            "running": self.running,
            "port": self.port,
            "pid": self.proc.pid if self.proc else None,
            "started_at": self._started_at,
            "uptime_seconds": (time.time() - self._started_at) if self._started_at else None,
            "boot_url": self.url,
            "token": self.token,
            "cookie": self.cookie,
            "log_path": str(self.log_path),
        }

    def stop(self) -> dict:
        if not self.proc:
            return {"stopped": False, "reason": "no process"}
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            self.proc.wait(timeout=10)
        except Exception:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass
        pid = self.proc.pid
        self.proc = None
        return {"stopped": True, "pid": pid, "port": self.port}


# Singleton (set by api.py)
dsh: Optional[DSHBridge] = None


def get_dsh() -> DSHBridge:
    global dsh
    if dsh is None:
        dsh = DSHBridge()
    return dsh


# === DSH source identification ===

SOURCE = "REAL DeepSeek Harness (deepseek-ai/deepseek-harness-master) — 9162 files, 100+ packages, Cordis plugin framework, vendored as subprocess runtime"
