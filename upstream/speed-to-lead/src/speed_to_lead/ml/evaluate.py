"""Score the fine-tuned classifier against the rule baseline.

Both are evaluated on the *hand-written* realistic set (`data.realistic_eval_set`),
never seen in training — so the numbers reflect generalization to real phrasing.
Run with `make eval` (needs the `ml` extra).
"""

from __future__ import annotations

from pathlib import Path

from ..config import get_settings
from ..services.qualify import detect_intent, looks_like_spam
from .data import INTENT_LABELS, realistic_eval_set


def _rule_intent(text: str) -> str:
    """The rule baseline's intent decision (spam check first, like the qualifier)."""
    return "spam" if looks_like_spam(text) else detect_intent(text)


def _score(gold: list[str], preds: list[str]) -> tuple[float, float]:
    from sklearn.metrics import accuracy_score, f1_score

    acc = float(accuracy_score(gold, preds))
    f1 = float(f1_score(gold, preds, average="macro", labels=INTENT_LABELS, zero_division=0))
    return acc, f1


def main() -> None:
    from .lora import LoraIntentClassifier

    eval_set = realistic_eval_set()
    texts = [t for t, _ in eval_set]
    gold = [lbl for _, lbl in eval_set]

    clf = LoraIntentClassifier.load(Path(get_settings().classifier_adapter_path))
    lora_preds = [clf.predict(t)[0] for t in texts]
    rule_preds = [_rule_intent(t) for t in texts]

    lora_acc, lora_f1 = _score(gold, lora_preds)
    rule_acc, rule_f1 = _score(gold, rule_preds)

    print("\n  Intent classification — held-out realistic set")
    print(f"  ({len(eval_set)} hand-written messages, unseen in training)\n")
    print(f"  {'strategy':<22}{'accuracy':<12}{'f1_macro':<10}{'$/1k leads'}")
    print("  " + "─" * 56)
    print(f"  {'rule baseline':<22}{rule_acc:<12.3f}{rule_f1:<10.3f}{'$0'}")
    print(f"  {'LoRA classifier':<22}{lora_acc:<12.3f}{lora_f1:<10.3f}{'~$0 (local)'}")
    print()

    # Surface the disagreements — useful, honest signal for the README.
    misses: list[tuple[str, str, str]] = [
        (t, g, p) for t, g, p in zip(texts, gold, lora_preds, strict=True) if g != p
    ]
    if misses:
        print(f"  LoRA misses ({len(misses)}):")
        for text, g, p in misses:
            print(f"    · gold={g:<16} pred={p:<16} “{text[:48]}”")
    print()


if __name__ == "__main__":
    main()
