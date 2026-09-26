from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, get_current_user
from app.schemas.assistant import (
    AssistantAskRequest,
    AssistantAskResponse,
    AssistantCitation,
    AssistantSuggestionsRequest,
    AssistantSuggestionsResponse,
    ChatTurn,
)
from app.services.assistant import AssistantUnavailable, generate_answer, generate_suggestions

router = APIRouter(tags=["assistant"])

# Shown to the user on any LLM/RAG failure — same message for every cause
# (unreachable model, rate limit, timeout), per the spec's error table for
# both endpoints: the user can just retry.
_UNAVAILABLE_MESSAGE = "The assistant couldn't answer that. Try again."


@router.post("/assistant/ask", response_model=AssistantAskResponse)
def ask_assistant(
    body: AssistantAskRequest,
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
    )

    return AssistantAskResponse(
        answer=answer,
        citations=[AssistantCitation(moment_id=moment_id) for moment_id in citation_ids],
        suggested_questions=suggested_questions,
    )


@router.post("/assistant/suggestions", response_model=AssistantSuggestionsResponse)
def suggest_questions(
    body: AssistantSuggestionsRequest,
    _user: CurrentUser = Depends(get_current_user),
) -> AssistantSuggestionsResponse:
    """§4.3 — opening suggested questions. Called once per context: right
    after a search, when a moment is selected, or when Clip Detail opens.
    Rule-based, not an LLM call — see app/services/assistant.py."""
    questions = generate_suggestions(scope=body.scope, moments=body.moments, history=body.history)
    return AssistantSuggestionsResponse(suggested_questions=questions)
