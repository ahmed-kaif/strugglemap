# ──────────────────────────────────────────────────────────────────────────────
# Adaptive Video Tutor — Orchestrator API
# Multi-stage build: builder → runtime
#
# Build:
#   docker build -t tutor-orchestrator .
#
# Run:
#   docker run --rm -p 8000:8000 \
#     -e GEMINI_API_KEY=your_key_here \
#     tutor-orchestrator
#
# Or with a .env file:
#   docker run --rm -p 8000:8000 \
#     --env-file .env \
#     tutor-orchestrator
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ──────────────────────────────────────────────────────────
ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder

# System deps needed only at build time (compiling C extensions like grpcio)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create an isolated venv inside the image
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Copy and install dependencies first (layer-cached until requirements change)
COPY requirements.txt .
RUN pip install --upgrade pip --no-cache-dir \
 && pip install --no-cache-dir -r requirements.txt \
 && pip check


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

# Non-root user for security
RUN groupadd --gid 1001 appgroup \
 && useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy the pre-built venv from the builder — no compiler toolchain in runtime
COPY --from=builder /app/venv /app/venv

# Copy application source
COPY orchestrator.py api.py ./

# Make Python use the venv
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# GEMINI_API_KEY must be injected at runtime — never bake secrets into the image
# ENV GEMINI_API_KEY=""   ← intentionally omitted

USER appuser

EXPOSE 8000

# uvicorn with:
#   --host 0.0.0.0   → reachable from outside the container
#   --workers 1      → single worker (asyncio; scale via replicas instead)
#   --no-access-log  → reduce noise; set to --access-log if you need request logs
CMD ["uvicorn", "api:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log"]
