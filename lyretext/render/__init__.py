"""PreTeXt rendering for the review workspace.

``render_pretext`` turns a chapter's ``.ptx`` into preview HTML with no
external toolchain — see pretext_html.py for why this is a preview renderer
rather than a call out to the PreTeXt-CLI.
"""
from .pretext_html import RenderResult, render_pretext

__all__ = ["RenderResult", "render_pretext"]
