from typing import Generator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.assistant import (
    AssistantAskRequest,
    AssistantAskResponse,
    AssistantCitation,
    AssistantSuggestionsRequest,
    AssistantSuggestionsResponse,
    ChatTurn,
)
from app.services.assistant import AssistantUnavailable, generate_answer, generate_suggestions
from app.services.sse import format_sse_event as _sse

router = APIRouter(tags=["assistant"])

# Shown to the user on any LLM/RAG failure — same message for every cause
# (unreachable model, rate limit, timeout), per the spec's error table for
# both endpoints: the user can just retry.
_UNAVAILABLE_MESSAGE = "The assistant couldn't answer that. Try again."


@router.post("/assistant/ask", response_model=AssistantAskResponse)
def ask_assistant(
    body: AssistantAskRequest,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> AssistantAskResponse:
    """§4.2 — conversational follow-up chat on the Results screen.

    Stateless (the frontend resends query/moments/history every call —
    see app/services/assistant.py's module docstring). scope="moment"
    without a valid focus_moment_id is rejected as 422 by
    AssistantAskRequest's own validator before this function runs.
    """
    try:
        answer, citation_ids = generate_answer(body)
    except AssistantUnavailable as exc:
        raise HTTPException(status_code=502, detail=_UNAVAILABLE_MESSAGE) from exc

    # Suggestions for the *next* question should account for the one just
    # asked too, so they don't immediately repeat it.
    history_including_this_turn = body.history + [ChatTurn(role="user", text=body.question)]
    suggested_questions = generate_suggestions(
        scope=body.scope,
        moments=body.moments,
        history=history_including_this_turn,
        db=db,
        focus_moment_id=body.focus_moment_id,
    )

    return AssistantAskResponse(
        answer=answer,
        citations=[AssistantCitation(moment_id=moment_id) for moment_id in citation_ids],
        suggested_questions=suggested_questions,
    )


# ---------------------------------------------------------------------------
# POST /assistant/ask/stream — same request/response contract as
# /assistant/ask, but sent as Server-Sent Events so the frontend can show
# what's happening instead of a blank wait while the LLM call is in flight.
#
# This is coarse status, not token-by-token streaming: rag.llm.complete()
# is a single blocking call with no streaming variant today, so there's no
# real progress to report *during* it — only before and after. A phrase is
# emitted at each actual step this function takes; the LLM call itself is
# the one long gap between two of them, which is the honest limit of what
# this can show without a streaming LLM client.
#
# Native browser EventSource only supports GET with no request body, and
# this needs one (moments/history can be too large for a query string), so
# the frontend must consume this with fetch() + a manual SSE reader (e.g.
# the response body's ReadableStream, or a small library), not
# `new EventSource(...)`.
#
# Event framing: `event: status` for progress phrases (data: {"phase",
# "message"}), `event: result` once with the same shape as
# AssistantAskResponse, or `event: error` (data: {"message"}) in place of
# result if the LLM call fails. The frontend should treat "result" or
# "error" as the terminal event either way.
# ---------------------------------------------------------------------------


def _ask_assistant_stream_events(body: AssistantAskRequest, db: Session) -> Generator[str, None, None]:
    if body.scope == "moment":
        yield _sse("status", {"phase": "reading", "message": "Reading focused moment…"})
    else:
        yield _sse("status", {"phase": "reading", "message": f"Reading {len(body.moments)} moments…"})

    yield _sse("status", {"phase": "thinking", "message": "Generating answer…"})
    try:
        answer, citation_ids = generate_answer(body)
    except AssistantUnavailable:
        yield _sse("error", {"message": _UNAVAILABLE_MESSAGE})
        return

    yield _sse("status", {"phase": "citing", "message": "Verifying citations…"})
    yield _sse("status", {"phase": "suggesting", "message": "Drafting follow-up questions…"})

    history_including_this_turn = body.history + [ChatTurn(role="user", text=body.question)]
    suggested_questions = generate_suggestions(
        scope=body.scope,
        moments=body.moments,
        history=history_including_this_turn,
        db=db,
        focus_moment_id=body.focus_moment_id,
    )

    result = AssistantAskResponse(
        answer=answer,
        citations=[AssistantCitation(moment_id=moment_id) for moment_id in citation_ids],
        suggested_questions=suggested_questions,
    )
    yield _sse("result", result.model_dump())


@router.post("/assistant/ask/stream")
def ask_assistant_stream(
    body: AssistantAskRequest,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Same behaviour as POST /assistant/ask, as Server-Sent Events — see
    the module-level comment above _ask_assistant_stream_events for the
    event framing and why this can't be true token streaming."""
    return StreamingResponse(
        _ask_assistant_stream_events(body, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/assistant/suggestions", response_model=AssistantSuggestionsResponse)
def suggest_questions(
    body: AssistantSuggestionsRequest,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> AssistantSuggestionsResponse:
    """§4.3 — opening suggested questions. Called once per context: right
    after a search, when a moment is selected, or when Clip Detail opens.
    Rule-based, not an LLM call — see app/services/assistant.py."""
    questions = generate_suggestions(
        scope=body.scope, moments=body.moments, history=body.history, db=db, focus_moment_id=body.focus_moment_id
    )
    return AssistantSuggestionsResponse(suggested_questions=questions)
