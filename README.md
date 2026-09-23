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

## Déployer sur Kubernetes (Helm)

Le chart est dans `helm/ollama-agent/`. Construire et pousser l'image dans un registry accessible au cluster :

```bash
docker build -t <registry>/ollama-agent:0.1.0 . && docker push <registry>/ollama-agent:0.1.0
```

**Ollama déjà disponible** (dans le cluster ou ailleurs) :

```bash
helm install ollama-agent ./helm/ollama-agent \
  --set image.repository=<registry>/ollama-agent \
  --set ollamaUrl=http://<hôte-ollama>:11434/api/chat
```

**Ollama déployé par le chart** (PVC de 50 Gi pour les modèles, le modèle est tiré au premier démarrage) :

```bash
helm install ollama-agent ./helm/ollama-agent \
  --set image.repository=<registry>/ollama-agent \
  --set ollama.enabled=true
# GPU : --set 'ollama.resources.limits.nvidia\.com/gpu=1'
```

Puis `kubectl port-forward svc/ollama-agent 8124:8124` → `http://localhost:8124`, ou activer `ingress.enabled` (penser à désactiver le buffering du proxy pour le streaming SSE, voir `values.yaml`).

`replicaCount` doit rester à 1 : l'historique de conversation est en mémoire dans le pod (le chart refuse une valeur > 1).

### Sur OpenShift

Le chart fonctionne tel quel sous la SCC `restricted` (UID aléatoire non-root, aucun `runAsUser` imposé). Exposer l'appli avec une **Route** plutôt qu'un Ingress (timeout HAProxy relevé à 10 min pour le streaming) :

```bash
helm install ollama-agent ./helm/ollama-agent -n <projet> \
  --set image.repository=image-registry.openshift-image-registry.svc:5000/<projet>/ollama-agent \
  --set image.tag=latest \
  --set route.enabled=true \
  --set ollamaUrl=http://<service-ollama>:11434/api/chat
oc get route ollama-agent -n <projet>
```

Si l'appli est déjà déployée à la main (`oc new-app`, manifests…), Helm refusera de créer des objets portant le même nom : installer sous un autre nom de release, ou supprimer d'abord les objets existants (`oc delete deploy,svc,route -l app=ollama-agent` — vérifier le sélecteur avec `oc get all --show-labels`).
