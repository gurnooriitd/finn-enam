# Deploying FINN

The app is one Docker container with **no external network and no API keys** at
runtime: it serves the committed SQLite snapshot (`data/finn.db`) and the
prebuilt frontend (`web/dist/`). `FINN_MODE=snapshot` disables live refresh and
forces `LLM_ENABLED=0` (rules-only push pipeline).

## Render (recommended)

1. Push this repo to GitHub.
2. Render dashboard → **New → Web Service** → *Deploy from a Git repository* → pick the repo.
3. Settings:
   - **Runtime:** Docker (Render detects `Dockerfile` automatically)
   - **Instance type:** Free
   - **Environment variable:** `FINN_MODE=snapshot` (already the Dockerfile default; set it explicitly anyway)
4. Deploy. Render assigns the HTTPS URL; no port config needed (Render injects `PORT`, the Dockerfile honours it).

**Cold start:** on the free plan the service sleeps after ~15 min of inactivity;
the first request wakes it and takes ~30–60 s. The data snapshot is baked into
the image, so the wake-up serves full data immediately after boot.

**Updating data:** the snapshot is refreshed locally (`python scripts/refresh.py`)
and committed; a redeploy ships the new snapshot. Live refresh from a Render
datacenter IP is deliberately off — NSE commonly blocks datacenter IPs.

## Hugging Face Spaces (Docker) — alternative

1. Create a **Space** → SDK: **Docker** (Blank template).
2. Push the repo contents to the Space's git repo (HF Spaces builds the `Dockerfile`).
3. In Space **Settings → Variables**, add `FINN_MODE=snapshot`.
4. The app must listen on port **7860** on HF — the Dockerfile reads `PORT`, so
   set the Space variable `PORT=7860` (or edit the CMD default).
5. Hardware: free CPU basic is enough. Spaces sleep after 48h of inactivity;
   they wake on request like Render.

## Local container smoke test (if Docker is installed)

```bash
docker build -t finn .
docker run --rm -p 8000:8000 -e FINN_MODE=snapshot finn
curl localhost:8000/health          # {"status":"ok","mode":"snapshot",...}
```

## What the container needs

- `backend/` (FastAPI app), `data/finn.db` (snapshot), `data/portfolio.json`, `web/dist/` (prebuilt UI)
- `requirements.txt` only: fastapi, uvicorn, pydantic, python-dotenv, requests
- If the filesystem is read-only, the DB is copied to a writable temp path at startup.
