# Dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (minimal, since we use html.parser and not lxml)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core ./core
COPY fetchers ./fetchers
COPY monitor.py ./

# config.json and /data directory will be mounted from host
VOLUME ["/data"]

ENV CONFIG_PATH=/data/config.json \
    DB_PATH=/data/wishlist_state.sqlite3 \
    LOG_FILE=/data/wishlist_monitor.log \
    MODE=daemon

CMD ["python", "monitor.py"]
