"""Turning a run's raw failure into something the user can act on.

A provider or graph error arrives as an envelope: long, and specific to a library
the user never chose. The user needs the cause and the next action, so the error
is clipped and, when the cause is recognisable, given a hint that names the
setting to change.
"""

from __future__ import annotations

#: Provider payloads are long; the user needs the cause and the next action, not
#: the whole gateway envelope.
_ERROR_CLIP = 400

_TOOL_CHOICE_HINT = (
    "\n\nHint: the request forced a tool call, which this model rejects in thinking "
    "mode. If the agent has Structured output, set its strategy to Provider (native "
    "schema); Auto and Tool both force a tool call."
)

_LOOP_HINT = (
    "\n\nHint: the flow kept looping until the graph's step budget ran out. If a router "
    "routes back into the flow it came from, set its Max passes (for example 3) so the "
    "loop can exit with an answer; the Studio's Checks tab flags this."
)

_CHART_REPAIR_HINT = (
    "\n\nHint: the graph's step budget ran out while the model was correcting a chart or "
    "table block. Raise the agent's recursion limit, or simplify the chart request."
)


def friendly_error(message: str, *, chart_repair: bool = False) -> str:
    """A readable run error: clipped provider text plus an actionable hint.

    ``chart_repair`` says the turn asked the model to repair a chart block, so a
    spent step budget is reported as the repair's cost rather than as a flow that
    looped on its own.
    """
    text = str(message or "")
    lowered = text.lower()
    clipped = text if len(text) <= _ERROR_CLIP else text[:_ERROR_CLIP] + " …"
    if "tool_choice" in lowered and "thinking mode" in lowered:
        return clipped + _TOOL_CHOICE_HINT
    if "recursion limit" in lowered:
        return clipped + (_CHART_REPAIR_HINT if chart_repair else _LOOP_HINT)
    return clipped
