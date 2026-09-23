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

## Déployer avec Helm (OpenShift)

Le chart `helm/ollama-agent/` remplace `openshift/deployment.yaml`. Contenu, réglages, migration depuis `oc apply` et commandes du quotidien : voir [`helm/README.md`](helm/README.md).

## Déployer avec Argo CD (GitOps)

`argocd/application.yaml` fait piloter ce même chart Helm par Argo CD (sync automatique depuis `main`) à la place de `helm install`/`helm upgrade` manuels. Voir [`argocd/README.md`](argocd/README.md).

## CI : build automatique de l'image

À chaque push sur `main` qui touche le code, GitHub Actions reconstruit l'image dans le registre interne d'OpenShift et redémarre l'appli, via un runner installé sur le Mac qui fait tourner CRC. Voir [`openshift/CI.md`](openshift/CI.md).
