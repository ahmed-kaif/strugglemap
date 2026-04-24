"""
orchestrator.py — AI Orchestrator for Adaptive Video Tutoring System
=====================================================================

EXAMPLE USAGE (run as a standalone test script):

    import asyncio
    import orchestrator

    async def main():
        store = {}
        tutor = orchestrator.TutorOrchestrator(session_store=store, max_iterations=3)
        result = await tutor.start_session("Explain how neural networks learn")
        print("Session ID :", result["session_id"])
        print("Video path :", result["video_path"])
        print("Quiz Q's   :", [q.text for q in result["questions"]])

        # Simulate answering the quiz
        fake_answers = [
            {"question_id": q.id, "answer": q.correct_answer}
            for q in result["questions"]
        ]
        quiz_result = await tutor.process_quiz_results(
            result["session_id"],
            result["questions"],
            fake_answers,
        )
        print("Quiz result:", quiz_result)

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# EXTERNAL FUNCTION STUBS
# Replace each stub with a real implementation when the downstream modules
# are ready.  Every stub is async so the orchestrator can await them uniformly.
# ─────────────────────────────────────────────────────────────────────────────


async def generate_manim_code(scene_plan: list["Scene"]) -> str:  # STUB
    """Generate Manim Python code from a list of Scene objects."""
    logger.info("[STUB] generate_manim_code called with %d scenes", len(scene_plan))
    return "# Manim code placeholder"


async def generate_narration(scene_plan: list["Scene"]) -> list["NarrationSegment"]:  # STUB
    """Generate narration segments aligned to each scene."""
    logger.info("[STUB] generate_narration called with %d scenes", len(scene_plan))
    segments: list[NarrationSegment] = []
    elapsed = 0
    for scene in scene_plan:
        seg = NarrationSegment(
            scene_id=scene.id,
            text=scene.narration_text,
            start_time=float(elapsed),
            end_time=float(elapsed + scene.duration_seconds),
        )
        segments.append(seg)
        elapsed += scene.duration_seconds
    return segments


async def render_video(manim_code: str) -> str:  # STUB
    """Render Manim code to an MP4 file; returns the file path."""
    logger.info("[STUB] render_video called")
    return "/tmp/output_video.mp4"


async def run_tts(segments: list["NarrationSegment"]) -> str:  # STUB
    """Run TTS on narration segments; returns the audio file path."""
    logger.info("[STUB] run_tts called with %d segments", len(segments))
    return "/tmp/output_audio.wav"


async def merge_av(video_path: str, audio_path: str) -> str:  # STUB
    """Merge audio and video with FFmpeg; returns the final MP4 path."""
    logger.info("[STUB] merge_av called: video=%s, audio=%s", video_path, audio_path)
    return "/tmp/final_output.mp4"


async def generate_quiz(topic: str, concepts: list[str]) -> list["Question"]:  # STUB
    """Generate quiz questions for the given topic and concept list."""
    logger.info("[STUB] generate_quiz called: topic=%s, concepts=%s", topic, concepts)
    questions: list[Question] = []
    for i, concept in enumerate(concepts, start=1):
        questions.append(
            Question(
                id=f"q_{i}",
                text=f"What is the key idea behind '{concept}'?",
                type="open",
                options=None,
                correct_answer=f"A correct explanation of {concept}.",
                concept_tested=concept,
            )
        )
    return questions


async def evaluate_answers(
    questions: list["Question"],
    answers: list[dict[str, str]],
    concepts: list[str],
) -> "EvaluationResult":  # STUB
    """Evaluate user answers and return an EvaluationResult."""
    logger.info("[STUB] evaluate_answers called")
    answer_map = {a["question_id"]: a["answer"] for a in answers}
    passed_concepts: list[str] = []
    failed_concepts: list[str] = []
    feedback: dict[str, str] = {}
    for q in questions:
        user_ans = answer_map.get(q.id, "")
        # Naïve stub: treat non-empty answers as correct
        if user_ans.strip():
            passed_concepts.append(q.concept_tested)
        else:
            failed_concepts.append(q.concept_tested)
            feedback[q.concept_tested] = f"No answer was provided for '{q.concept_tested}'."
    score = len(passed_concepts) / max(len(questions), 1)
    return EvaluationResult(
        passed=score >= 0.7,
        score=score,
        gaps=list(dict.fromkeys(failed_concepts)),  # deduplicate, preserve order
        feedback=feedback,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────


class Scene(BaseModel):
    """A single instructional scene in the lesson plan."""

    id: str = Field(..., description="Unique scene identifier, e.g. 'scene_1'")
    title: str = Field(..., description="Short human-readable title")
    concept: str = Field(..., description="The core concept this scene teaches")
    visual_description: str = Field(..., description="What Manim should animate")
    narration_text: str = Field(..., description="What the narrator says over this scene")
    duration_seconds: int = Field(60, ge=1, description="Estimated scene duration in seconds")


class NarrationSegment(BaseModel):
    """A timed narration block aligned to a single scene."""

    scene_id: str
    text: str
    start_time: float = Field(..., ge=0)
    end_time: float = Field(..., ge=0)


class Question(BaseModel):
    """A single quiz question."""

    id: str
    text: str
    type: str = Field(..., pattern="^(mcq|open)$")
    options: list[str] | None = None
    correct_answer: str
    concept_tested: str


class EvaluationResult(BaseModel):
    """The outcome of evaluating a completed quiz."""

    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list, description="Concept names that failed")
    feedback: dict[str, str] = Field(
        default_factory=dict,
        description="Per-concept explanation of what was wrong",
    )


class IterationRecord(BaseModel):
    """Snapshot of one teach→quiz cycle."""

    iteration: int
    concepts_taught: list[str]
    quiz_score: float
    gaps_found: list[str]
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class SessionState(BaseModel):
    """Full mutable state for a tutoring session."""

    session_id: str
    topic: str
    iteration: int = Field(1, ge=1)
    covered_concepts: list[str] = Field(default_factory=list)
    failed_concepts: list[str] = Field(default_factory=list)
    history: list[IterationRecord] = Field(default_factory=list)
    last_scene_plan: list[Scene] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI CLIENT INITIALISATION  (google-genai SDK — replaces deprecated
# google-generativeai which reached end-of-life on 30 Nov 2025)
# ─────────────────────────────────────────────────────────────────────────────

_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if len(_GEMINI_API_KEY) >= 2 and _GEMINI_API_KEY[0] == _GEMINI_API_KEY[-1] and _GEMINI_API_KEY[0] in {'"', "'"}:
    _GEMINI_API_KEY = _GEMINI_API_KEY[1:-1].strip()
if not _GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY is not set — Gemini calls will fail at runtime.")

# Single client instance shared across both model configs
_genai_client = genai.Client(api_key=_GEMINI_API_KEY)

# Config: structured / JSON output — planning, quiz generation, evaluation
_PLANNER_CONFIG = genai_types.GenerateContentConfig(
    system_instruction=(
        "You are a curriculum designer. "
        "You always respond with valid JSON only. "
        "No explanation, no markdown, no extra text."
    ),
    response_mime_type="application/json",
    max_output_tokens=2000,
    temperature=0.4,
)

# Config: natural-language output — short motivational / summary text
_CHAT_CONFIG = genai_types.GenerateContentConfig(
    max_output_tokens=300,
    temperature=0.8,
)

_PLANNER_MODEL = "gemini-2.5-pro"    # strong reasoning for structured JSON planning
_CHAT_MODEL    = "gemini-2.5-flash"  # fast, cheap for short natural-language responses
_PLANNER_MODEL_FALLBACKS = (
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
)
_CHAT_MODEL_FALLBACKS = (
    "gemini-2.0-flash-lite",
)


def _is_quota_or_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "resource_exhausted",
            "quota",
            "rate limit",
            "rate-limit",
            "too many requests",
            "unavailable",
            "high demand",
            "429",
            "503",
        )
    )


async def _generate_content_with_fallback(
    *,
    primary_model: str,
    fallback_models: tuple[str, ...],
    contents: str,
    config: genai_types.GenerateContentConfig,
):
    models_to_try = [primary_model] + [m for m in fallback_models if m != primary_model]
    last_error: Exception | None = None

    for index, model_name in enumerate(models_to_try):
        try:
            return await _genai_client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            last_error = exc
            has_next_model = index < len(models_to_try) - 1
            if has_next_model and _is_quota_or_rate_limit_error(exc):
                logger.warning(
                    "Gemini model '%s' hit quota/rate limits; falling back to '%s'.",
                    model_name,
                    models_to_try[index + 1],
                )
                continue
            raise

    raise RuntimeError("All configured Gemini models failed.") from last_error

# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

_SCENE_SCHEMA = """
{
  "id": "scene_1",
  "title": "<short title>",
  "concept": "<the core concept taught>",
  "visual_description": "<what Manim should animate>",
  "narration_text": "<narrator voice-over text>",
  "duration_seconds": 60
}
"""


def _build_planning_prompt(
    user_question: str,
    iteration: int,
    covered_concepts: list[str],
    failed_concepts: list[str],
) -> str:
    """Construct the structured planning prompt for Gemini."""

    covered_str = (
        ", ".join(covered_concepts) if covered_concepts else "none yet"
    )
    gaps_str = ", ".join(failed_concepts) if failed_concepts else "none"

    base = f"""
You are designing a video lesson plan.

Topic / student question: "{user_question}"
Current iteration: {iteration}
Already-covered concepts (DO NOT repeat these): {covered_str}

Return a JSON array of scene objects. Each scene must match this schema exactly:
{_SCENE_SCHEMA}

Rules:
- Include 3–6 scenes.
- Each scene teaches ONE distinct concept.
- Keep narration_text between 50 and 150 words.
- Keep visual_description concrete and animation-friendly.
- duration_seconds should reflect narration length (typical: 45–90 s).
"""

    if iteration > 1 and failed_concepts:
        base += f"""
IMPORTANT — Remediation iteration:
These concepts were already taught but not understood: {gaps_str}.
Use a DIFFERENT teaching approach than the first attempt.
- If iteration 1 used abstract definitions, use concrete real-world analogies now.
- If iteration 1 used analogies, use step-by-step worked examples now.
- If iteration 1 used worked examples, use an analogy + worked example combination.
Only include scenes that address the gap concepts listed above.
"""

    return base.strip()


class TutorOrchestrator:
    """
    Brain of the adaptive video tutoring pipeline.

    Coordinates lesson planning, multimedia generation, quiz evaluation,
    and adaptive remediation across multiple teach→quiz→gap iterations.
    """

    def __init__(self, session_store: dict[str, Any], max_iterations: int = 3) -> None:
        self._store = session_store
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------ #
    # session helpers                                                       #
    # ------------------------------------------------------------------ #

    def _get_state(self, session_id: str) -> SessionState:
        if session_id not in self._store:
            raise KeyError(f"Session '{session_id}' not found.")
        return SessionState.model_validate(self._store[session_id])

    def _save_state(self, state: SessionState) -> None:
        self._store[state.session_id] = state.model_dump()

    # ------------------------------------------------------------------ #
    # plan_topic                                                            #
    # ------------------------------------------------------------------ #

    async def plan_topic(self, session_id: str, user_question: str) -> list[Scene]:
        """
        Ask Gemini to produce a structured scene plan.

        On iteration > 1 the prompt focuses exclusively on gap concepts so
        the student receives targeted remediation rather than a full replay.
        """
        state = self._get_state(session_id)

        prompt = _build_planning_prompt(
            user_question=user_question,
            iteration=state.iteration,
            covered_concepts=state.covered_concepts,
            failed_concepts=state.failed_concepts,
        )

        scenes = await self._call_planner_with_retry(prompt)

        # Persist plan into session state
        state.last_scene_plan = scenes
        self._save_state(state)

        logger.info(
            "plan_topic → session=%s iter=%d scenes=%d",
            session_id,
            state.iteration,
            len(scenes),
        )
        return scenes

    async def _call_planner_with_retry(self, prompt: str) -> list[Scene]:
        """Call planner model; on JSON parse failure, retry once with a correction hint."""
        for attempt in range(2):
            try:
                effective_prompt = prompt if attempt == 0 else (
                    prompt
                    + "\n\nPrevious response failed JSON parsing. "
                    "Return ONLY a valid JSON array matching the schema above."
                )
                response = await _generate_content_with_fallback(
                    primary_model=_PLANNER_MODEL,
                    fallback_models=_PLANNER_MODEL_FALLBACKS,
                    contents=effective_prompt,
                    config=_PLANNER_CONFIG,
                )
                raw_text = response.text.strip()
                scenes_data: list[dict] = json.loads(raw_text)
                if not isinstance(scenes_data, list):
                    raise ValueError("Expected a JSON array at the top level.")
                return [Scene.model_validate(s) for s in scenes_data]
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("plan attempt %d failed: %s", attempt + 1, exc)
                if attempt == 1:
                    raise RuntimeError(
                        "Gemini planner returned unparseable JSON after 2 attempts."
                    ) from exc
        # Unreachable — satisfies type checkers
        raise RuntimeError("Unexpected exit from retry loop.")

    # ------------------------------------------------------------------ #
    # run_iteration                                                         #
    # ------------------------------------------------------------------ #

    async def run_iteration(self, session_id: str) -> dict[str, Any]:
        """
        Execute one full teach cycle:

          plan → [manim code + narration] (concurrent) → render → TTS → merge

        Returns a dict the API layer can send directly to the client.
        """
        try:
            state = self._get_state(session_id)
            scenes = await self.plan_topic(session_id, state.topic)

            # ── Concurrent generation ──────────────────────────────────
            manim_code, narration_segments = await asyncio.gather(
                generate_manim_code(scenes),
                generate_narration(scenes),
            )

            # ── Sequential render / TTS / merge ───────────────────────
            try:
                video_path = await render_video(manim_code)
            except Exception as render_err:
                logger.error("render_video failed: %s — attempting auto-fix", render_err)
                fix_prompt = (
                    f"The following Manim code produced this error:\n\n"
                    f"ERROR:\n{render_err}\n\n"
                    f"CODE:\n{manim_code}\n\n"
                    "Return ONLY the corrected Manim Python code as a JSON string "
                    'value: {"fixed_code": "..."}.'
                )
                fix_response = await _generate_content_with_fallback(
                    primary_model=_PLANNER_MODEL,
                    fallback_models=_PLANNER_MODEL_FALLBACKS,
                    contents=fix_prompt,
                    config=_PLANNER_CONFIG,
                )
                fix_data = json.loads(fix_response.text.strip())
                fixed_code = fix_data.get("fixed_code", manim_code)
                video_path = await render_video(fixed_code)

            audio_path = await run_tts(narration_segments)
            final_path = await merge_av(video_path, audio_path)

            concepts_taught = list(dict.fromkeys(s.concept for s in scenes))

            result = {
                "video_path": final_path,
                "session_id": session_id,
                "iteration": state.iteration,
                "concepts_taught": concepts_taught,
            }
            logger.info("run_iteration complete: %s", result)
            return result

        except Exception as exc:
            logger.exception("run_iteration crashed for session %s", session_id)
            return {"error": str(exc), "session_id": session_id}

    # ------------------------------------------------------------------ #
    # process_quiz_results                                                  #
    # ------------------------------------------------------------------ #

    async def process_quiz_results(
        self,
        session_id: str,
        questions: list[Question],
        user_answers: list[dict[str, str]],
    ) -> dict[str, Any]:
        """
        Evaluate answers, update session state, and decide whether to
        loop back for remediation or declare the session complete.
        """
        state = self._get_state(session_id)
        taught_concepts = list(dict.fromkeys(q.concept_tested for q in questions))

        evaluation: EvaluationResult = await evaluate_answers(
            questions, user_answers, taught_concepts
        )

        # ── Update session state ───────────────────────────────────────
        passed_concepts = [c for c in taught_concepts if c not in evaluation.gaps]
        record = IterationRecord(
            iteration=state.iteration,
            concepts_taught=taught_concepts,
            quiz_score=evaluation.score,
            gaps_found=evaluation.gaps,
        )
        state.history.append(record)

        # Mark passed concepts as covered; update gap list
        for concept in passed_concepts:
            if concept not in state.covered_concepts:
                state.covered_concepts.append(concept)

        state.failed_concepts = evaluation.gaps  # replace with latest gaps
        state.iteration += 1
        self._save_state(state)

        # ── Decide: complete or continue ───────────────────────────────
        session_complete = evaluation.passed or state.iteration > self.max_iterations

        if session_complete:
            if evaluation.passed:
                summary = (
                    f"Excellent work! You've mastered all concepts in '{state.topic}' "
                    f"with a score of {evaluation.score:.0%}. "
                    f"Total iterations: {len(state.history)}."
                )
            else:
                # Max iterations reached without full mastery
                remaining = ", ".join(state.failed_concepts)
                summary = (
                    f"You've made great progress! For deeper understanding, "
                    f"consider reviewing additional resources on: {remaining}. "
                    f"Final score: {evaluation.score:.0%}."
                )
            return {
                "status": "complete",
                "final_score": evaluation.score,
                "summary": summary,
            }

        # ── Generate encouragement for the next round ──────────────────
        gaps_str = ", ".join(evaluation.gaps)
        encourage_prompt = (
            f"The student is learning {state.topic}. "
            f"They got {evaluation.score:.0%} on the quiz and struggled with: {gaps_str}. "
            "Write one warm, encouraging sentence to motivate them to try again."
        )
        try:
            encourage_resp = await _generate_content_with_fallback(
                primary_model=_CHAT_MODEL,
                fallback_models=_CHAT_MODEL_FALLBACKS,
                contents=encourage_prompt,
                config=_CHAT_CONFIG,
            )
            encouragement = encourage_resp.text.strip()
        except Exception as exc:
            logger.warning("Encouragement generation failed: %s", exc)
            encouragement = (
                "Keep going — every great learner hits a bump on the road. You've got this!"
            )

        return {
            "status": "continue",
            "gaps": evaluation.gaps,
            "encouragement": encouragement,
            "feedback": evaluation.feedback,
        }

    # ------------------------------------------------------------------ #
    # start_session                                                         #
    # ------------------------------------------------------------------ #

    async def start_session(self, user_question: str) -> dict[str, Any]:
        """
        Bootstrap a brand-new tutoring session.

        1. Creates a SessionState with a fresh UUID.
        2. Runs the first teach iteration (plan → generate → render → merge).
        3. Generates an initial quiz based on the concepts just taught.
        4. Returns the session_id, video path, and quiz questions.
        """
        session_id = str(uuid.uuid4())

        state = SessionState(
            session_id=session_id,
            topic=user_question,
            iteration=1,
        )
        self._save_state(state)
        logger.info("start_session: new session %s for topic: %s", session_id, user_question)

        iteration_result = await self.run_iteration(session_id)

        if "error" in iteration_result:
            # Propagate the error but still return the session_id so
            # the frontend can display a meaningful message.
            return {
                "session_id": session_id,
                "error": iteration_result["error"],
            }

        # Reload state after run_iteration (it may have updated last_scene_plan)
        state = self._get_state(session_id)
        concepts_taught = iteration_result.get("concepts_taught", [])

        try:
            questions = await generate_quiz(state.topic, concepts_taught)
        except Exception as exc:
            logger.error("generate_quiz failed: %s", exc)
            questions = []

        return {
            "session_id": session_id,
            "video_path": iteration_result["video_path"],
            "questions": questions,
        }
