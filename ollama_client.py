import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# En Docker, "localhost" désigne le conteneur lui-même, pas l'hôte qui fait
# tourner Ollama — on rend donc l'URL surchargeable via l'environnement
# (ex. http://host.docker.internal:11434/api/chat, voir Dockerfile).
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:26b")
SYSTEM_PROMPT = "Réponds de façon brève et factuelle."

# Un unique historique de conversation partagé par tous les visiteurs
# (single-tenant, comme le _client unique côté Claude) : Ollama n'a pas de
# notion de session, donc c'est nous qui renvoyons tout l'historique à
# chaque appel.
_history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


def get_lock() -> asyncio.Lock:
    return _lock


def get_or_create_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=None)
        logger.info("Client HTTP Ollama créé")
    return _client


async def disconnect_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        logger.info("Client HTTP Ollama fermé")
        _client = None


async def stream_chat(message: str):
    """Envoie `message` à Ollama et streame les morceaux de réponse.

    Suppose que l'appelant tient déjà get_lock() (même contrat que
    agent.get_or_create_client() côté Claude) : la mutation de _history
    n'est pas task-safe sans ce verrou.
    """
    client = get_or_create_client()
    _history.append({"role": "user", "content": message})

    assistant_text = ""
    async with client.stream(
        "POST",
        OLLAMA_URL,
        json={"model": MODEL, "messages": _history, "stream": True},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            content = chunk.get("message", {}).get("content", "")
            if content:
                assistant_text += content
                yield {"type": "text", "content": content}
            if chunk.get("done"):
                _history.append({"role": "assistant", "content": assistant_text})
                yield {
                    "type": "done",
                    "duration_ms": chunk.get("total_duration", 0) / 1e6,
                    "eval_count": chunk.get("eval_count", 0),
                }
