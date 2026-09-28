# The API server and the built review app in one image. The model does not run here: agents
# call Ollama on the host (OLLAMA_HOST), or Gemini when the model name says so.
#
#   docker build -t mycroft-accountability .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env mycroft-accountability
#
# Publish the port on 127.0.0.1 only. This is a research prototype with unauthenticated routes
# (see the Honest Ledger's audit-criticals); it must not be reachable from a network. .env is
# passed at run time and is never copied into the image (.dockerignore).

# ── Stage 1: build the review app (web/frontend -> dist/) ───────────────────────
FROM node:20-slim AS frontend
WORKDIR /app/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

# ── Stage 2: the Python server ───────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OLLAMA_HOST=http://host.docker.internal:11434

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /app/web/frontend/dist ./web/frontend/dist

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000"]
