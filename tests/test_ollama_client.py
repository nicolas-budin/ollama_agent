import json

import ollama_client


class FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStreamContext:
    def __init__(self, lines, calls, method, url, json_body):
        self._lines = lines
        calls.append({"method": method, "url": url, "json": json_body})

    async def __aenter__(self):
        return FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc_info):
        return False


class FakeAsyncClient:
    def __init__(self, lines):
        self._lines = lines
        self.calls = []

    def stream(self, method, url, json=None):
        return FakeStreamContext(self._lines, self.calls, method, url, json)


def test_get_or_create_client_reuses_the_same_client(monkeypatch):
    created = []

    class FakeHttpxAsyncClient:
        def __init__(self, timeout=None):
            created.append(self)

    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", FakeHttpxAsyncClient)
    monkeypatch.setattr(ollama_client, "_client", None)

    client_a = ollama_client.get_or_create_client()
    client_b = ollama_client.get_or_create_client()

    assert client_a is client_b, "pas de reconnexion une fois le client déjà créé"
    assert len(created) == 1


async def test_stream_chat_parses_ndjson_and_updates_history(monkeypatch):
    """Non-régression sur la partie la plus spécifique de ce projet : Ollama ne
    renvoie pas du SSE mais du NDJSON, avec un objet final "done": true portant
    les stats (total_duration en ns, eval_count) - voir web_app.py qui retraduit
    ça en SSE pour le navigateur."""
    lines = [
        json.dumps({"message": {"content": "Bon"}, "done": False}),
        json.dumps({"message": {"content": "jour"}, "done": False}),
        json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "total_duration": 123_000_000,
                "eval_count": 42,
            }
        ),
    ]
    fake_client = FakeAsyncClient(lines)
    monkeypatch.setattr(ollama_client, "_client", fake_client)
    monkeypatch.setattr(ollama_client, "_history", [{"role": "system", "content": "test"}])

    chunks = [chunk async for chunk in ollama_client.stream_chat("Bonjour")]

    assert chunks == [
        {"type": "text", "content": "Bon"},
        {"type": "text", "content": "jour"},
        {"type": "done", "duration_ms": 123.0, "eval_count": 42},
    ]
    assert ollama_client._history[-2] == {"role": "user", "content": "Bonjour"}
    assert ollama_client._history[-1] == {"role": "assistant", "content": "Bonjour"}

    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"] == ollama_client.OLLAMA_URL
    assert fake_client.calls[0]["json"]["model"] == ollama_client.MODEL
    assert fake_client.calls[0]["json"]["stream"] is True


async def test_stream_chat_skips_empty_lines(monkeypatch):
    """Ollama peut envoyer des lignes vides dans le flux NDJSON (keep-alive) :
    doivent être ignorées plutôt que de faire planter json.loads."""
    lines = [
        "",
        json.dumps({"message": {"content": "Hi"}, "done": False}),
        "",
        json.dumps({"message": {"content": ""}, "done": True, "total_duration": 0, "eval_count": 1}),
    ]
    fake_client = FakeAsyncClient(lines)
    monkeypatch.setattr(ollama_client, "_client", fake_client)
    monkeypatch.setattr(ollama_client, "_history", [])

    chunks = [chunk async for chunk in ollama_client.stream_chat("Hi")]

    assert chunks == [
        {"type": "text", "content": "Hi"},
        {"type": "done", "duration_ms": 0.0, "eval_count": 1},
    ]
