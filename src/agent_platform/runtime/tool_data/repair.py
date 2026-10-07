"""Repairing chart/table blocks the model cannot draw, inside the model's own turn.

An ``after_model`` hook sees the reply the moment it is produced, reports an
undrawable block back to the model, and sends the graph back for a corrected
reply. It runs in the agent that wrote the block, so it works for every runtime
(a plain agent, a supervisor, a swarm, a graph flow) without knowing a node name.

Surgical repair: the model is asked for *only* the broken block(s) and the
server splices them back into the previous reply (:func:`splice_blocks`), so a
single bad chart out of ten does not force the model to regenerate all ten.
A full re-send still works — it is detected as prose plus a full block set and
validated as before, including the dropped-block guard.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.config import get_stream_writer

from src.agent_platform.runtime.tool_data.spec import (
    Block,
    chart_feedback,
    extract_blocks,
    validate_reply,
)

logger = logging.getLogger("text2sql.agent_platform")

#: How many times a turn asks the model to correct a block it cannot draw.
MAX_CHART_FIX_ATTEMPTS = 2

#: The status a client receives when a draft it has already been streamed is
#: superseded. It is the client's cue to drop that draft.
DISCARD_DRAFT = "fixing_chart"

#: A patch-only reply carries blocks and almost no prose. Anything longer is a
#: full re-send (an explanation plus blocks) and is validated as one.
_PATCH_PROSE_LIMIT = 80


def dropped_blocks(reference: str | None, candidate: str) -> list[dict[str, Any]]:
    """Blocks the turn drew earlier that ``candidate`` no longer has.

    Counted by kind rather than by reference: correcting ``#CHART_D9`` into
    ``#CHART_D1`` is the repair being asked for, not a block being dropped.
    """
    if not reference:
        return []
    drawn = Counter(block.kind for block in extract_blocks(reference))
    kept = Counter(block.kind for block in extract_blocks(candidate))
    problems: list[dict[str, Any]] = []
    for kind in sorted(drawn):
        lost = drawn[kind] - kept[kind]
        if lost > 0:
            problems.append(
                {
                    "kind": kind,
                    "ref": "",
                    "error": (
                        f"your reply has {lost} fewer {kind} block(s) than the previous reply; "
                        "keep every block the previous reply drew"
                    ),
                }
            )
    return problems


def _prose_length(text: str, blocks: list[Block]) -> int:
    """Non-block prose in ``text``, ignoring fences and blank lines."""
    tmp = text or ""
    for block in blocks:
        if block.raw and block.raw in tmp:
            tmp = tmp.replace(block.raw, "", 1)
    kept: list[str] = []
    for line in tmp.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        kept.append(line)
    return len("\n".join(kept).strip())


def is_patch_reply(candidate: str, reference: str | None) -> bool:
    """Whether ``candidate`` is a patch (only fixed blocks) rather than a full reply."""
    if not reference:
        return False
    candidate_blocks = extract_blocks(candidate)
    reference_blocks = extract_blocks(reference)
    if not candidate_blocks or not reference_blocks:
        return False
    if len(candidate_blocks) >= len(reference_blocks):
        return False
    return _prose_length(candidate, candidate_blocks) <= _PATCH_PROSE_LIMIT


def _splice_once(merged: str, old_raw: str, new_raw: str) -> str:
    """``merged`` with the first occurrence of ``old_raw`` replaced."""
    if old_raw and old_raw in merged:
        return merged.replace(old_raw, new_raw, 1)
    logger.warning("reference block raw not found; appending patch block")
    return f"{merged.rstrip()}\n{new_raw}\n" if merged else new_raw


def _problem_count(text: str, scope: Any) -> int:
    try:
        return len(validate_reply(text, scope))
    except Exception:
        logger.exception("chart validation failed")
        return 1 << 30


def splice_blocks(reference: str, patch: str) -> str:
    """``reference`` with each block in ``patch`` spliced over its match.

    Matching is by ``(kind, ref)`` first, so the nine good blocks are never
    touched. A block whose reference was corrected (``#CHART_D9`` → ``#CHART_D1``)
    matches the first still-unmatched reference block of the same kind instead,
    and ``#NOTE`` blocks (which carry no ref) match in order. When several
    blocks share one reference, use :func:`best_splice` with a scope so the
    broken occurrence wins instead of the first one.
    """
    reference_blocks = extract_blocks(reference or "")
    patch_blocks = extract_blocks(patch or "")
    if not reference_blocks or not patch_blocks:
        return patch if patch else (reference or "")
    merged = reference or ""
    used: set[int] = set()
    note_order = 0
    notes = [index for index, block in enumerate(reference_blocks) if block.kind == "note"]
    for incoming in patch_blocks:
        target: int | None = None
        if incoming.kind == "note":
            if note_order < len(notes) and notes[note_order] not in used:
                target = notes[note_order]
            note_order += 1
        else:
            for index, existing in enumerate(reference_blocks):
                if index in used or existing.kind != incoming.kind:
                    continue
                if existing.ref and incoming.ref and existing.ref == incoming.ref:
                    target = index
                    break
            if target is None:
                for index, existing in enumerate(reference_blocks):
                    if index in used or existing.kind != incoming.kind:
                        continue
                    target = index
                    break
        if target is None:
            merged = _splice_once(merged, "", incoming.raw)
            continue
        used.add(target)
        merged = _splice_once(merged, reference_blocks[target].raw, incoming.raw)
    return merged


def best_splice(reference: str, patch: str, scope: Any = None) -> str:
    """Like :func:`splice_blocks`, but a patch targeting a repeated reference.

    Several blocks can share one ``(kind, ref)`` (five ``#CHART_D2`` lines in a
    ten-chart answer). Replacing the first match can clobber a good chart while
    the broken one survives. When ``scope`` is given, each incoming block is
    tried against every still-unmatched reference block of the same kind and the
    replacement with the fewest remaining validation problems wins; ties prefer
    the exact ``(kind, ref)`` match. Without a scope this falls back to
    :func:`splice_blocks`.
    """
    if scope is None:
        return splice_blocks(reference, patch)
    reference_blocks = extract_blocks(reference or "")
    patch_blocks = extract_blocks(patch or "")
    if not reference_blocks or not patch_blocks:
        return patch if patch else (reference or "")
    merged = reference or ""
    # Track replacements against the *current* merged text via block raws.
    current_raws: list[str] = [block.raw for block in reference_blocks]
    used: set[int] = set()
    note_order = 0
    notes = [index for index, block in enumerate(reference_blocks) if block.kind == "note"]
    for incoming in patch_blocks:
        if incoming.kind == "note":
            target = notes[note_order] if note_order < len(notes) else None
            note_order += 1
            if target is None or target in used:
                merged = _splice_once(merged, "", incoming.raw)
                continue
            used.add(target)
            merged = _splice_once(merged, current_raws[target], incoming.raw)
            current_raws[target] = incoming.raw
            continue
        exact = [
            index
            for index, existing in enumerate(reference_blocks)
            if index not in used
            and existing.kind == incoming.kind
            and existing.ref
            and incoming.ref
            and existing.ref == incoming.ref
        ]
        same_kind = [
            index
            for index, existing in enumerate(reference_blocks)
            if index not in used and existing.kind == incoming.kind and index not in exact
        ]
        candidates = [*exact, *same_kind]
        if not candidates:
            merged = _splice_once(merged, "", incoming.raw)
            continue
        if len(candidates) == 1:
            best = candidates[0]
        else:
            best = candidates[0]
            best_score: int | None = None
            for index in candidates:
                trial = _splice_once(merged, current_raws[index], incoming.raw)
                score = _problem_count(trial, scope)
                # Prefer fewer problems; on a tie prefer the exact-ref match.
                if best_score is None or score < best_score:
                    best_score = score
                    best = index
                    if score == 0 and index in exact:
                        break
        used.add(best)
        merged = _splice_once(merged, current_raws[best], incoming.raw)
        current_raws[best] = incoming.raw
    return merged


def merged_reply(
    reference: str | None, candidate: str, scope: Any = None
) -> tuple[str, bool]:
    """The full reply ``candidate`` stands for, and whether it was a patch."""
    if reference and is_patch_reply(candidate, reference):
        if scope is not None:
            return best_splice(reference, candidate, scope), True
        return splice_blocks(reference, candidate), True
    return candidate, False


class ChartRepairMiddleware(AgentMiddleware):
    """Reports undrawable chart/table blocks to the model inside its own turn."""

    def __init__(self, scope: Any, *, max_attempts: int = MAX_CHART_FIX_ATTEMPTS) -> None:
        super().__init__()
        self.scope = scope
        self.max_attempts = max_attempts
        self._attempts = 0
        self._reference: str | None = None
        self._last_reply: str | None = None

    def _reset(self) -> None:
        self._attempts = 0
        self._reference = None
        self._last_reply = None

    def _problems(self, text: str) -> list[dict[str, Any]]:
        try:
            problems = validate_reply(text, self.scope)
        except Exception:
            logger.exception("chart validation failed")
            problems = []
        return [*problems, *dropped_blocks(self._reference, text)]

    def _announce(self) -> None:
        try:
            get_stream_writer()({"type": "status", "message": DISCARD_DRAFT})
        except Exception:
            logger.debug("no stream writer for the chart-repair notice", exc_info=True)

    def _check(self, state: Any) -> dict[str, Any] | None:
        messages = (state or {}).get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
            return None
        text = str(last.content or "")
        if not text.strip():
            previous = self._last_reply
            self._reset()
            if previous is None:
                return None
            return {
                "messages": [
                    RemoveMessage(id=str(last.id)),
                    AIMessage(content=previous),
                ]
            }
        if self._reference is None:
            # First reply of the turn: it is the full answer by definition.
            self._last_reply = text
            problems = self._problems(text)
            if not problems:
                self._reset()
                return None
            if self._attempts >= self.max_attempts:
                logger.warning(
                    "chart blocks still invalid after %s fix(es): %s", self._attempts, problems
                )
                self._reset()
                return None
            self._attempts += 1
            self._reference = text
            self._announce()
            return {
                "messages": [HumanMessage(content=chart_feedback(problems))],
                "jump_to": "model",
            }
        merged, was_patch = merged_reply(self._reference, text, self.scope)
        if was_patch:
            try:
                problems = validate_reply(merged, self.scope)
            except Exception:
                logger.exception("chart validation failed")
                problems = []
        else:
            problems = self._problems(merged)
        # The full answer is what the user must finally see and what an empty
        # follow-up restores, so remember the merged text, not the bare patch.
        self._last_reply = merged
        if not problems:
            self._reset()
            if was_patch and merged != text:
                return {
                    "messages": [
                        RemoveMessage(id=str(last.id)),
                        AIMessage(content=merged),
                    ]
                }
            return None
        if self._attempts >= self.max_attempts:
            logger.warning("chart blocks still invalid after %s fix(es): %s", self._attempts, problems)
            self._reset()
            if was_patch and merged != text:
                # The last model message was a bare patch; leave the full
                # answer (with the remaining error block(s) rendered downstream)
                # rather than a fragment.
                return {
                    "messages": [
                        RemoveMessage(id=str(last.id)),
                        AIMessage(content=merged),
                    ]
                }
            return None
        self._attempts += 1
        self._announce()
        return {
            "messages": [HumanMessage(content=chart_feedback(problems))],
            "jump_to": "model",
        }

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._check(state)

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._check(state)


__all__ = [
    "MAX_CHART_FIX_ATTEMPTS",
    "DISCARD_DRAFT",
    "best_splice",
    "dropped_blocks",
    "is_patch_reply",
    "merged_reply",
    "splice_blocks",
    "ChartRepairMiddleware",
]
