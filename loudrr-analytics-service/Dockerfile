# loudrr-analytics — crawl worker (Coolify deploy).
# Runs the smart-set following-graph crawl to completion against Postgres, then exits 0.
# Later this same image upgrades into the official microservice (scoring API + cron).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# curl: the API container's healthcheck (GET /health) needs an HTTP client — slim has neither
# curl nor wget. (asyncpg / scientific wheels are prebuilt, so no other system deps needed.)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# One image, five modes (set RUN_MODE in Coolify):
#   RUN_MODE=api          -> public scoring + mindshare + engagement API (uvicorn, port 8000)
#   RUN_MODE=mindshare    -> hourly mindshare worker (ingest -> score -> aggregate -> movers)
#   RUN_MODE=engagement   -> daily smart-engagement worker (smart-set timelines -> edges)
#   RUN_MODE=twitterscore -> one-shot: scrape TwitterScore categories for all handles, then exit
#   default/crawl         -> crawl the smart-set following graph to completion, then exit 0
CMD ["sh", "-c", "if [ \"$RUN_MODE\" = \"api\" ]; then exec uvicorn app.main:app --host 0.0.0.0 --port 8000; elif [ \"$RUN_MODE\" = \"mindshare\" ]; then exec python -m app.mindshare.worker; elif [ \"$RUN_MODE\" = \"engagement\" ]; then exec python -m app.engagement.worker; elif [ \"$RUN_MODE\" = \"twitterscore\" ]; then exec python -m scripts.scrape_twitterscore_prod; else exec python -m scripts.run_until_done; fi"]
