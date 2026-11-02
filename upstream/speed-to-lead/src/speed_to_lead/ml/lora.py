"""Inference wrapper for the LoRA-fine-tuned intent classifier.

Implements the `Qualifier` protocol: it predicts buyer intent from the message
with the fine-tuned model, then reuses the shared `assemble_result` so its
output is identical in shape to the rule baseline. Torch/transformers are
imported lazily (only when an adapter is actually present).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import EnrichmentResult, Lead, QualificationResult
from ..services.qualify import assemble_result

_MAX_LEN = 64


class LoraIntentClassifier:
    """Loads a DistilBERT + LoRA adapter and qualifies leads with it."""

    name = "lora-classifier"

    def __init__(self, model: Any, tokenizer: Any, id2label: dict[int, str]) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self._id2label = id2label

    @classmethod
    def load(cls, path: Path) -> LoraIntentClassifier:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        meta = json.loads((path / "meta.json").read_text())
        id2label = {int(k): v for k, v in meta["id2label"].items()}
        label2id = {v: k for k, v in id2label.items()}

        base = AutoModelForSequenceClassification.from_pretrained(
            meta["base_model"], num_labels=len(id2label), id2label=id2label, label2id=label2id
        )
        model = PeftModel.from_pretrained(base, str(path))
        model.train(False)  # inference / eval mode
        torch.set_grad_enabled(False)
        tokenizer = AutoTokenizer.from_pretrained(str(path))
        return cls(model, tokenizer, id2label)

    def predict(self, text: str) -> tuple[str, float]:
        """Return (intent_label, confidence) for a raw message."""
        import torch

        inputs = self._tokenizer(text, return_tensors="pt", truncation=True, max_length=_MAX_LEN)
        with torch.no_grad():
            logits = self._model(**inputs).logits
        probs = logits.softmax(dim=-1)[0]
        idx = int(probs.argmax().item())
        return self._id2label[idx], float(probs[idx].item())

    def qualify(self, lead: Lead, enrichment: EnrichmentResult) -> QualificationResult:
        message = (lead.message or "").strip()
        if not message:
            # No text to classify — defer to a neutral intent at low confidence.
            return assemble_result(lead, enrichment, "general_inquiry", 0.4, self.name)
        intent, confidence = self.predict(message)
        return assemble_result(lead, enrichment, intent, confidence, self.name)
