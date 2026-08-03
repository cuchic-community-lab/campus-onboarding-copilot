FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY . /app

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e '.[parsers]' \
    && python -m campus_copilot.cli build \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin campus \
    && mkdir -p /app/data/runtime \
    && chown -R campus:campus /app/data/runtime

USER campus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)); raise SystemExit(0 if data.get('status') == 'ok' and data.get('database_ready') else 1)"

CMD ["python", "-m", "campus_copilot.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
