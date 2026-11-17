"""Errors shared across commands."""
from __future__ import annotations

from typing import List, Optional


class Refused(Exception):
    """A safety rule said no. Carries the reasons."""

    def __init__(self, message: str, reasons: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        self.reasons = reasons or []
