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

# /data holds the cached Kite access token; mount a volume there to survive restarts.
RUN mkdir -p /data && useradd --create-home appuser

EXPOSE 8000
# Starts as root to chown the volume, then drops to appuser and runs a single uvicorn worker.
CMD ["python", "docker-entrypoint.py"]
