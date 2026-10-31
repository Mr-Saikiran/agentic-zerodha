FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY docker-entrypoint.py .

# Users' keys and Kite tokens live in their browser cookies, so nothing here needs to persist.
RUN mkdir -p /data && useradd --create-home appuser

EXPOSE 8000
# Runs a single uvicorn worker, as appuser when started as root (see docker-entrypoint.py).
CMD ["python", "docker-entrypoint.py"]
