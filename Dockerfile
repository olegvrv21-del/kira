# Kira app image
FROM python:3.12-slim

# system deps: ripgrep (grep tool), git (git tool), curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ripgrep git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# install python deps first (better layer caching)
COPY requirements.txt requirements-embed.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy source
COPY . .

# runtime
EXPOSE 3000
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KIRA_SANDBOX=0

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:3000/healthz || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
