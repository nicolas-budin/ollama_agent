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

# requirements-dev.txt has: pytest, pytest-asyncio (kept out of requirements.txt
# so the Docker runtime image doesn't carry test-only deps)
pip install -r requirements-dev.txt
pytest -v
```

### Frontend (Node/React/Vite)

```bash
cd frontend
npm install
npm run dev      # dev server on :5173, proxies /api/* to :8124 (see vite.config.js)
npm run build    # outputs to frontend/dist/, served by FastAPI in prod
npm run lint     # oxlint
npm test         # vitest run — parseEvent.test.js and App.test.jsx
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
- `POST /api/reset` clears `_history` back to just the system prompt (same lock as `/api/chat`, so it can't race a stream in progress) — backs the "Nouvelle conversation" button in the frontend. Exists because `_history` otherwise grows unbounded for the life of the process (Ollama is stateless, so every turn resends the full history).
- `StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False)` is mounted at `/` **last**, after `/api/chat`/`/api/reset` — mount order matters here: an earlier mount at `/` would shadow the API routes.

### No tool-sandboxing concerns

The Claude version has a documented footgun around `ClaudeAgentOptions.allowed_tools` not actually restricting the CLI's tool set. **This doesn't apply here** — Ollama is a plain local inference server with no tool-calling/agentic loop in play in this app, so there's no equivalent hardening needed.

### Frontend SSE parsing (`frontend/src/App.jsx`)

Identical to the reference project's approach and copied near-verbatim: the browser can't use the native `EventSource` API (GET-only), so `App.jsx` manually reads `fetch()`'s `ReadableStream` and parses SSE framing by hand, normalizing `sse-starlette`'s `\r\n` line endings before splitting on blank lines. `parseEvent` is exported for isolated unit testing (`parseEvent.test.js`, ported from the reference project — it's a generic SSE parser, backend-agnostic).

The one visible difference from the reference UI: the `done` event has no cost (Ollama is local/free), so the meta line shows `Durée : Xms · Y tokens` instead of a `$` cost figure.

### `main.py`

A standalone one-shot reference script (non-streaming, single-turn) — the Ollama-side analogue of the reference project's `claude_demo.py`. Not wired into the web app; useful as a minimal example of calling Ollama directly with `requests`.

### Model

`MODEL` in `ollama_client.py` reads `OLLAMA_MODEL` (default `"gemma4:26b"`, matching what's actually pulled locally — `ollama list`). Same pattern as `OLLAMA_URL`/`OLLAMA_HOST` env vars: needed so the Docker/OpenShift images aren't hardcoded to one machine's setup.

### Tests (`tests/`, `frontend/src/*.test.*`)

Both mock the network entirely — no live Ollama, no real `fetch` — so they run the same locally and in CI (`.github/workflows/ci.yml`, on push to `main` and on every PR):

- `tests/test_ollama_client.py` monkeypatches `ollama_client._client` with a fake `httpx.AsyncClient` whose `.stream()` replays canned NDJSON lines (Ollama's actual wire format), asserting both the yielded `{"type": "text"/"done", ...}` chunks and the `_history` mutations. This is the regression guard for the NDJSON→internal-chunk translation, the one piece of logic that has no equivalent in the reference Claude project.
- `tests/test_web_app.py` monkeypatches `ollama_client.stream_chat` directly (an async generator function, not a client object — simpler to fake than the reference project's `ClaudeSDKClient`), and asserts on the SSE body via `TestClient`.
- `frontend/src/parseEvent.test.js` / `App.test.jsx`: ported from the reference project; `App.test.jsx`'s `done`-event assertions were adapted to this project's actual meta line (`Durée : Xms · Y tokens`, no `cost_usd`).
- `web_app.py`'s `StaticFiles` mount uses `check_dir=False`: `frontend/dist` doesn't exist in a fresh checkout (gitignored, built via `npm run build`), and the backend CI job never builds the frontend — without this flag, just `import web_app` would crash before any test ran.

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
- Steps: `oc login` with the `github-ci` service account token (`secrets.OPENSHIFT_TOKEN`, `vars.OPENSHIFT_SERVER`, job-local `KUBECONFIG`) → `oc start-build --from-dir=.` (wait for a final phase) → `oc tag <is>@<build digest> <is>:<short sha>` → rewrite the single `  tag:` line of `helm/ollama-agent/values-openshift.yaml` and push that commit to `main` (retry on rejection). Argo CD then rolls the pod. The SA's minimal Role (builds + imagestreamtags, no Deployment access) lives in `openshift/ci-serviceaccount.yaml`.
- GitOps on purpose: the CI never touches the Deployment. No `image.openshift.io/triggers` (Argo CD `selfHeal` would fight it) and no `:latest` + `rollout restart` (Git wouldn't say what runs). Tags are immutable, hence `pullPolicy: IfNotPresent`. The build `paths` filter excludes `helm/`, so the CI's own commit doesn't retrigger it. Rollback = `git revert` the `ci: déploie l'image …` commit.
- The repo is public: fork-PR workflows must require approval, since a self-hosted runner executes workflow code on the Mac. The workflow has `contents: write` (to push the tag commit); if `main` gets branch protection, `github-actions[bot]` must be allowed to push.

