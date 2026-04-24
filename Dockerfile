# ──────────────────────────────────────────────────────────────────────────────
# Adaptive Video Tutor — Orchestrator API
# Multi-stage build: builder → runtime
#
# Build:
#   docker build -t tutor-orchestrator .
#
# Run:
#   docker run --rm -p 8000:8000 \
#     -e GOOGLE_CLOUD_PROJECT=your-gcp-project \
#     -e GOOGLE_CLOUD_LOCATION=us-central1 \
#     -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
#     -v $(pwd)/service-account.json:/app/service-account.json:ro \
#     tutor-orchestrator
#
# Or with a .env file:
#   docker run --rm -p 8000:8000 \
#     --env-file .env \
#     -v $(pwd)/service-account.json:/app/service-account.json:ro \
#     tutor-orchestrator
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ──────────────────────────────────────────────────────────
ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder

# System deps needed only at build time (compiling C extensions like grpcio)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    ffmpeg \
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

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN groupadd --gid 1001 appgroup \
 && useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy the pre-built venv from the builder — no compiler toolchain in runtime
COPY --from=builder /app/venv /app/venv

# Copy application source
COPY orchestrator.py api.py ./

# Ensure runtime user can write generated artifacts
RUN mkdir -p /app/artifacts \
 && chown -R appuser:appgroup /app

# Make Python use the venv
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GENAI_BACKEND=vertex \
    GOOGLE_CLOUD_LOCATION=us-central1 \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/.config/matplotlib

# Inject credentials at runtime (recommended: mount read-only JSON key file)
# ENV GOOGLE_CLOUD_PROJECT=""                ← set at runtime
# ENV GOOGLE_APPLICATION_CREDENTIALS=""      ← set at runtime

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
