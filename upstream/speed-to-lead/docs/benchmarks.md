# Benchmarks

> Methodology-first, and honest about what's measured vs. pending. Numbers marked _(pending)_ land
> when the LoRA classifier is trained — no placeholders are presented as results.

## Latency (measured)

Qualification runs in-process; the slow part (LLM drafting) is off the request path via the worker.
From the bundled `make demo` run (6 sample leads, rule qualifier, Apple Silicon, no network):

| Stage | p50 | p95 |
|-------|-----|-----|
| Qualify (rule baseline) | **~1.7 ms** | **~9.4 ms** |
| Draft (template, keyless) | <1 ms | <1 ms |
| Draft (LLM, network-bound) | _provider-dependent_ | _provider-dependent_ |

The point: the **decision** to respond is effectively instant; only the optional LLM text generation
costs real time, and it never blocks intake.

## Classifier quality (measured)

`make eval` scores the rule baseline and the LoRA-fine-tuned classifier on a **hand-written, held-out
realistic set** (16 messages, none seen in training — so this measures generalization to real phrasing,
not template recall). DistilBERT base + LoRA (`r=8` on `q_lin`/`v_lin` plus a trained head) =
**744K trainable params, 1.1% of the model**.

| Strategy | Accuracy | Macro-F1 | $/1k leads | Notes |
|----------|----------|----------|-----------|-------|
| Rule baseline (keyword) | 0.500 | 0.500 | **$0** | transparent, zero deps |
| **LoRA classifier** | **0.938** | **0.933** | **~$0** | local inference, no per-call API cost |

The fine-tune nearly **doubles** intent accuracy over keyword rules on phrasing it never saw, while
running locally in milliseconds for ~$0 — the production-economics argument for fine-tuning over
prompting a per-lead LLM. (An LLM zero-shot row is intentionally omitted: it requires a metered API key,
and this repo doesn't ship numbers it didn't measure. Synthetic-train / real-eval split documented in
[`../MODEL_CARD.md`](../MODEL_CARD.md).)

## Cost framing (per 1,000 leads)

- **This tool (rule / LoRA path):** qualification is local → **~$0**. Cost is only incurred if you
  enable LLM drafting, and only on leads worth a personalized reply (not the spam you filtered).
- **Per-seat "instant response" SaaS:** typically billed per seat/month regardless of volume.

Self-hosting trades a SaaS subscription for your own compute — which, for the qualification path, is
effectively free.
