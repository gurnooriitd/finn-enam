# FINN — single-container, snapshot-first deploy.
# No network egress or API keys needed at runtime: serves the committed
# SQLite snapshot + prebuilt web/dist. FINN_MODE=snapshot forces
# rules-only (no LLM) and disables live refresh.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY data/finn.db data/finn.db
COPY data/portfolio.json data/portfolio.json
COPY web/dist/ web/dist/

ENV FINN_MODE=snapshot \
    LLM_ENABLED=0 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
