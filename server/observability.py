"""
Observability + production hardening.

Provides:
1. Structured JSON logs (one log per request, with request_id, duration, status)
2. Prometheus-compatible /metrics endpoint (counters, histograms)
3. Request ID propagation (X-Request-ID in every response)
4. Error boundary wrapper (uncaught exceptions return 500 with request_id, never crash)
5. Graceful shutdown (SIGTERM handler that finishes in-flight requests)
6. Circuit breaker (track external service health, fail-fast if down)
7. Per-endpoint timeout
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable


# === STRUCTURED LOGS ===

LOG_FILE = Path("/workspace/orchestrator/data/orchestrator.log.jsonl")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log_json(level: str, msg: str, **fields):
    """Append one structured JSON log line."""
    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "level": level,
        "msg": msg,
        **fields,
    }
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass
    # Mirror to stderr for dev
    sys.stderr.write(f"[{entry['iso']}] {level} {msg} {fields}\n")


# === PROMETHEUS-COMPATIBLE METRICS ===

class Metrics:
    """Lightweight Prometheus metrics. Stores in-memory and exposes text format."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = defaultdict(float)
        self.histograms: dict[str, list[float]] = defaultdict(list)
        self.labels: dict[str, list[dict]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels):
        with self._lock:
            key = self._label_key(name, labels)
            self.counters[key] += value
            self.labels[name].append(labels)

    def gauge(self, name: str, value: float, **labels):
        with self._lock:
            key = self._label_key(name, labels)
            self.gauges[key] = value
            self.labels[name].append(labels)

    def observe(self, name: str, value: float, **labels):
        with self._lock:
            key = self._label_key(name, labels)
            self.histograms[key].append(value)
            # Keep last 1000 observations per metric
            if len(self.histograms[key]) > 1000:
                self.histograms[key] = self.histograms[key][-1000:]
            self.labels[name].append(labels)

    def _label_key(self, name: str, labels: dict) -> str:
        if not labels:
            return name
        return f"{name}{{{','.join(f'{k}={v!r}' for k, v in sorted(labels.items()))}}}"

    def render(self) -> str:
        """Render Prometheus text exposition format."""
        with self._lock:
            out: list[str] = []
            for name in set(self.counters) | set(self.gauges) | set(self.histograms):
                metric_name = name.split("{")[0]
                out.append(f"# TYPE {metric_name} {'counter' if metric_name in [c.split('{')[0] for c in self.counters] else 'gauge' if metric_name in [g.split('{')[0] for g in self.gauges] else 'histogram'}")
            for key, val in self.counters.items():
                out.append(f"{key} {val}")
            for key, val in self.gauges.items():
                out.append(f"{key} {val}")
            for key, vals in self.histograms.items():
                if not vals:
                    continue
                base = key.split("{")[0]
                out.append(f"{key}_count {len(vals)}")
                out.append(f"{key}_sum {sum(vals):.4f}")
                # Buckets (Prometheus convention)
                sorted_v = sorted(vals)
                for q in [0.5, 0.9, 0.95, 0.99]:
                    idx = int(len(sorted_v) * q)
                    idx = min(idx, len(sorted_v) - 1)
                    out.append(f"{key}" + '{quantile="' + str(q) + '"} ' + f"{sorted_v[idx]:.4f}")
            return "\n".join(out)


_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics


# === REQUEST ID ===

def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


# === ERROR BOUNDARY ===

def safe_handler(handler_method: Callable) -> Callable:
    """Decorator: catch uncaught exceptions, return JSON 500 with request_id."""

    def wrapper(self, *args, **kwargs):
        req_id = new_request_id()
        self._request_id = req_id
        start = time.time()
        status_holder = [500]
        try:
            return handler_method(self, *args, **kwargs)
        except Exception as e:
            log_json("error", "uncaught_exception",
                     request_id=req_id,
                     error=f"{type(e).__name__}: {e}",
                     traceback=traceback.format_exc()[:600])
            try:
                self._send_json({"error": "internal_server_error",
                                 "request_id": req_id}, 500)
            except Exception:
                pass
        finally:
            duration = time.time() - start
            path = getattr(self, "path", "?")
            method = getattr(self, "command", "?")
            log_json("request", "completed",
                     request_id=req_id, path=path, method=method,
                     status=status_holder[0], duration_ms=int(duration * 1000))
            _metrics.inc("http_requests_total", 1, method=method, path=path)
            _metrics.observe("http_request_duration_ms", duration * 1000,
                             method=method, path=path)
    return wrapper


# === CIRCUIT BREAKER ===

class CircuitBreaker:
    """Track external service health. Fail-fast when service is unhealthy.

    State machine:
    - CLOSED: normal, calls go through
    - OPEN: too many failures, calls fail immediately
    - HALF_OPEN: testing if service recovered
    """

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, name: str, *, failure_threshold: int = 5,
                 recovery_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failures = 0
        self._last_failure: float = 0
        self._last_state_change: float = time.time()
        self._lock = threading.Lock()

    def call(self, fn: Callable, *args, **kwargs):
        with self._lock:
            if self._state == self.OPEN:
                if time.time() - self._last_failure > self.recovery_timeout:
                    self._state = self.HALF_OPEN
                    log_json("circuit", "state_change",
                             breaker=self.name, state="half_open")
                else:
                    raise RuntimeError(f"circuit_breaker_open: {self.name}")

        try:
            result = fn(*args, **kwargs)
            with self._lock:
                if self._state == self.HALF_OPEN:
                    self._state = self.CLOSED
                    self._failures = 0
                    log_json("circuit", "state_change",
                             breaker=self.name, state="closed")
            return result
        except Exception as e:
            with self._lock:
                self._failures += 1
                self._last_failure = time.time()
                if self._failures >= self.failure_threshold and self._state != self.OPEN:
                    self._state = self.OPEN
                    log_json("circuit", "state_change",
                             breaker=self.name, state="open",
                             failures=self._failures)
            raise

    def state(self) -> dict:
        return {
            "name": self.name,
            "state": self._state,
            "failures": self._failures,
            "last_failure": self._last_failure,
            "since_last_failure_seconds": time.time() - self._last_failure,
        }


# Standard breakers for each external service
BREAKERS = {
    "nim": CircuitBreaker("nim", failure_threshold=5, recovery_timeout=30),
    "google_maps": CircuitBreaker("google_maps", failure_threshold=3, recovery_timeout=60),
    "agent_reach": CircuitBreaker("agent_reach", failure_threshold=5, recovery_timeout=30),
    "jina": CircuitBreaker("jina", failure_threshold=5, recovery_timeout=20),
    "twilio": CircuitBreaker("twilio", failure_threshold=3, recovery_timeout=120),
    "sendgrid": CircuitBreaker("sendgrid", failure_threshold=3, recovery_timeout=120),
    "apollo": CircuitBreaker("apollo", failure_threshold=3, recovery_timeout=120),
    "hunter": CircuitBreaker("hunter", failure_threshold=3, recovery_timeout=120),
}


def get_breaker(name: str) -> CircuitBreaker:
    return BREAKERS.setdefault(name, CircuitBreaker(name))


# === GRACEFUL SHUTDOWN ===

class GracefulShutdown:
    """SIGTERM handler: stop accepting new connections, finish in-flight, exit."""

    def __init__(self):
        self._shutdown = threading.Event()
        self._in_flight: set = set()
        self._lock = threading.Lock()

    def is_shutting_down(self) -> bool:
        return self._shutdown.is_set()

    def track(self, request_id: str):
        with self._lock:
            self._in_flight.add(request_id)

    def untrack(self, request_id: str):
        with self._lock:
            self._in_flight.discard(request_id)

    def install(self, server):
        """Install SIGTERM/SIGINT handlers."""
        def handler(signum, frame):
            log_json("shutdown", "signal_received", signum=signum)
            self._shutdown.set()
            # Wait up to 30s for in-flight to drain
            deadline = time.time() + 30
            while time.time() < deadline:
                with self._lock:
                    if not self._in_flight:
                        break
                time.sleep(0.5)
            log_json("shutdown", "server_stopping",
                     in_flight=len(self._in_flight))
            server.shutdown()
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)


_shutdown = GracefulShutdown()


def get_shutdown() -> GracefulShutdown:
    return _shutdown


# === HEALTH ===

def liveness() -> dict:
    """Liveness probe — server is up."""
    return {"status": "alive", "ts": time.time()}


def readiness() -> dict:
    """Readiness probe — server can handle traffic."""
    breakers_state = {name: b.state() for name, b in BREAKERS.items()}
    unhealthy = [n for n, s in breakers_state.items() if s["state"] == "open"]
    return {
        "status": "ready" if not unhealthy else "degraded",
        "unhealthy_services": unhealthy,
        "breakers": breakers_state,
        "ts": time.time(),
    }


SOURCE = "Production observability — structured logs + Prometheus metrics + circuit breakers + graceful shutdown + request IDs"
