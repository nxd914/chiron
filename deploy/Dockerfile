FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libssl-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV EXECUTION_MODE=paper

RUN mkdir -p /app/data

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -m research.health_check --quiet 2>/dev/null || exit 0

CMD ["python3", "-m", "strategies.crypto.daemon"]
