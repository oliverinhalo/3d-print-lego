# Build the frontend, then serve everything from one Python process.
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY run.py ./
COPY --from=frontend /build/dist ./frontend/dist

# Data (catalogue, LDraw library, cache, job output) lives on a volume so it
# survives a rebuild — re-downloading 150 MB on every deploy would be rude.
VOLUME ["/app/data"]

ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1 \
    # Download the catalogue and parts library on first start, so that
    # `docker compose up` is the entire install with no follow-up commands.
    AUTO_BOOTSTRAP=true

EXPOSE 8000

# start-period is long because the first boot downloads ~150 MB before the
# server binds; being marked unhealthy during that would restart-loop us.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15m --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["python", "run.py"]
