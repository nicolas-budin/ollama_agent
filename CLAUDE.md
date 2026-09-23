# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo chat app that talks to a **local Ollama model** (`gemma4:26b`) over Ollama's HTTP API (`http://localhost:11434/api/chat`) — no cloud API, no API key. Backend is FastAPI (Python), frontend is React (Vite). There's no user-account layer: it's single-tenant, one shared conversation history for every visitor, kept in server memory (Ollama itself is stateless — every request must resend the full message history).

This project mirrors the architecture of a sibling demo (`~/github/agent`) that talks to Claude via the Claude Agent SDK — same FastAPI + React + SSE-streaming shape, swapped to a different backend model provider. See "Architecture" below for what differs.

## Commands

### Backend (Python)

```bash
source .venv/bin/activate

# requirements.txt has: requests, fastapi, uvicorn[standard], sse-starlette, httpx
pip install -r requirements.txt

# Ollama must be running locally with the model pulled:
ollama list                 # confirm gemma4:26b is present
ollama serve                # only if it isn't already running as a service

# Run the server (serves API + built frontend from frontend/dist)
uvicorn web_app:app --reload --port 8124
```

No test suite yet (deliberately skipped for the first pass — see CLAUDE.md history / ask before assuming one exists).

### Frontend (Node/React/Vite)

```bash
cd frontend
npm install
npm run dev      # dev server on :5173, proxies /api/* to :8124 (see vite.config.js)
npm run build    # outputs to frontend/dist/, served by FastAPI in prod
npm run lint     # oxlint
```

**Two-server dev workflow**: run `uvicorn` (backend, :8124) and `npm run dev` (frontend, :5173) in parallel terminals. For a single-server setup, run `npm run build` then serve everything through `uvicorn` alone — the backend mounts `frontend/dist` directly. Note: `StaticFiles` requires `frontend/dist` to exist at import time, so build the frontend at least once before starting `uvicorn` without `--reload`-driven frontend changes.

## Architecture

### Ollama has no server-side session — we hold the history instead

Unlike `ClaudeSDKClient` (which manages multi-turn state internally over a persistent CLI connection), Ollama's `/api/chat` is stateless: every call must include the *entire* message history to get multi-turn behavior. `ollama_client.py` owns:

- `_history: list[dict]` — a single module-level list (`role: system/user/assistant`), shared across every visitor, seeded with `SYSTEM_PROMPT`. This is the direct analogue of the single shared `ClaudeSDKClient` in the reference project: one conversation for everyone, no per-user isolation.
- `_client: httpx.AsyncClient | None` — created lazily on first message (`get_or_create_client()`), closed on shutdown (`disconnect_client()`, called from `web_app.py`'s `lifespan`). Same lazy-singleton shape as the Claude version, just pooling HTTP connections instead of holding a CLI subprocess.
- `get_lock()` / module-level `_lock = asyncio.Lock()` — same contract as the Claude version: the caller (`web_app.chat()`) holds the lock for the whole turn, `stream_chat()` does **not** re-acquire it. This serializes turns (no two requests interleaving on `_history`) and prevents double-mutating the shared history concurrently.
- `stream_chat(message)` — async generator: appends the user message to `_history`, opens a streaming POST to Ollama with the full `_history` and `"stream": true`, parses Ollama's **NDJSON** response line by line (`{"message": {"content": "..."}, "done": false}` chunks, then a final `"done": true` object carrying `total_duration`, `eval_count`, etc.), yields `{"type": "text", ...}` per chunk and `{"type": "done", "duration_ms": ..., "eval_count": ...}` at the end, appending the full assistant reply to `_history` at that point.

### Backend request flow (`web_app.py`)

- `lifespan` is thin: just `ollama_client.disconnect_client()` at shutdown.
- `POST /api/chat` acquires `ollama_client.get_lock()`, iterates `ollama_client.stream_chat(message)`, and re-emits each chunk as a **Server-Sent Event** via `sse-starlette`'s `EventSourceResponse` — translating Ollama's raw NDJSON stream into the same SSE contract the frontend expects (`text`, `done`, `error`). This translation step is the main thing this backend does that the Claude version's backend doesn't: Ollama doesn't speak SSE natively.
- `StaticFiles(directory=FRONTEND_DIST, html=True)` is mounted at `/` **last**, after `/api/chat` — mount order matters here: an earlier mount at `/` would shadow the API route.

### No tool-sandboxing concerns

The Claude version has a documented footgun around `ClaudeAgentOptions.allowed_tools` not actually restricting the CLI's tool set. **This doesn't apply here** — Ollama is a plain local inference server with no tool-calling/agentic loop in play in this app, so there's no equivalent hardening needed.

### Frontend SSE parsing (`frontend/src/App.jsx`)

Identical to the reference project's approach and copied near-verbatim: the browser can't use the native `EventSource` API (GET-only), so `App.jsx` manually reads `fetch()`'s `ReadableStream` and parses SSE framing by hand, normalizing `sse-starlette`'s `\r\n` line endings before splitting on blank lines. `parseEvent` is exported for isolated unit testing (no test file wired up yet in this repo, unlike the reference project's `parseEvent.test.js`).

The one visible difference from the reference UI: the `done` event has no cost (Ollama is local/free), so the meta line shows `Durée : Xms · Y tokens` instead of a `$` cost figure.

### `main.py`

A standalone one-shot reference script (non-streaming, single-turn) — the Ollama-side analogue of the reference project's `claude_demo.py`. Not wired into the web app; useful as a minimal example of calling Ollama directly with `requests`.

### Model

`MODEL = "gemma4:26b"` is hardcoded in `ollama_client.py`, matching what's actually pulled locally (`ollama list`). Swap it there if you pull a different model.

### Deployment docs

`docs/deploiement-openshift.md` (French) is the end-to-end overview of the OpenShift chain — image build, OpenShift specifics, Helm, Argo CD, CI — and the reasoning/alternatives behind each choice. Keep it in sync when any of those change; per-folder READMEs hold the detailed commands.

### Helm chart (`helm/ollama-agent/`)

OpenShift-only chart (documented in `helm/README.md`, in French) that replaces `openshift/deployment.yaml`; the BuildConfig/ImageStream in `openshift/buildconfig.yaml` stay outside Helm. Docs for it talk about OpenShift only — keep Kubernetes-generic options (Ingress, `kubectl`) out. Key constraints:

- `replicaCount` is forced to 1 (template `fail`s otherwise) and the Deployment uses `strategy: Recreate` — the shared `_history` lives in process memory, so two pods would mean two diverging conversations.
- `OLLAMA_URL` comes from `ollamaUrl`. The chart deliberately does **not** deploy Ollama: it runs outside the cluster (on CRC, on the Mac) and must be reachable from the pods.
- No `/health` endpoint exists, so the app's probes hit `GET /` (the React `index.html`).
- Exposure is a `route.openshift.io/v1` Route (enabled by default) with `haproxy.router.openshift.io/timeout` (default 30s would cut long streams).
- `values-openshift.yaml` mirrors `openshift/deployment.yaml` (the CRC setup: internal-registry image, `OLLAMA_URL` on the Mac's LAN IP, plain-HTTP Route).
- No `runAsUser` is set so the restricted SCC can assign a random UID (the app writes nothing to disk).

### CI (`.github/workflows/build.yml`, documented in `openshift/CI.md`)

- Runs on a **self-hosted runner on the Mac** (`runs-on: [self-hosted, macOS, crc]`): CRC isn't reachable from the internet, so GitHub-hosted runners and BuildConfig webhooks can't be used. Custom label `crc` is declared in `.github/actionlint.yaml`.
- Triggered on push to `main` touching app code (`Dockerfile`, `*.py`, `requirements.txt`, `frontend/**`…) or manually; changes under `helm/`/`argocd/` are Argo CD's job, not a build.
- Steps: `oc login` with the `github-ci` service account token (`secrets.OPENSHIFT_TOKEN`, `vars.OPENSHIFT_SERVER`, job-local `KUBECONFIG`) → `oc start-build --from-dir=. --follow --wait` → `oc rollout restart` + `rollout status`. The SA and its minimal Role live in `openshift/ci-serviceaccount.yaml`.
- Deliberately **no** `image.openshift.io/triggers` on the Deployment: Argo CD `selfHeal` would revert the image field and fight the trigger. `rollout restart` only adds a pod-template annotation Argo CD ignores.
- The repo is public: fork-PR workflows must require approval, since a self-hosted runner executes workflow code on the Mac.

