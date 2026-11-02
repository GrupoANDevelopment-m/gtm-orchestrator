# GTM playbook — how this agent thinks about a lead

This isn't a generic chatbot bolted onto a form. It encodes a go-to-market opinion: **respond fast,
qualify ruthlessly, and personalize by intent.** Here's the logic.

## 1. Speed is the variable

The agent optimizes for **time-to-first-touch**, because that's the lever with the most evidence
behind it. In *"The Short Life of Online Sales Leads"* (HBR, 2011), firms contacting a lead within an
hour were **~7× more likely** to reach a decision-maker than those who waited an hour longer, and
**~60× more** than those waiting a day. The architecture reflects this: the webhook returns `202` in
milliseconds and a worker does the slow work, so intake never blocks.

## 2. Qualification = fit × intent

Every lead is scored on two independent axes, then bucketed into a tier:

| Signal | Why it matters | Source |
|--------|----------------|--------|
| **ICP fit** | Is this the kind of account that closes? | business vs. free email domain, company present, enrichment match |
| **Buyer intent** | Are they ready to act, or browsing? | message classification (pricing / demo / purchase / support / …) |

`score = fit + intent`, mapped to `hot ≥ 0.70 · warm ≥ 0.45 · cold`. Non-buyers (job seekers, SEO
pitches) short-circuit to `spam`. Every result carries human-readable `reasons` — the model never
asks you to trust a black box.

> The rule baseline is transparent and strong. The LoRA-fine-tuned classifier improves intent
> accuracy on ambiguous messages — see [`benchmarks.md`](benchmarks.md).

## 3. Personalize by intent, gate by confidence

The draft adapts its opening to the detected intent (a pricing question gets a pricing answer, not a
generic "thanks"). High-confidence `hot`/`warm` leads can **auto-send**; everything below
`AUTO_SEND_MIN_CONFIDENCE` is drafted but **held for human review** — speed without sending something
embarrassing.

## 4. Attribution is first-class

Every lead keeps its `source` (UTM / referrer). The funnel metrics (`/metrics`) break down volume,
qualification rate, and speed-to-lead latency **by source**, so you can see which channels send leads
that actually qualify — not just which send the most. That's the difference between a lead *counter*
and a growth *system*.

## 5. Same engine, recruiting too

Lead qualification and candidate screening are the same shape: an inbound person, scored for fit and
intent, routed by tier. The `Greenhouse` connector and `docs/` mapping let the same pipeline run an
applicant funnel — qualify, personalize, route to your ATS.
