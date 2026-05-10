FROM python:3.12-slim

ARG WITH_EXTRAS=0

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    postgresql-client \
    curl \
    ffmpeg \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-extras.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    if [ "$WITH_EXTRAS" = "1" ]; then pip install -r requirements-extras.txt; fi

COPY src/ ./src/
COPY prompts/ ./prompts/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/

RUN mkdir -p data/exports data/backups data/uploads

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "-m", "src.main"]
