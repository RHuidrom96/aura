FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/mpl

WORKDIR /app

# System deps for matplotlib/numpy wheels are already bundled; keep image slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable data dir for SQLite + the generated secret (when DATABASE_URL is not set).
RUN mkdir -p /app/data && chmod -R 777 /app/data

# Hugging Face Spaces expects the app on 7860; other hosts pass $PORT.
ENV PORT=7860
EXPOSE 7860

# 1 worker is safest with SQLite; raise --workers when using Postgres (DATABASE_URL).
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-7860} --workers ${WEB_CONCURRENCY:-1} --timeout 180
