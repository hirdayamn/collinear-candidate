FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y \
    redis-server \
    sqlite3 \
    procps \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest pytest-asyncio redis psutil

RUN mkdir -p /app/analytics_service /app/scripts /app/tests /var/log/services

COPY src/analytics_service/ /app/analytics_service/
COPY src/scripts/ /app/scripts/
COPY tests/ /app/tests/

COPY setup_db.py /app/setup_db.py

RUN python3 /app/setup_db.py

RUN chmod -R 755 /app /var/log/services

CMD redis-server --daemonize yes && tail -f /dev/null