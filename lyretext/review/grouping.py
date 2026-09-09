"""Finding identity — the rule that decides when two findings are "the same".

The check-agents report one systematic problem once per occurrence, so a chapter
routinely comes back with twenty findings that say the same thing about twenty
different places. Two things need to agree on when that has happened:

  * the review gate UI, which collapses them into one row with one Fix button
  * finalize_review, which carries a user's dismissal across review passes

so the rule lives here and is stamped onto each Issue as ``group_key``. The UI
groups by that field rather than re-deriving the rule in JavaScript.

**Findings group by their suggestion.** What makes a group worth having is that
one edit resolves all of it, and the suggestion is the only field that says what
that edit is. Keying on the message as well was too strict in practice: a check
that names the offending element per occurrence —

    The definition '1.11 definition' lacks the required <statement> structure.
    The definition '1.13 definition' lacks the required <statement> structure.

— writes a different message every time while describing one systematic problem.

Normalisation therefore has to absorb per-instance identifiers, not just line
numbers: quoted literals and dotted numbers are masked along with standalone
integers, so ``xml:id='def-fn-addition'`` and ``'1.11 definition'`` stop making
two findings look distinct. Digits glued to other characters (2x, h2) are still
left alone — those are content, not identity.

Findings with no suggestion fall back to check_id + message, so they can group
with each other without every suggestion-less finding collapsing into one row.
"""
from __future__ import annotations

import re

# Anything inside matching quotes: element names, xml:ids, titles — the things a
# check varies per occurrence while describing one systematic problem.
_QUOTED = re.compile(r"""(['"])(?:(?!\1).)*\1""")
# Whole numbers and dotted numbers (7, 34, 1.11), but only standing alone —
# "2x" and "h2" keep their digits because there they are content.
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)*(?![\w])")


def normalise(text: str | None) -> str:
    """Strip the parts of a finding that vary between its occurrences."""
    masked = _QUOTED.sub("'#'", text or "")
    return _NUMBER.sub("#", masked)


def group_key(*, check_id: str | None, message: str | None, suggestion: str | None) -> str:
    """Return the identity shared by every occurrence of one systematic finding."""
    if suggestion and suggestion.strip():
        return f"fix | {normalise(suggestion)}"
    # No suggestion to key on — fall back to the finding itself, scoped to its
    # check so unrelated suggestion-less findings never share a row.
    return f"finding | {check_id or ''} | {normalise(message)}"


def finding_identity(issue: object) -> str:
    """Stable cross-run identity of a finding — the reconciliation seam (#33).

    Chosen implementation (decision: reuse ``group_key``): a finding's identity
    *is* its group key — the normalised content key the review gate UI and the
    dismissal-carry already share. Keeping reconciliation on that same rule is
    what stops the three from drifting apart.

    Swap this body for a block anchor (``block_id``, issue-draft 005) once
    block-level translation populates it, and finalize_review's reconciliation
    follows without further change — that is the whole point of the seam.

    Accepts an ``Issue`` or a raw sidecar dict.
    """
    if isinstance(issue, dict):
        get = issue.get
    else:
        get = lambda k, d=None: getattr(issue, k, d)  # noqa: E731
    stored = get("group_key")
    if stored:
        return stored
    return group_key(
        check_id=get("check_id"),
        message=get("message"),
        suggestion=get("suggestion"),
    )
