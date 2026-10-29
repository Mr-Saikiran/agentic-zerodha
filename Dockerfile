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

# Holds the cached Kite access token; mount a volume here to survive restarts.
RUN mkdir -p /data && useradd --create-home appuser && chown appuser /data
USER appuser
VOLUME ["/data"]

EXPOSE 8000
# One worker only: the Kite session, chat history and research jobs live in process memory.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips '*'
