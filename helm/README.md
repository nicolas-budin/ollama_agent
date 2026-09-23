# Chart Helm `ollama-agent` (OpenShift)

Le chart `helm/ollama-agent/` installe l'appli sur OpenShift avec une seule commande (`helm install`), au lieu d'appliquer des YAML à la main.

Sur le cluster CRC, il **remplace `openshift/deployment.yaml`** (Deployment + Service + Route). Le **build ne change pas** : on garde `openshift/buildconfig.yaml` et `oc start-build` (voir [`openshift/README.md`](../openshift/README.md)).

**Ollama n'est pas déployé par le chart** : il tourne hors du cluster (sur le Mac pour CRC), et l'appli le joint via `ollamaUrl`.

## Pourquoi un chart Helm

| Avec `oc apply -f deployment.yaml` | Avec le chart |
|---|---|
| L'IP d'Ollama est codée en dur dans le YAML ; on la change avec `oc set env`, qui n'est enregistré nulle part | L'IP est un réglage (`ollamaUrl`), qu'on change avec `helm upgrade --set ollamaUrl=…` |
| Pas d'historique des déploiements | `helm history` / `helm rollback` pour revenir à la version précédente |
| Un seul environnement (CRC) | Même chart pour plusieurs clusters OpenShift, via un fichier de valeurs par cluster |
| Supprimer l'appli = supprimer chaque objet à la main | `helm uninstall ollama-agent` |

## Contenu du chart

### `Chart.yaml`, `.helmignore`
Carte d'identité du chart (nom `ollama-agent`, version `0.1.0`) et fichiers à ignorer au packaging.

### `values.yaml` : les réglages par défaut
Tous les paramètres modifiables, commentés. Les principaux :

| Réglage | Défaut | Rôle |
|---|---|---|
| `image.repository` / `image.tag` / `image.pullPolicy` | `ollama-agent` / `0.1.0` / `IfNotPresent` | Image de l'appli (construite par le BuildConfig depuis le `Dockerfile`) |
| `model` | `gemma4:26b` | Devient la variable `OLLAMA_MODEL` |
| `ollamaUrl` | `http://ollama:11434/api/chat` | Devient `OLLAMA_URL` (Ollama tourne hors du cluster) |
| `replicaCount` | `1` | **Ne doit pas dépasser 1** (voir plus bas) |
| `service.port` | `8124` | Port du Service |
| `route.enabled` / `route.timeout` / `route.tls.*` | `true` / `10m` / HTTPS edge | Route OpenShift |
| `livenessProbe` / `readinessProbe` | `GET /` | Vérifications de santé |
| `resources`, `nodeSelector`, `tolerations`, `affinity` | vides | Réglages de placement et de ressources |

### `values-openshift.yaml` : les réglages du cluster CRC actuel
Surcharge `values.yaml` pour reproduire `openshift/deployment.yaml`. Voir la comparaison détaillée plus bas.

### `templates/deployment.yaml` : l'appli
- Lance le conteneur FastAPI + React sur le port `8124`, avec `OLLAMA_URL` et `OLLAMA_MODEL`.
- **`replicaCount` bloqué à 1** : l'historique de conversation (`_history` dans `ollama_client.py`) vit dans la mémoire du process. Avec deux pods, chaque pod aurait son propre historique et le modèle « oublierait » la moitié de la conversation selon le pod qui répond. Le template refuse donc toute valeur > 1 avec un message explicite.
- **`strategy: Recreate`** : pendant une mise à jour, l'ancien pod est arrêté avant le démarrage du nouveau, pour ne jamais avoir deux pods en même temps. Contrepartie : quelques secondes de coupure à chaque mise à jour.
- Aucun `runAsUser` n'est imposé : OpenShift peut attribuer son UID aléatoire (SCC `restricted`). L'appli n'écrit rien sur le disque, donc ça fonctionne sans changer l'image.

### `templates/service.yaml`
Adresse interne stable (`ollama-agent:8124`) vers le pod de l'appli.

### `templates/route.yaml` : exposition hors du cluster
- Annotation `haproxy.router.openshift.io/timeout: 10m`. Par défaut, le routeur OpenShift coupe une connexion au bout de **30 s**, alors qu'une réponse streamée de `gemma4:26b` peut durer plus longtemps et serait coupée au milieu.
- TLS configurable (HTTPS edge par défaut ; désactivé dans `values-openshift.yaml` pour garder le comportement actuel en HTTP).
- `host` vide : OpenShift génère le même nom qu'aujourd'hui (`ollama-agent-ollama-agent.apps-crc.testing`).

### `templates/serviceaccount.yaml`
ServiceAccount dédié, sans token monté (l'appli n'appelle pas l'API du cluster). Désactivé dans `values-openshift.yaml`, comme aujourd'hui.

### `templates/_helpers.tpl`, `templates/NOTES.txt`
Fonctions communes (noms, labels) et message affiché après `helm install` (commande pour obtenir l'URL de la Route).

## Comparaison avec `openshift/deployment.yaml`

| Élément | `deployment.yaml` | Chart + `values-openshift.yaml` |
|---|---|---|
| Image | `image-registry…/ollama-agent/ollama-agent:latest` | identique |
| `OLLAMA_URL` | `http://192.168.1.119:11434/api/chat` | identique |
| `OLLAMA_MODEL` | non défini (défaut du code : `gemma4:26b`) | `gemma4:26b`, explicite |
| Probes | `GET /`, délais 3 s / 10 s | identiques |
| Noms des objets | `ollama-agent` | identiques, donc **même URL de Route** |
| Route | HTTP, timeout 30 s | HTTP, **timeout 10 min** |
| Stratégie de mise à jour | `RollingUpdate` (2 pods pendant un instant) | **`Recreate`** (jamais 2 pods) |
| `imagePullPolicy` | défaut (`Always` pour `:latest`) | `Always`, explicite |
| Nombre de replicas | 1 (rien n'empêche de monter) | 1, **bloqué** |
| Labels | `app: ollama-agent` | labels Helm standard |

## Migration depuis `oc apply`

Helm ne peut pas reprendre des objets qu'il n'a pas créés, et le sélecteur d'un Deployment ne peut pas être modifié. Il faut donc supprimer les trois objets avant d'installer, ce qui coupe l'appli quelques secondes. L'URL de la Route ne change pas.

```bash
oc project ollama-agent
oc delete deployment/ollama-agent service/ollama-agent route/ollama-agent
helm install ollama-agent ./helm/ollama-agent -f helm/ollama-agent/values-openshift.yaml
oc get route ollama-agent -o jsonpath='{.spec.host}'
```

**Ne pas supprimer** le `BuildConfig` ni l'`ImageStream`.

## Au quotidien

```bash
# Nouveau code : automatique à chaque push sur main (voir openshift/CI.md), ou à la main :
oc start-build ollama-agent --from-dir=. --follow
oc rollout restart deployment/ollama-agent

# L'IP du Mac a changé (remplace `oc set env`, qui serait écrasé au prochain upgrade)
helm upgrade ollama-agent ./helm/ollama-agent -f helm/ollama-agent/values-openshift.yaml \
  --set ollamaUrl=http://<nouvelle-ip>:11434/api/chat

# Revenir en arrière
helm rollback ollama-agent                                              # version précédente
helm uninstall ollama-agent && oc apply -f openshift/deployment.yaml    # retour aux YAML
```

## Vérifier le chart sans l'installer

```bash
helm lint helm/ollama-agent -f helm/ollama-agent/values-openshift.yaml
helm template ollama-agent helm/ollama-agent -f helm/ollama-agent/values-openshift.yaml
```
