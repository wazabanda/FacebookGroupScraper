FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Debian's Chromium + matching chromedriver (works on amd64 and arm64)
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY scrape.py ./

# Session, cookies and output live in /data (mount a volume there)
ENV CHROME_BIN=/usr/bin/chromium \
    FB_PROFILE=/data/fb_profile \
    FB_COOKIES=/data/cookies.json \
    OUT=/data/posts-%Y%m%d-%H%M.json \
    HEADLESS=1
VOLUME /data

ENTRYPOINT ["uv", "run", "--frozen", "--no-sync", "scrape.py"]
