import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

import ollama_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Build React : `cd frontend && npm run build`
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await ollama_client.disconnect_client()


app = FastAPI(lifespan=lifespan)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message vide"}, status_code=400)

    async def event_stream():
        logger.info("Message reçu : %s", message)
        async with ollama_client.get_lock():
            try:
                async for chunk in ollama_client.stream_chat(message):
                    if chunk["type"] == "text":
                        yield {"event": "text", "data": chunk["content"]}
                    elif chunk["type"] == "done":
                        logger.info(
                            "Durée : %.0f ms | Tokens générés : %d",
                            chunk["duration_ms"],
                            chunk["eval_count"],
                        )
                        yield {
                            "event": "done",
                            "data": json.dumps(
                                {
                                    "duration_ms": chunk["duration_ms"],
                                    "eval_count": chunk["eval_count"],
                                }
                            ),
                        }
            except Exception as exc:  # noqa: BLE001 - remonter l'erreur au client web
                logger.exception("Erreur pendant la génération")
                yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_stream())


# Monté en dernier : sert le build React (index.html + assets),
# sans masquer la route /api/chat déclarée au-dessus.
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
