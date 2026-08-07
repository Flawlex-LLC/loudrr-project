# Loudrr-FastAPI backend — multi-stage production image.
#
# Stage 1 (builder): installs all wheels into a clean virtualenv, picks up
# any C extension build deps (libpq-dev for asyncpg, gcc, etc.).
#
# Stage 2 (runtime): slim base, copies the venv, runs as non-root.
# Includes curl so the HEALTHCHECK + Coolify health probes work.
#
# Built with:    docker build -t loudrr-backend ./backend
# Run with:      docker run --rm -p 8000:8000 --env-file backend/.env loudrr-backend
# Coolify:       set the build context to ./backend and the Dockerfile path
#                to ./Dockerfile — the platform handles TLS termination upstream.

# =============================================================================
# Stage 1 — builder
# =============================================================================
FROM python:3.14-slim AS builder

WORKDIR /build

# Build deps for asyncpg, hiredis, cryptography wheels that don't ship for
# every arch. Removed from the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into an isolated venv so we can copy ONLY that into the
# runtime stage (no pip cache, no build tools in prod).
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# =============================================================================
# Stage 2 — runtime
# =============================================================================
FROM python:3.14-slim AS runtime

# Runtime deps:
#   - libpq5: asyncpg's C lib (NOT libpq-dev — that's for building)
#   - curl:   for the HEALTHCHECK probe + Coolify health-route checks
# Then drop the apt cache to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — running as root is a no-go for any prod container.
# UID 1000 matches the typical default on Linux hosts so bind-mounted volumes
# stay readable without chown gymnastics.
RUN useradd --create-home --uid 1000 --shell /bin/bash loudrr

WORKDIR /app

# Copy the virtualenv from the builder. --chown so the non-root user can
# execute the entrypoint.
COPY --from=builder --chown=loudrr:loudrr /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Copy app source. .dockerignore excludes tests/, .env, __pycache__/, .venv/, etc.
COPY --chown=loudrr:loudrr . /app

USER loudrr

EXPOSE 8000

# In-container HEALTHCHECK — hits /readyz which actually probes the DB.
# Coolify also has its own external health check; this one is for Docker /
# bare orchestration where there's no platform-level probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/readyz || exit 1

# Coolify handles process supervision + scaling; we run a single uvicorn that
# uses its asyncio loop's built-in concurrency. If you scale up via Coolify
# replicas instead of in-container workers, this is correct. For bare-metal
# multi-worker, swap to: gunicorn -k uvicorn.workers.UvicornWorker -w 4 app.main:app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
