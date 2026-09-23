Une démo de chat qui parle à un modèle **Ollama local** (`gemma4:26b`) via l'API HTTP d'Ollama (`http://localhost:11434`), pas d'API cloud ni de clé requise. Backend FastAPI (streaming SSE), frontend React (Vite).

## Lancer en local (dev)

```bash
source .venv/bin/activate && pip install -r requirements.txt
uvicorn web_app:app --reload --port 8124

cd frontend && npm install && npm run dev   # http://localhost:5173
```

## Lancer avec Docker

Ollama tourne sur la machine hôte, pas dans le conteneur — `--add-host` route `host.docker.internal` vers l'hôte (déjà résolu par défaut sur Docker Desktop Mac/Windows, mais requis sur Linux) :

```bash
docker build -t ollama-agent .
docker run -d --add-host=host.docker.internal:host-gateway -p 8124:8124 ollama-agent
```

App disponible sur `http://localhost:8124`. Pour pointer vers un autre modèle ou une autre URL Ollama : `-e OLLAMA_MODEL=... -e OLLAMA_URL=...`.

## Déployer sur OpenShift

Voir [`openshift/README.md`](openshift/README.md) — build binaire via le registre interne, connectivité vers l'Ollama de l'hôte, et comment augmenter le disque/mémoire alloués à CRC.
