"""Fine-tuned intent classifier (LoRA). Trained offline; loaded if present."""

from .classifier import load_classifier

__all__ = ["load_classifier"]
