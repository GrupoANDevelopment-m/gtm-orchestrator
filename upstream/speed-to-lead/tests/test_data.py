"""Synthetic training data: balance, determinism, label validity."""

from speed_to_lead.ml.data import INTENT_LABELS, generate, realistic_eval_set


def test_generate_is_balanced_and_labeled() -> None:
    rows = generate(n_per_class=50, seed=1)
    assert len(rows) == 50 * len(INTENT_LABELS)
    counts: dict[str, int] = {}
    for _, label in rows:
        counts[label] = counts.get(label, 0) + 1
    assert set(counts) == set(INTENT_LABELS)
    assert all(c == 50 for c in counts.values())  # every class balanced


def test_generate_is_deterministic() -> None:
    assert generate(30, seed=7) == generate(30, seed=7)


def test_realistic_eval_labels_are_valid() -> None:
    for _, label in realistic_eval_set():
        assert label in INTENT_LABELS
