FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/lerim-dev/lerim-cli

# Install curl (healthcheck) and ripgrep
RUN apt-get update && apt-get install -y --no-install-recommends curl ripgrep && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install lerim from local source
COPY . /build
RUN pip install --no-cache-dir /build && rm -rf /build

# Pre-download the fastembed model so it's cached in the image
# (the container has a read-only /tmp tmpfs that's too small for model downloads)
ENV FASTEMBED_CACHE_PATH=/opt/lerim/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

# Dashboard assets for the built-in web UI
COPY dashboard/ /opt/lerim/dashboard/

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["lerim", "serve"]
