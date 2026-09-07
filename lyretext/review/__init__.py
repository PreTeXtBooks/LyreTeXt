"""LyreTeXt review package — structured check-agent layer (Validate stage)."""
from .structure import Issue, ReviewReport
from .checks import CheckSpec, CheckRegistry, get_registry, register_check
from .graph import build_review_graph

__all__ = [
    "Issue",
    "ReviewReport",
    "CheckSpec",
    "CheckRegistry",
    "get_registry",
    "register_check",
    "build_review_graph",
]
