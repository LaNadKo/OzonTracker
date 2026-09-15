FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

# The local resolver intermittently refuses foreign lookups for minutes at a
# time; retry across the failure window instead of failing the whole build.
RUN s=1; for attempt in 1 2 3 4 5; do \
        pip install --no-cache-dir . && s=0 && break; \
        echo "pip attempt $attempt failed, retrying in 20s"; \
        sleep 20; \
    done; exit $s

RUN mkdir -p /app/data

CMD ["python", "-m", "ozon_tracker_bot"]
