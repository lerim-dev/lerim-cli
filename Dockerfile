FROM python:3.12-slim

# Install curl (for healthcheck) and Deno (for DSPy RLM)
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ripgrep && \
    curl -fsSL https://deno.land/install.sh | sh && \
    ln -s /root/.deno/bin/deno /usr/local/bin/deno && \
    apt-get purge -y unzip && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install lerim from local source
COPY . /build
RUN pip install --no-cache-dir /build && rm -rf /build

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["lerim", "serve"]
