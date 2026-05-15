FROM node:20-slim AS web-builder

WORKDIR /build/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV AUDIO2TEXT_WORKSPACE_DIR=/app/workspace
ENV PLAYWRIGHT_BROWSERS_PATH=/app/workspace/runtime/playwright-browsers
ENV AUDIO2TEXT_API_HOST=0.0.0.0
ENV AUDIO2TEXT_DEPLOYMENT_MODE=cloud_preview
ENV AUDIO2TEXT_PUBLIC_PREVIEW_MODE=1
ENV AUDIO2TEXT_ALLOW_DEGRADED_START=1
ENV AUDIO2TEXT_MODEL_PATH=/app/workspace/runtime/models/faster-whisper/medium
ENV AUDIO2TEXT_FFMPEG_PATH=/usr/bin/ffmpeg

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

COPY app ./app
COPY scripts ./scripts
COPY audio2text.settings.example.json ./audio2text.settings.example.json
COPY --from=web-builder /build/frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["python", "-m", "scripts.start_api"]
