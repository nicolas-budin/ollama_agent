from fastapi.testclient import TestClient

import ollama_client
import web_app


def make_fake_stream_chat(chunks):
    async def fake_stream_chat(message):
        for chunk in chunks:
            yield chunk

    return fake_stream_chat


def test_chat_empty_message_returns_400():
    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_chat_streams_text_and_done_events(monkeypatch):
    chunks = [
        {"type": "text", "content": "Bon"},
        {"type": "text", "content": "jour"},
        {"type": "done", "duration_ms": 123.0, "eval_count": 42},
    ]
    monkeypatch.setattr(ollama_client, "stream_chat", make_fake_stream_chat(chunks))

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Bonjour"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: text" in body
    assert "data: Bon" in body
    assert "data: jour" in body
    assert "event: done" in body
    assert '"duration_ms": 123.0' in body
    assert '"eval_count": 42' in body


def test_chat_forwards_message_to_stream_chat(monkeypatch):
    received = {}

    async def fake_stream_chat(message):
        received["message"] = message
        return
        yield  # pragma: no cover - jamais atteint, nécessaire pour un générateur async

    monkeypatch.setattr(ollama_client, "stream_chat", fake_stream_chat)

    client = TestClient(web_app.app)
    client.post("/api/chat", json={"message": "  Quelle heure est-il ?  "})

    assert received["message"] == "Quelle heure est-il ?"


def test_chat_streams_error_event_on_exception(monkeypatch):
    async def failing_stream_chat(message):
        raise RuntimeError("boom")
        yield  # pragma: no cover - jamais atteint, nécessaire pour un générateur async

    monkeypatch.setattr(ollama_client, "stream_chat", failing_stream_chat)

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "test"})

    assert "event: error" in resp.text
    assert "boom" in resp.text
