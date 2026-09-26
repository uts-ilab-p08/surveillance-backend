"""
Generation logic behind POST /assistant/ask and POST /assistant/suggestions
(frontend spec §4.2/§4.3). Both endpoints are stateless (per the spec's
explicit rule: "the frontend sends everything needed each time") — no
search_id, no server-side chat storage.

Retrieval already happened once, in GET /search. These endpoints never
call rag.pipeline.answer_query() again — they only generate text (an
answer, or suggested questions) from the moments the frontend already has
on screen, using the RAG package's shared LLM client
(rag.llm.complete()), the same OpenRouter config /search uses.
"""
from __future__ import annotations

import re
from typing import Literal

from rag import llm

from app.schemas.assistant import AssistantAskRequest, AssistantMoment, ChatTurn


class AssistantUnavailable(Exception):
    """The LLM could not be reached or returned nothing usable."""


# ---------------------------------------------------------------------------
# POST /assistant/ask
# ---------------------------------------------------------------------------

_ASK_SYSTEM_PROMPT = """You are an assistant helping an investigator review CCTV search results in a chat panel next to the video moments.

You are given a numbered-free list of "moments" (short video clips) an earlier search already found, each with a caption, a relevance score (0-1), a start/end time in seconds (relative to that moment's own video clip — NOT a wall-clock or calendar time), and a camera id when known. One moment may be marked FOCUSED — the user selected it before asking.

RULES
- Answer only using the moments given below. Never invent an event, a time, a camera, or an object that isn't in a caption or a listed field.
- Be honest when the question needs data you don't have, in ONE plain sentence, instead of guessing: a wall-clock or calendar time (only relative in-clip seconds are shown here), a camera id (many moments show "unknown"), or following one person/vehicle across different cameras (not supported — only same-camera, same-video comparisons are possible).
- If scope is "moment", lead with what the FOCUSED moment shows; bring in other moments only afterwards, as separate additional context.
- If scope is "results", answer about the moments as a whole set.
- Chat-style: 1 to 3 sentences. No preamble like "Based on the moments provided" or "According to the search results".
- After your answer, on its own final line, name exactly which moment_id(s) it relies on, in this literal format (comma-separated, or empty if none):
CITED: <moment_id>, <moment_id>
Only ever list a moment_id that was given to you below. This line is stripped before the user sees your answer, so it must be the LAST line, nothing after it."""


def _format_moments(moments: list[AssistantMoment], focus_moment_id: str | None) -> str:
    if not moments:
        return "(no moments were provided)"
    lines = []
    for m in moments:
        marker = "  <- FOCUSED MOMENT" if m.moment_id == focus_moment_id else ""
        end = m.end_seconds if m.end_seconds is not None else m.start_seconds
        lines.append(
            f"- moment_id: {m.moment_id}{marker}\n"
            f"  camera: {m.camera or 'unknown'} | in-clip time: {m.start_seconds:.0f}s-{end:.0f}s | "
            f"relevance score: {m.score:.2f}\n"
            f"  caption: {m.caption}"
        )
    return "\n".join(lines)


def _format_history(history: list[ChatTurn]) -> str:
    if not history:
        return "(no earlier messages in this conversation)"
    return "\n".join(f"{'User' if t.role == 'user' else 'Assistant'}: {t.text}" for t in history)


_CITED_LINE_RE = re.compile(r"^\s*CITED:\s*(.*)$", re.IGNORECASE | re.MULTILINE)


def _split_answer_and_citations(raw_text: str, known_ids: set[str]) -> tuple[str, list[str]]:
    """Pull the trailing "CITED: ..." line off the model's reply.

    Only ids that were actually in the request survive — this is the
    spec's step 4 ("remove any citation whose moment_id was not in the
    request"), done here rather than trusting the model.
    """
    last_match = None
    for match in _CITED_LINE_RE.finditer(raw_text):
        last_match = match  # if the model repeats the line, trust the last one

    if last_match is None:
        return raw_text.strip(), []

    answer = raw_text[: last_match.start()].rstrip()
    ids = [token.strip() for token in last_match.group(1).split(",") if token.strip()]

    citations: list[str] = []
    for moment_id in ids:
        if moment_id in known_ids and moment_id not in citations:
            citations.append(moment_id)
    return answer, citations


def generate_answer(request: AssistantAskRequest) -> tuple[str, list[str]]:
    """Returns (answer_text, citation_moment_ids) — see module docstring."""
    known_ids = {m.moment_id for m in request.moments}

    user_prompt = (
        f"Earlier conversation:\n{_format_history(request.history)}\n\n"
        f"Original search that produced these moments: {request.query!r}\n"
        f"Scope: {request.scope}\n\n"
        f"Moments:\n{_format_moments(request.moments, request.focus_moment_id)}\n\n"
        f"Question: {request.question}"
    )

    try:
        raw = llm.complete(_ASK_SYSTEM_PROMPT, user_prompt, max_tokens=600)
    except llm.LLMError as exc:
        raise AssistantUnavailable(str(exc)) from exc

    answer, citations = _split_answer_and_citations(raw, known_ids)
    if not answer:
        raise AssistantUnavailable("Model returned an empty answer")
    return answer, citations


# ---------------------------------------------------------------------------
# Suggested questions — shared by both /assistant/ask's suggested_questions
# field and the standalone /assistant/suggestions endpoint.
#
# Rule-based rather than an LLM call: the spec explicitly allows this
# ("A cheap LLM call or even rule-based logic over the moments is
# acceptable") and rule-based is the only way to *guarantee* "only suggest
# what the data supports" (no camera question with one camera, no vehicle
# question with no vehicles, nothing needing wall-clock time) — an LLM can
# still be tempted to suggest something the data can't actually answer.
# ---------------------------------------------------------------------------

# Same vocabulary app/services/tagging.py uses for object_types, applied
# here as a plain substring check against caption text (AssistantMoment
# has no object_types field — only free-text captions).
_VEHICLE_WORDS = {"car", "truck", "bus", "motorcycle", "bicycle", "bike", "van", "suv", "vehicle"}
_PERSON_WORDS = {"person", "man", "woman", "pedestrian", "individual"}

_MOMENT_SCOPE_QUESTIONS = [
    "Who else was near this location around this time?",
    "What happened right before this moment?",
    "What happened right after this moment?",
]

# Backfilled only if data-driven candidates + moment-scope templates don't
# clear 2 items after removing already-asked ones (the spec's "Done when"
# for /assistant/ask wants 2-4 suggested_questions). Generic enough to
# always be a fair question regardless of what the data contains.
_GENERIC_FALLBACKS: dict[str, list[str]] = {
    "moment": ["What is shown in this moment?", "How confident is this match?"],
    "results": ["Summarize what's shown here", "Explain why these moments matched"],
}


def _mentions_any(caption: str, words: set[str]) -> bool:
    lowered = caption.lower()
    return any(word in lowered for word in words)


def generate_suggestions(
    scope: Literal["results", "moment"],
    moments: list[AssistantMoment],
    history: list[ChatTurn],
) -> list[str]:
    already_asked = {t.text.strip().lower() for t in history if t.role == "user"}

    if scope == "moment":
        candidates = list(_MOMENT_SCOPE_QUESTIONS)
    else:
        if not moments:
            return []
        candidates = []

        cameras = {m.camera for m in moments if m.camera}
        if len(cameras) > 1:
            candidates.append("Which camera has the most matches?")

        candidates.append("Show only the highest-confidence event")

        if any(_mentions_any(m.caption, _VEHICLE_WORDS) for m in moments):
            candidates.append("Narrow this to vehicle events only")
        if any(_mentions_any(m.caption, _PERSON_WORDS) for m in moments):
            candidates.append("Narrow this to person events only")

    result = [q for q in candidates if q.strip().lower() not in already_asked]

    if len(result) < 2:
        for filler in _GENERIC_FALLBACKS[scope]:
            if filler not in result and filler.strip().lower() not in already_asked:
                result.append(filler)
            if len(result) >= 2:
                break

    return result[:3]
