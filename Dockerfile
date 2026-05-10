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
    # Chromium runtime deps for Playwright (replacing `--with-deps`, which
    # tries to install package names that don't exist on Debian Bookworm).
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-extras.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    if [ "$WITH_EXTRAS" = "1" ]; then pip install -r requirements-extras.txt; fi

# Install Chromium for Playwright (~150 MB). System deps are already installed
# above. To skip: build with --build-arg WITH_BROWSER=0.
ARG WITH_BROWSER=1
RUN if [ "$WITH_BROWSER" = "1" ]; then \
      playwright install chromium; \
    fi

# Node 20 + Claude Code CLI — used by the claude_code primitive for real
# code editing (multi-file, runs tests, iterates). Authenticates via
# either ANTHROPIC_API_KEY or a mounted ~/.claude/ folder (Pro/Max
# subscription). Skip with --build-arg WITH_CLAUDE_CODE=0.
ARG WITH_CLAUDE_CODE=1
RUN if [ "$WITH_CLAUDE_CODE" = "1" ]; then \
      curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
      apt-get install -y --no-install-recommends nodejs && \
      npm install -g @anthropic-ai/claude-code && \
      rm -rf /var/lib/apt/lists/*; \
    fi
# Configure git inside the container so commits and PRs work non-interactively.
RUN git config --global user.email "omnime-bot@users.noreply.github.com" && \
    git config --global user.name "OMNIME Bot" 2>/dev/null || \
    (apt-get update && apt-get install -y --no-install-recommends git && \
     git config --global user.email "omnime-bot@users.noreply.github.com" && \
     git config --global user.name "OMNIME Bot")

COPY src/ ./src/
COPY prompts/ ./prompts/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/

RUN mkdir -p data/exports data/backups data/uploads

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

CMD ["python", "-m", "src.main"]
