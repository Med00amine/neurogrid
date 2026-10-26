# syntax=docker/dockerfile:1
# One image runs both the API (which also serves the built UI) and the worker; the command decides which.

# ---- stage 1: build the React UI ------------------------------------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: the application -----------------------------------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --from=ui /ui/dist ./static
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data/uploads && chown -R app:app /data /app
ENV DATA_DIR=/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]
