"""
Wire types for POST /assistant/ask and POST /assistant/suggestions —
see the frontend spec §3.4 (AssistantMoment/ChatTurn, shared by both) and
§4.2/§4.3 (the request/response envelopes themselves).
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AssistantMoment(BaseModel):
    # Frontend id: "<video_id>:<start_seconds>". Cite by this, never by
    # video_id alone — a video can contain several moments.
    moment_id: str
    video_id: str
    start_seconds: float
    end_seconds: float | None = None
    caption: str
    score: float = Field(ge=0, le=1)
    # null until GET /search returns cameras (frontend spec §5.1) — every
    # moment the frontend sends today will have camera=None. Code here
    # must degrade gracefully rather than assume it's populated.
    camera: str | None = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


def _validate_focus_moment(scope: str, focus_moment_id: str | None, moments: list[AssistantMoment]) -> None:
    if scope != "moment":
        return
    if not focus_moment_id:
        raise ValueError("focus_moment_id is required when scope is 'moment'")
    if not any(m.moment_id == focus_moment_id for m in moments):
        raise ValueError("focus_moment_id must match one of moments[].moment_id")


class AssistantAskRequest(BaseModel):
    query: str  # the original search text
    question: str  # what the user just asked (typed or clicked)
    scope: Literal["results", "moment"]
    focus_moment_id: str | None = None
    moments: list[AssistantMoment] = Field(default_factory=list, max_length=10)
    history: list[ChatTurn] = Field(default_factory=list)  # oldest first

    @model_validator(mode="after")
    def _check_focus_moment(self) -> "AssistantAskRequest":
        _validate_focus_moment(self.scope, self.focus_moment_id, self.moments)
        return self


class AssistantCitation(BaseModel):
    moment_id: str


class AssistantAskResponse(BaseModel):
    answer: str
    citations: list[AssistantCitation]
    suggested_questions: list[str]


class AssistantSuggestionsRequest(BaseModel):
    query: str
    scope: Literal["results", "moment"]
    focus_moment_id: str | None = None
    moments: list[AssistantMoment] = Field(default_factory=list, max_length=10)
    history: list[ChatTurn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_focus_moment(self) -> "AssistantSuggestionsRequest":
        _validate_focus_moment(self.scope, self.focus_moment_id, self.moments)
        return self


class AssistantSuggestionsResponse(BaseModel):
    suggested_questions: list[str]
