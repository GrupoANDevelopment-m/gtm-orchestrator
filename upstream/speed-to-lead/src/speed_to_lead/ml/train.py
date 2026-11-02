"""Fine-tune the intent classifier: DistilBERT + LoRA (PEFT), tracked in MLflow.

Run with `make train` (needs the `ml` extra). Produces a LoRA adapter at
`settings.classifier_adapter_path` that the pipeline loads automatically.

Why LoRA over prompting an LLM per lead: a small adapter on a frozen base gives
a model that runs locally in milliseconds for ~$0 — the production-economics
argument we then verify in `evaluate.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging import configure_logging, get_logger
from .data import ID2LABEL, INTENT_LABELS, LABEL2ID, generate

BASE_MODEL = "distilbert-base-uncased"
SEED = 7


def _compute_metrics(eval_pred: Any) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, f1_score

    logits, labels = eval_pred
    preds = logits.argmax(axis=-1)
    return {
        "accuracy": float(accuracy_score(labels, preds)),
        "f1_macro": float(f1_score(labels, preds, average="macro", zero_division=0)),
    }


def main() -> None:
    from datasets import Dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        Trainer,
        TrainingArguments,
    )

    configure_logging("INFO")
    log = get_logger("train")
    out = Path(get_settings().classifier_adapter_path)
    out.mkdir(parents=True, exist_ok=True)

    rows = generate(n_per_class=220, seed=13)
    data = Dataset.from_dict(
        {"text": [t for t, _ in rows], "label": [LABEL2ID[lbl] for _, lbl in rows]}
    ).train_test_split(test_size=0.15, seed=SEED)
    log.info("dataset", train=len(data["train"]), val=len(data["test"]), classes=len(INTENT_LABELS))

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    tokenized = data.map(
        lambda b: tokenizer(b["text"], truncation=True, max_length=64), batched=True
    )

    base = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=len(INTENT_LABELS), id2label=ID2LABEL, label2id=LABEL2ID
    )
    lora = LoraConfig(
        task_type=TaskType.SEQ_CLS,
        r=8,
        lora_alpha=16,
        lora_dropout=0.1,
        target_modules=["q_lin", "v_lin"],
        modules_to_save=["pre_classifier", "classifier"],  # train the new head too
    )
    model = get_peft_model(base, lora)
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=str(out / "_trainer"),
        num_train_epochs=6,
        per_device_train_batch_size=16,
        per_device_eval_batch_size=32,
        learning_rate=2e-4,
        warmup_ratio=0.1,
        weight_decay=0.01,
        eval_strategy="epoch",
        save_strategy="no",
        logging_steps=50,
        seed=SEED,
        report_to=["mlflow"],  # experiment tracking → ./mlruns
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["test"],
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=_compute_metrics,
    )
    trainer.train()
    val = trainer.evaluate()
    log.info(
        "val_metrics",
        accuracy=round(val["eval_accuracy"], 4),
        f1=round(val["eval_f1_macro"], 4),
    )

    model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))
    (out / "meta.json").write_text(
        json.dumps(
            {"base_model": BASE_MODEL, "id2label": {str(k): v for k, v in ID2LABEL.items()}},
            indent=2,
        )
    )
    print(f"\n✓ Saved LoRA adapter → {out}\n  Run `make eval` to score it vs. the baseline.\n")


if __name__ == "__main__":
    main()
