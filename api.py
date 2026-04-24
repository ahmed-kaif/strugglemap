"""
api.py — FastAPI application for the Adaptive Video Tutoring Orchestrator
=========================================================================

Endpoints
---------
  POST /session/start              — start a new tutoring session
  POST /session/{session_id}/quiz  — submit quiz answers and get feedback
  GET  /session/{session_id}/state — inspect full session state (debug / UI)

Run locally:
    uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from orchestrator import (
    Question,
    SessionState,
    TutorOrchestrator,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Application-level singletons
# ─────────────────────────────────────────────────────────────────────────────

# In a production deployment swap this plain dict for a Redis / DB-backed store.
_session_store: dict[str, Any] = {}
_orchestrator: TutorOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise shared resources on startup and clean up on shutdown."""
    global _orchestrator
    _orchestrator = TutorOrchestrator(session_store=_session_store, max_iterations=3)
    logger.info("TutorOrchestrator initialised.")
    yield
    logger.info("Shutting down — sessions in memory: %d", len(_session_store))


def get_orchestrator() -> TutorOrchestrator:
    if _orchestrator is None:
        raise RuntimeError("Orchestrator has not been initialised yet.")
    return _orchestrator


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Adaptive Video Tutor — Orchestrator API",
    description=(
        "AI-powered adaptive tutoring service that plans lessons, "
        "coordinates multimedia generation, and adapts to quiz performance."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────────────────────


class StartSessionRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        description="The student's natural-language learning question.",
        examples=["Explain how neural networks learn using backpropagation"],
    )


class StartSessionResponse(BaseModel):
    session_id: str
    video_path: str | None = None
    questions: list[Question] = Field(default_factory=list)
    error: str | None = None


class QuizAnswer(BaseModel):
    question_id: str = Field(..., description="Matches Question.id")
    answer: str = Field(..., description="The student's answer text or selected option")


class SubmitQuizRequest(BaseModel):
    answers: list[QuizAnswer] = Field(..., min_length=1)


class SubmitQuizResponse(BaseModel):
    status: str = Field(..., description="'complete' or 'continue'")
    # Fields present when status == "complete"
    final_score: float | None = None
    summary: str | None = None
    # Fields present when status == "continue"
    gaps: list[str] | None = None
    encouragement: str | None = None
    feedback: dict[str, str] | None = None
    # Error passthrough (rare)
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /session/start
# ─────────────────────────────────────────────────────────────────────────────


@app.post(
    "/session/start",
    response_model=StartSessionResponse,
    summary="Start a new tutoring session",
    tags=["Session"],
)
async def start_session(body: StartSessionRequest) -> StartSessionResponse:
    """
    Kick off a brand-new adaptive tutoring session.

    The orchestrator will:
    1. Plan a multi-scene lesson around the student's question.
    2. Generate Manim animation code and narration concurrently.
    3. Render the video, run TTS, and merge to a final MP4.
    4. Generate an initial quiz to probe understanding.

    Returns the session_id, the path to the rendered video, and the quiz
    questions that the frontend should present to the student.
    """
    orchestrator = get_orchestrator()
    try:
        result = await orchestrator.start_session(user_question=body.question)
    except Exception as exc:
        logger.exception("Unexpected error in start_session")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if "error" in result and result["error"]:
        # Partial failure — return a 200 with the error field populated
        # so the frontend can still display the session_id and retry.
        return StartSessionResponse(
            session_id=result["session_id"],
            error=result["error"],
        )

    return StartSessionResponse(
        session_id=result["session_id"],
        video_path=result.get("video_path"),
        questions=result.get("questions", []),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /session/{session_id}/quiz
# ─────────────────────────────────────────────────────────────────────────────


@app.post(
    "/session/{session_id}/quiz",
    response_model=SubmitQuizResponse,
    summary="Submit quiz answers and receive feedback",
    tags=["Session"],
)
async def submit_quiz(
    body: SubmitQuizRequest,
    session_id: str = Path(..., description="The session UUID returned by /session/start"),
) -> SubmitQuizResponse:
    """
    Submit the student's quiz answers.

    The orchestrator evaluates the answers, updates the session state,
    and returns one of two outcomes:

    - **complete**: the student passed (score ≥ 0.70) or the maximum
      number of iterations has been reached.
    - **continue**: gaps were detected; a new lesson targeting only
      those concepts will be prepared. The `gaps` and `encouragement`
      fields guide the frontend's next step (call `run_iteration`
      again and present the new video + quiz).

    **Note**: after receiving `status: continue` the frontend should
    trigger another `POST /session/{session_id}/next` to kick off the
    remediation iteration (extend this API as needed).
    """
    orchestrator = get_orchestrator()

    # Verify session exists before calling the orchestrator
    if session_id not in _session_store:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    # Reconstruct Question objects from session state so we can re-evaluate
    # In a real system the frontend would pass the question ids and the
    # orchestrator would look them up.  Here we derive what we need from state.
    state_raw = _session_store[session_id]
    state = SessionState.model_validate(state_raw)

    # Rebuild question list from the last scene plan (concept names) so
    # evaluate_answers has type-safe Question objects.
    last_scenes = state.last_scene_plan
    questions: list[Question] = []
    if last_scenes:
        for i, scene in enumerate(last_scenes, start=1):
            questions.append(
                Question(
                    id=f"q_{i}",
                    text=f"What is the key idea behind '{scene.concept}'?",
                    type="open",
                    options=None,
                    correct_answer=f"A correct explanation of {scene.concept}.",
                    concept_tested=scene.concept,
                )
            )

    answers_dicts = [a.model_dump() for a in body.answers]

    try:
        result = await orchestrator.process_quiz_results(
            session_id=session_id,
            questions=questions,
            user_answers=answers_dicts,
        )
    except Exception as exc:
        logger.exception("Unexpected error in submit_quiz for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return SubmitQuizResponse(**result)


# ─────────────────────────────────────────────────────────────────────────────
# Route: GET /session/{session_id}/state
# ─────────────────────────────────────────────────────────────────────────────


@app.get(
    "/session/{session_id}/state",
    response_model=SessionState,
    summary="Retrieve full session state",
    tags=["Session"],
)
async def get_session_state(
    session_id: str = Path(..., description="The session UUID"),
) -> SessionState:
    """
    Return the complete session state for debugging or UI progress display.

    Includes iteration number, covered concepts, detected gaps, and the
    full history of teach→quiz cycles.
    """
    if session_id not in _session_store:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    return SessionState.model_validate(_session_store[session_id])


# ─────────────────────────────────────────────────────────────────────────────
# Route: POST /session/{session_id}/next  (convenience continuation endpoint)
# ─────────────────────────────────────────────────────────────────────────────


@app.post(
    "/session/{session_id}/next",
    summary="Run the next remediation iteration after gaps were detected",
    tags=["Session"],
)
async def next_iteration(
    session_id: str = Path(..., description="The session UUID"),
) -> dict[str, Any]:
    """
    Run the next lesson iteration targeting detected gap concepts.

    Call this endpoint after receiving `status: continue` from the quiz
    submission.  Returns the same shape as the internal run_iteration:

        { "video_path": str, "session_id": str, "iteration": int,
          "concepts_taught": list[str] }

    or on error:

        { "error": str, "session_id": str }
    """
    if session_id not in _session_store:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    orchestrator = get_orchestrator()
    try:
        result = await orchestrator.run_iteration(session_id)
    except Exception as exc:
        logger.exception("Unexpected error in next_iteration for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["Meta"], summary="Health check")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "adaptive-tutor-orchestrator"}
