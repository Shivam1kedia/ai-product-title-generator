"""AI product title generator for B2B listings."""
from .pipeline import AUTO_APPROVED, FALLBACK, NEEDS_REVIEW, STATUS_LABEL, TitleResult, generate

__all__ = ["generate", "TitleResult", "AUTO_APPROVED", "NEEDS_REVIEW", "FALLBACK", "STATUS_LABEL"]
