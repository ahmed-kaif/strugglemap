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
import re
import shutil
import sys
import uuid
import wave
from datetime import datetime
from pathlib import Path
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


def _extract_python_code(text: str) -> str:
    """Extract Python code from markdown fences if present."""
    stripped = text.strip()
    fence_match = re.search(r"```(?:python)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    return stripped


def _scene_class_name(manim_code: str) -> str:
    """Best-effort Scene subclass extraction from generated code."""
    match = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\(Scene\)\s*:", manim_code)
    if match:
        return match.group(1)
    return "AITeacherLesson"


def _build_default_manim_code(scene_plan: list["Scene"]) -> str:
    """Deterministic Manim fallback when model output is unavailable."""
    scene_payload: list[dict[str, Any]] = []
    for scene in scene_plan:
        narration_lines = [line.strip() for line in scene.narration_text.split(".") if line.strip()]
        scene_payload.append(
            {
                "title": scene.title,
                "concept": scene.concept,
                "narration_lines": narration_lines[:4] or [scene.narration_text],
                "duration": max(4, min(12, scene.duration_seconds // 8 or 6)),
            }
        )

    payload_json = json.dumps(scene_payload, ensure_ascii=False)
    return f'''from manim import *


class AITeacherLesson(Scene):
    def construct(self):
        self.camera.background_color = "#111827"
        scenes = {payload_json}

        for index, scene in enumerate(scenes, start=1):
            title = Text(f"{{index}}. {{scene['title']}}", font_size=42, weight=BOLD).to_edge(UP)
            concept = Text(f"Concept: {{scene['concept']}}", font_size=30, color=YELLOW).next_to(title, DOWN, buff=0.4)
            body = Paragraph(*scene["narration_lines"], alignment="left", line_spacing=0.55)
            body.scale(0.45).next_to(concept, DOWN, buff=0.45)

            frame = RoundedRectangle(width=12.5, height=6.6, corner_radius=0.2, color=BLUE_E)
            frame.set_stroke(width=2)
            frame.move_to(ORIGIN)

            group = VGroup(frame, title, concept, body)

            self.play(FadeIn(frame), Write(title), FadeIn(concept, shift=UP * 0.2), FadeIn(body, shift=UP * 0.2), run_time=1.6)
            self.wait(scene["duration"])
            self.play(FadeOut(group), run_time=0.8)
'''


async def generate_manim_code(scene_plan: list["Scene"]) -> str:  # STUB
    """Generate executable Manim Python code for the provided scene plan."""
    logger.info("generate_manim_code called with %d scenes", len(scene_plan))
    return _build_default_manim_code(scene_plan)


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
    """Render Manim code to MP4 and return the rendered video path."""
    logger.info("render_video called")

    _RENDER_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    script_path = _RENDER_DIR / f"lesson_{run_id}.py"
    media_dir = _RENDER_DIR / f"media_{run_id}"
    output_stem = f"lesson_{run_id}"
    scene_name = _scene_class_name(manim_code)

    script_path.write_text(manim_code, encoding="utf-8")

    manim_bin = shutil.which("manim")
    if manim_bin:
        cmd = [
            manim_bin,
            "-qm",
            "--media_dir",
            str(media_dir),
            str(script_path),
            scene_name,
            "-o",
            output_stem,
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "manim",
            "-qm",
            "--media_dir",
            str(media_dir),
            str(script_path),
            scene_name,
            "-o",
            output_stem,
        ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        err = stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"Manim render failed with exit code {process.returncode}: {err[:3000]}")

    mp4_candidates = sorted((media_dir / "videos").glob("**/*.mp4"))
    if not mp4_candidates:
        raise RuntimeError("Manim completed but no MP4 file was found in media output directory.")

    return str(mp4_candidates[-1])


async def run_tts(segments: list["NarrationSegment"]) -> str:  # STUB
    """Generate a silent WAV matching narration duration as a safe audio fallback."""
    logger.info("run_tts called with %d segments", len(segments))

    _AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = _AUDIO_DIR / f"narration_{uuid.uuid4().hex[:10]}.wav"
    duration = max((seg.end_time for seg in segments), default=1.0)
    sample_rate = 16000
    frames = int(duration * sample_rate)

    def _write_silence_wav() -> None:
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00\x00" * frames)

    await asyncio.to_thread(_write_silence_wav)
    return str(audio_path)


async def merge_av(video_path: str, audio_path: str) -> str:  # STUB
    """Merge audio and video with FFmpeg. Falls back to video-only output if unavailable."""
    logger.info("merge_av called: video=%s, audio=%s", video_path, audio_path)

    _FINAL_DIR.mkdir(parents=True, exist_ok=True)
    final_path = _FINAL_DIR / f"final_{uuid.uuid4().hex[:10]}.mp4"
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.warning("ffmpeg not found; returning video-only output.")
        return video_path

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(final_path),
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        ffmpeg_err = stderr.decode("utf-8", errors="replace") or stdout.decode("utf-8", errors="replace")
        logger.warning("ffmpeg merge failed, returning video-only output: %s", ffmpeg_err[:1000])
        return video_path

    return str(final_path)


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

def _clean_env(value: str | None) -> str:
    """Trim whitespace and optional surrounding single/double quotes."""
    if value is None:
        return ""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


_VERTEX_MODE = _clean_env(os.environ.get("GENAI_BACKEND", "vertex")).lower() in {
    "vertex",
    "vertexai",
    "vertex_ai",
    "google_vertex_ai",
}
_GEMINI_API_KEY = _clean_env(os.environ.get("GEMINI_API_KEY"))
_GOOGLE_CLOUD_PROJECT = _clean_env(os.environ.get("GOOGLE_CLOUD_PROJECT"))
_GOOGLE_CLOUD_LOCATION = _clean_env(os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
_GOOGLE_APPLICATION_CREDENTIALS = _clean_env(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))


def _create_genai_client() -> genai.Client | None:
    """Create genai client in Vertex mode by default, fallback to API key mode."""
    try:
        if _VERTEX_MODE:
            if _GOOGLE_APPLICATION_CREDENTIALS and not Path(_GOOGLE_APPLICATION_CREDENTIALS).exists():
                logger.warning(
                    "GOOGLE_APPLICATION_CREDENTIALS points to a missing file: %s",
                    _GOOGLE_APPLICATION_CREDENTIALS,
                )
            if not _GOOGLE_CLOUD_PROJECT:
                logger.warning("GOOGLE_CLOUD_PROJECT is not set; Vertex AI requests will fail.")
            logger.info(
                "Initialising google-genai client in Vertex AI mode (project=%s, location=%s).",
                _GOOGLE_CLOUD_PROJECT or "<unset>",
                _GOOGLE_CLOUD_LOCATION,
            )
            return genai.Client(vertexai=True, project=_GOOGLE_CLOUD_PROJECT, location=_GOOGLE_CLOUD_LOCATION)

        if not _GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY is not set — Gemini Developer API calls will fail.")
        logger.info("Initialising google-genai client in Gemini Developer API mode.")
        return genai.Client(api_key=_GEMINI_API_KEY)
    except Exception as exc:
        logger.error("Failed to initialize google-genai client: %s", exc)
        return None


# Single client instance shared across all model configs
_genai_client = _create_genai_client()

_ARTIFACTS_DIR = Path("artifacts")
_RENDER_DIR = _ARTIFACTS_DIR / "render"
_AUDIO_DIR = _ARTIFACTS_DIR / "audio"
_FINAL_DIR = _ARTIFACTS_DIR / "final"

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
    if _genai_client is None:
        raise RuntimeError(
            "google-genai client is not initialized. Configure Vertex AI env vars "
            "(GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GOOGLE_APPLICATION_CREDENTIALS) "
            "or set GEMINI_API_KEY when using Developer API mode."
        )

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


def _build_fallback_scene_plan(topic: str, failed_concepts: list[str] | None = None) -> list["Scene"]:
    """Create a deterministic scene plan when model JSON is invalid."""
    concepts = [c for c in (failed_concepts or []) if c.strip()]
    if not concepts:
        concepts = [
            f"What friction means in daily life ({topic})",
            "Types of friction: static, sliding, rolling, and fluid",
            "How to control friction in real-world examples",
        ]

    scenes: list[Scene] = []
    for index, concept in enumerate(concepts[:4], start=1):
        scenes.append(
            Scene(
                id=f"scene_{index}",
                title=f"Concept {index}",
                concept=concept,
                visual_description=(
                    "Use simple labels and arrows to compare force direction, motion direction, "
                    "and resulting effect for this concept."
                ),
                narration_text=(
                    f"In this part, we explain {concept}. We use an easy classroom-style example, "
                    "define the key terms clearly, and connect the idea to daily activities so that "
                    "students can remember when and why friction helps or opposes motion."
                ),
                duration_seconds=60,
            )
        )
    return scenes


def _extract_json_array_candidate(raw_text: str) -> str:
    """Extract the most likely JSON array payload from model output text."""
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _parse_scene_array(raw_text: str) -> list[dict[str, Any]]:
    """Parse scene array JSON with lightweight extraction logic."""
    candidates = [raw_text.strip()]
    extracted = _extract_json_array_candidate(raw_text)
    if extracted not in candidates:
        candidates.append(extracted)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
            raise ValueError("Expected a JSON array at the top level.")
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc

    raise ValueError(f"Unable to parse scenes JSON: {last_error}")


async def _repair_scene_json_with_model(raw_text: str) -> str:
    """Ask Gemini to convert malformed planner output into valid JSON array only."""
    repair_prompt = (
        "Fix the following malformed JSON and return ONLY a valid JSON array of scene objects. "
        "Do not add markdown, comments, or explanation.\n\n"
        f"MALFORMED_JSON:\n{raw_text}"
    )
    response = await _generate_content_with_fallback(
        primary_model=_PLANNER_MODEL,
        fallback_models=_PLANNER_MODEL_FALLBACKS,
        contents=repair_prompt,
        config=_PLANNER_CONFIG,
    )
    return response.text.strip()


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

        try:
            scenes = await self._call_planner_with_retry(prompt)
        except Exception as exc:
            logger.warning("plan_topic fallback engaged due to planner failure: %s", exc)
            target_concepts = state.failed_concepts if state.iteration > 1 else []
            scenes = _build_fallback_scene_plan(user_question, target_concepts)

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
                try:
                    scenes_data = _parse_scene_array(raw_text)
                except ValueError:
                    repaired = await _repair_scene_json_with_model(raw_text)
                    scenes_data = _parse_scene_array(repaired)
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
                try:
                    fix_data = json.loads(_extract_json_array_candidate(fix_response.text))
                    if not isinstance(fix_data, dict):
                        raise ValueError("Expected JSON object with 'fixed_code'.")
                    fixed_code = fix_data.get("fixed_code", manim_code)
                except Exception:
                    logger.warning("Auto-fix response was not valid JSON object; using original code.")
                    fixed_code = manim_code
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
