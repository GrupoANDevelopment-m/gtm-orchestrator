"""Loader for the fine-tuned LoRA intent classifier.

Contract: `load_classifier()` returns an object implementing the `Qualifier`
protocol (`speed_to_lead.services.qualify.Qualifier`) when a trained adapter
exists at `settings.classifier_adapter_path`, otherwise `None` so the caller
falls back to the rule baseline.

The training pipeline that produces the adapter lives in `ml/train.py`
(`make train`); inference is implemented in `ml/lora.py` and only imports
torch/transformers when an adapter is actually present — so the base install
stays light.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_settings
from ..logging import get_logger

if TYPE_CHECKING:
    from ..services.qualify import Qualifier

log = get_logger(__name__)


def load_classifier() -> Qualifier | None:
    """Return the trained classifier if its adapter exists, else None."""
    path = Path(get_settings().classifier_adapter_path)
    if not path.exists():
        return None
    try:
        from .lora import LoraIntentClassifier

        clf: Qualifier = LoraIntentClassifier.load(path)
        log.info("classifier.loaded", path=str(path))
        return clf
    except Exception as exc:  # missing ml extra / corrupt adapter → fall back to rules
        log.warning("classifier.load_failed", error=str(exc))
        return None
