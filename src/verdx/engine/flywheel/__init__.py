"""Flywheel — learning engine for verdx validation and fixing."""

from verdx.engine.flywheel.api import (
    Flywheel,
    categorize_resolution,
    detect_contradictions,
)
from verdx.engine.flywheel.store import FlywheelStore
from verdx.engine.flywheel.types import Annotation, Correction, Pattern

__all__ = [
    "Annotation",
    "Correction",
    "Flywheel",
    "FlywheelStore",
    "Pattern",
    "categorize_resolution",
    "detect_contradictions",
]
