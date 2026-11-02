# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Email the maintainer with details and a
reproduction; you'll get an acknowledgment within a few days.

## How this project handles secrets

- **Bring-your-own-key.** No credential is ever committed. All keys load from `.env` (gitignored) via
  `pydantic-settings`. `.env.example` documents the variables with placeholders only.
- **`detect-private-key`** runs as a pre-commit hook to block accidental key commits.
- **Webhook authenticity.** Inbound webhooks are verified with an HMAC-SHA256 signature
  (`WEBHOOK_SIGNING_SECRET`) using a constant-time comparison.
- **Least privilege.** The Docker image runs as a non-root user.

## Handling lead data (PII)

Inbound leads are personal data. When self-hosting:

- Restrict the `/leads` endpoint (network policy / API gateway) and always set a signing secret.
- Treat your CRM/Slack destinations as the system of record; this service is stateless by default.
- If you enable auto-send, ensure your outbound email complies with CAN-SPAM / GDPR
  (identification + unsubscribe). See [`docs/compliance.md`](docs/compliance.md).

## Hardening & threat notes

- **Untrusted webhook input** is length-capped (`InboundLead` field limits) and signature-verified
  (HMAC-SHA256). A malformed payload returns `400`, not a stack trace. In production, also front it with a
  gateway enforcing body-size limits and rate limiting.
- **Unauthenticated by default.** `/health`, `/metrics`, `/leads`, `/leads/sync` ship without auth so the demo
  runs anywhere. Before exposing publicly, restrict `/leads*` — especially `/leads/sync`, which runs the
  pipeline inline — behind your gateway/auth. The async `/leads` path plus the confidence gate limit blast radius.
- **Prompt injection.** A lead's message is passed to the LLM drafter. The system prompt constrains it and
  low-confidence drafts are held for human review — but treat auto-sent drafts as model output and keep a human
  in the loop for sensitive sends.
- **Model loading.** Only load LoRA adapters you trust; model files can execute code on load. The shipped path
  produces the adapter locally via `make train`.
- **No raw SQL, no SSRF.** Persistence goes through client SDKs (parameterized); enrichment is offline and never
  fetches user-supplied URLs.
