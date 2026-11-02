# Model Card — Lead Intent Classifier (LoRA)

A small text classifier that labels an inbound-lead message with its **buyer intent**, used by
`speed-to-lead-agent` to qualify leads. Built to demonstrate production-minded fine-tuning: replace a
per-lead LLM call with a model that runs locally in milliseconds for ~$0.

## Model details

- **Base model:** `distilbert-base-uncased` (66M params, frozen)
- **Adapter:** LoRA via PEFT — `r=8`, `alpha=16`, dropout `0.1`, targets `q_lin`/`v_lin`; the
  classification head (`pre_classifier`, `classifier`) is also trained
- **Trainable parameters:** 744,200 — **1.1%** of the model
- **Task:** single-label sequence classification, 8 classes:
  `purchase_intent · pricing · demo_request · support · partnership · general_inquiry · spam · job_inquiry`
- **Framework:** PyTorch + Hugging Face Transformers + PEFT; experiments tracked with MLflow
- **Reproduce:** `make train` (≈30s on a laptop) then `make eval`

## Training data

**Synthetic and reproducible.** A template-based generator (`speed_to_lead/ml/data.py`) produces a
balanced corpus of inbound-lead messages (~260/class) by slot-filling hand-written templates with a
fixed seed. No external data or API keys are required.

This is a deliberate, documented bootstrapping choice: it ships a working, reproducible classifier with
zero data dependencies. The cost is distribution coverage — see Limitations.

## Evaluation

Evaluated on a **hand-written, held-out realistic set** (16 messages, none templated, none seen in
training) so the score reflects generalization to real phrasing rather than template recall.

| Strategy | Accuracy | Macro-F1 |
|----------|----------|----------|
| Rule baseline (keyword) | 0.500 | 0.500 |
| **LoRA classifier** | **0.938** | **0.933** |

One miss: *"Take my money, how do I subscribe today"* → predicted `pricing` (gold `purchase_intent`).

## Limitations & responsible use

- **Synthetic training distribution.** Trained on generated text; real inbound messages are messier
  (typos, multi-intent, other languages). Expect lower accuracy in the wild than on the curated set, and
  retrain on your own labeled leads for production. The honest realistic-set number (0.938) is a better
  guide than the synthetic-val number (1.0).
- **English only.**
- **Small eval set** (16) — directional, not a benchmark.
- **Not a hiring/credit decision system.** It routes sales leads. The `job_inquiry`/`spam` classes filter
  non-buyers; they are not fit for evaluating people.
- **Human-in-the-loop by default.** Low-confidence predictions are held for review, not auto-actioned
  (see the pipeline's confidence gate).

## License

MIT — same as the repository.
