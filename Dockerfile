FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/lerim-dev/lerim-cli

# Install curl (healthcheck), ripgrep, and Node.js (for Codex CLI)
RUN apt-get update && apt-get install -y --no-install-recommends curl ripgrep nodejs npm && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install Codex CLI globally (filesystem sub-agent for the OAI agent)
RUN npm install -g @openai/codex

# Install lerim from local source
COPY . /build
RUN pip install --no-cache-dir /build && rm -rf /build

# Dashboard assets for the built-in web UI
COPY dashboard/ /opt/lerim/dashboard/

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["lerim", "serve"]
