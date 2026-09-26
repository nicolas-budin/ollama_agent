# Déploiement sur OpenShift : vue d'ensemble

Ce document explique toute la chaîne qui mène du code source à l'appli en ligne sur le cluster OpenShift local (CRC), et **pourquoi** chaque choix a été fait. Les commandes détaillées sont dans les README de chaque dossier ; ce document y renvoie au fil du texte.

## La chaîne complète

```
 ┌──────────── Mac ──────────────────────────────────────────────────────────┐
 │                                                                           │
 │  git push main ──► GitHub ──► runner GitHub Actions (sur le Mac)          │
 │                      ▲            │ 1. oc start-build --from-dir=.        │
 │                      │            │ 2. oc tag … ollama-agent:<sha>        │
 │   3. commit du tag <sha> dans     │                                       │
 │      values-openshift.yaml ───────┘                                       │
 │   ┌──────── VM CRC (OpenShift) ▼───────────────────────────────────┐      │
 │   │  BuildConfig ──► ImageStream ollama-agent:<sha> (registre      │      │
 │   │                            interne)                            │      │
 │   │  Argo CD ──(chart Helm depuis Git)──► Deployment ──► Pod appli │      │
 │   │                                        Service ──► Route ◄──── navigateur
 │   └────────────────────────────────────────────│───────────────────┘      │
 │                                                │ OLLAMA_URL (IP LAN)      │
 │  Ollama (gemma4:26b) ◄─────────────────────────┘                          │
 └───────────────────────────────────────────────────────────────────────────┘
```

Il y a deux circuits indépendants :

| Ce qui change | Qui s'en occupe | Résultat |
|---|---|---|
| Le **code** (`*.py`, `frontend/`, `Dockerfile`…) | **GitHub Actions** + runner sur le Mac | nouvelle image avec un tag unique, écrit dans `helm/` par un commit |
| La **configuration** (`helm/`), y compris ce tag | **Argo CD** | le cluster est aligné sur Git, nouveau pod si l'image a changé |

Les deux circuits se rejoignent dans Git : la CI ne touche jamais directement au Deployment. **C'est Git qui décide de ce qui tourne**, image comprise.

## Schémas détaillés : la CI (runner) et le CD (Argo CD)

### La CI : le runner GitHub Actions sur le Mac

```
  toi                     GITHUB                                   MAC (runner + CRC)
  ───                     ──────                                   ──────────────────
                                                                    Runner.Listener
 git push main ───────►  repo                                       (label: crc)
 (touche *.py, frontend/,   │                                           │
  Dockerfile, requirements) │ push sur main + filtre "paths"            │
                            ▼                                           │
                         Actions : build.yml                            │
                         runs-on: [self-hosted, macOS, crc]             │
                            │                                           │
                            │   job en attente ◄──── le runner INTERROGE GitHub
                            │   (aucun port ouvert :  (connexion sortante, long-poll)
                            │    le Mac appelle GitHub, jamais l'inverse)
                            └──────────── job envoyé ──────────────────►│
                                                                        │
              ┌─────────────────────────────────────────────────────────┘
              ▼   étapes exécutées EN LOCAL sur le Mac
   1. actions/checkout                       (clone dans actions-runner/_work)
   2. oc login  ← secrets.OPENSHIFT_TOKEN    (SA "github-ci", droits minimaux,
                  vars.OPENSHIFT_SERVER       namespace ollama-agent seulement)
   3. oc start-build ollama-agent --from-dir=.  ──────────►  CRC : BuildConfig
                                                              construit le Dockerfile
                                                                   │
                                                                   ▼
                                                        ImageStream ollama-agent
                                                        (registre INTERNE d'OpenShift)
   4. oc tag ollama-agent@<digest> → ollama-agent:<sha7>   (tag unique, immuable)
   5. sed : image.tag: "<sha7>" dans helm/ollama-agent/values-openshift.yaml
   6. git commit "ci: déploie l'image ollama-agent:<sha7>"
      git push origin main ───────────────────────────────►  GITHUB (repo)
      (GITHUB_TOKEN, contents: write ; 3 tentatives si main a bougé)
```

### Le CD : Argo CD dans CRC (namespace `openshift-gitops`)

```
   GITHUB (repo, branche main)                       CRC / OpenShift
   ───────────────────────────                       ───────────────
   helm/ollama-agent/                                Argo CD
     templates/ (Deployment, Service, Route)         ┌──────────────────────────────────┐
     values.yaml                                     │ repo-server                      │
     values-openshift.yaml  ◄── image.tag: "<sha7>"  │   clone Git, exécute             │
                 │                                   │   "helm template" + values-openshift
                 │  ◄──── Argo CD INTERROGE Git ─────│   → manifestes YAML (état voulu) │
                 │        (sortant, ~3 min ou        │                                  │
                 │         refresh manuel)           │ application-controller           │
                 │                                   │   compare voulu  vs  état réel  │
                 │                                   │   du namespace ollama-agent      │
                 │                                   │                                  │
                 │                                   │   OutOfSync ? ──► sync AUTO :    │
                 │                                   │     • apply (droits via label    │
                 │                                   │       argocd.argoproj.io/        │
                 │                                   │       managed-by)                │
                 │                                   │     • prune    (objet retiré → supprimé)
                 │                                   │     • selfHeal (modif à la main  │
                 │                                   │                 → annulée)       │
                 │                                   └───────────────┬──────────────────┘
                 │                                                   │ apply
                 │                                                   ▼
                 │                                        namespace ollama-agent
                 │                                        ┌────────────────────────────┐
                 │                                        │ Deployment (1 replica,     │
                 │                                        │   strategy: Recreate)      │
                 │                                        │      │ image: …/ollama-agent:<sha7>
                 │                                        │      ▼   (pull registre interne)
                 │                                        │ Pod  ◄── Service ◄── Route │◄── navigateur
                 │                                        └──────┬─────────────────────┘
                 │                                               │ OLLAMA_URL (IP LAN du Mac)
                 │                                               ▼
                 │                                          Ollama gemma4:26b (Mac, hors cluster)
```

### Les deux circuits, et pourquoi ils ne se battent pas

```
                    CODE                                          CONFIG
             (*.py, frontend/, Dockerfile)                  (helm/, argocd/)
                        │                                          │
                        ▼                                          ▼
                  CI : runner Mac                            CD : Argo CD
        construit l'image, tag = <sha7>              aligne le cluster sur Git
                        │                                          ▲
                        │  écrit le tag dans helm/  ───────────────┘
                        └──────── commit sur main ─────────────────►  Git = SEULE
                                                                       source de vérité

   Garde-fous :
   • la CI ne touche JAMAIS au Deployment (sinon selfHeal l'annulerait aussitôt)
   • pas de :latest → chaque image a un tag immuable ; imagePullPolicy: IfNotPresent
   • pas de boucle : build.yml filtre sur paths SANS helm/ → le commit de la CI
     (qui ne touche que helm/) ne relance pas de build, et un push fait avec
     GITHUB_TOKEN ne déclenche pas d'autre workflow
   • rollback : git revert du commit "ci: déploie l'image …" + push → Argo CD
     redéploie l'ancien tag
```

## 1. L'image : un seul conteneur pour tout

Le `Dockerfile` travaille en deux étapes :

```
Étape 1 (node:22-slim)             Étape 2 (python:3.12-slim) = l'image finale
─────────────────────              ──────────────────────────────────────────
npm ci                             pip install -r requirements.txt
npm run build                      copie ollama_client.py, web_app.py, main.py
  → frontend/dist/  ───────────►   copie frontend/dist/
(cette étape est jetée)            lance uvicorn sur le port 8124
```

- L'étape 1 transforme le code React en fichiers statiques. Seul le résultat (`dist/`) est gardé.
- L'image finale ne contient que Python, le backend FastAPI et ces fichiers. Un seul conteneur sert à la fois l'API (`/api/chat`) et la page web (`/`).
- **Ollama n'est pas dans l'image** : l'appli le joint via la variable `OLLAMA_URL`.

Sur OpenShift, l'image n'est pas construite avec `docker build` mais par un **BuildConfig binaire** (`openshift/buildconfig.yaml`). `oc start-build --from-dir=.` envoie le dossier au cluster, qui construit l'image à partir du même `Dockerfile` et la range dans son **registre interne** (ImageStream `ollama-agent:latest`). Aucun registre externe n'est nécessaire.

## 2. Les particularités d'OpenShift

### Un utilisateur au hasard, sans droits administrateur
OpenShift lance chaque conteneur avec un UID aléatoire (SCC `restricted`). L'appli fonctionne telle quelle :
- le port 8124 est supérieur à 1024, donc pas besoin de droits particuliers ;
- l'appli n'écrit rien sur le disque, puisque l'historique est en mémoire.

Aucun `runAsUser` n'est donc imposé nulle part.

### La Route et son délai de 30 secondes
L'appli est exposée hors du cluster par une **Route**. Par défaut, le routeur d'OpenShift (HAProxy) coupe une connexion au bout de **30 s**. Or une réponse de `gemma4:26b` arrive mot par mot (streaming SSE) et peut durer plus longtemps. La Route du chart porte donc l'annotation `haproxy.router.openshift.io/timeout: 10m`.

### Joindre Ollama depuis le cluster
Ollama tourne sur le Mac, hors du cluster. Sur ce setup CRC, les adresses habituelles (`host.docker.internal`, `host.crc.testing`) ne mènent pas à Ollama. Ce qui marche, c'est **l'IP LAN du Mac** (ex. `192.168.1.119`). Si elle change (DHCP), il faut mettre à jour `ollamaUrl`. Détails : [`openshift/README.md`](../openshift/README.md#connectivité-vers-ollama).

### Une seule copie de l'appli
L'historique de conversation (`_history` dans `ollama_client.py`) vit **dans la mémoire du process**. Avec deux pods, chacun aurait son propre historique et le modèle « oublierait » une partie de la conversation selon le pod qui répond. D'où :
- `replicaCount` bloqué à 1 : le chart refuse toute autre valeur ;
- `strategy: Recreate` : lors d'une mise à jour, l'ancien pod s'arrête avant que le nouveau démarre. En contrepartie, l'appli est coupée quelques secondes à chaque mise à jour.

## 3. Le chart Helm

Le chart `helm/ollama-agent/` remplace `openshift/deployment.yaml` (Deployment + Service + Route).

**Pourquoi Helm plutôt que des YAML appliqués à la main :**

| Avec `oc apply -f deployment.yaml` | Avec le chart |
|---|---|
| L'IP d'Ollama est codée en dur ; on la change avec `oc set env`, qui n'est enregistré nulle part | L'IP est un réglage (`ollamaUrl`) |
| Pas d'historique des déploiements | `helm history` / `helm rollback` |
| Un seul environnement | Un fichier de valeurs par cluster |
| Supprimer l'appli = supprimer chaque objet à la main | `helm uninstall` |

`values-openshift.yaml` reproduit exactement la configuration CRC : image du registre interne, IP LAN d'Ollama, probes, Route en HTTP avec le même nom (donc la même URL).

**Choix volontaires :**
- **Réservé à OpenShift** : pas d'Ingress ni de `kubectl`.
- **Ollama n'est pas déployé par le chart** : il tourne sur le Mac, et la VM CRC n'aurait pas la mémoire nécessaire pour `gemma4:26b`.
- **Pas d'endpoint `/health`** dans l'appli : les vérifications de santé appellent `GET /`, la page React.
- **Tag d'image unique par build** (SHA court du commit, écrit par la CI dans `values-openshift.yaml`) plutôt que `:latest` : on sait quelle version tourne, et chaque tag ne change jamais, d'où `imagePullPolicy: IfNotPresent`.

**Passer des YAML au chart :** Helm ne peut pas reprendre des objets qu'il n'a pas créés, et le sélecteur d'un Deployment n'est pas modifiable. Il faut supprimer les objets existants avant l'installation (coupure de quelques secondes). Le BuildConfig et l'ImageStream ne sont pas touchés.

Détails : [`helm/README.md`](../helm/README.md).

## 4. Argo CD : Git décide de ce qui tourne

Argo CD (opérateur OpenShift GitOps) suit le chart Helm sur la branche `main` et l'applique au cluster tout seul (`argocd/application.yaml`) :
- **sync automatique** : un commit dans `helm/` est déployé sans commande manuelle ;
- **`selfHeal: true`** : toute modification faite à la main sur le cluster est annulée pour revenir à ce que dit Git ;
- **`prune: true`** : un objet retiré du chart est supprimé du cluster.

Argo CD ne construit **pas** l'image. Il ne s'occupe que de la configuration.

Détails, droits RBAC et variante sans Helm : [`argocd/README.md`](../argocd/README.md).

## 5. La CI : reconstruire l'image quand le code change

### Le problème
CRC tourne sur le Mac et **n'est pas joignable depuis Internet**. Les solutions habituelles ne marchent donc pas :
- **webhook GitHub → BuildConfig** : GitHub ne peut pas appeler l'API de CRC ;
- **runners hébergés par GitHub** : ils ne peuvent pas joindre le cluster non plus.

La seule solution qui marche va du Mac vers GitHub, et non l'inverse.

### La solution retenue : un runner GitHub Actions sur le Mac
Un **runner auto-hébergé** est un petit programme GitHub installé sur le Mac. Il se connecte lui-même à GitHub pour récupérer les jobs, puis parle au cluster en local avec `oc`.

Le workflow `.github/workflows/build.yml` :
1. se déclenche à chaque push sur `main` qui touche le code (`Dockerfile`, `*.py`, `requirements.txt`, `frontend/**`…), ou à la main ;
2. se connecte au cluster avec le compte de service `github-ci`, dont les droits sont limités au strict nécessaire ;
3. lance `oc start-build --from-dir=.` : nouvelle image dans le registre interne ;
4. lui donne un **tag unique**, le SHA court du commit (`oc tag … ollama-agent:<sha>`), à partir du digest produit par ce build ;
5. écrit ce tag dans `helm/ollama-agent/values-openshift.yaml` et **pousse ce commit sur `main`** ;
6. Argo CD voit le commit et redéploie le pod avec la nouvelle image.

Un changement dans `helm/` ou `argocd/` ne lance pas de build : c'est le travail d'Argo CD. C'est aussi ce qui évite une boucle, puisque le commit de la CI ne touche que `helm/`.

Pour revenir à la version précédente : `git revert` du commit `ci: déploie l'image ollama-agent:<sha>`, puis push. Argo CD redéploie l'ancienne image.

### Pourquoi passer par Git plutôt que redémarrer le pod directement
Argo CD, avec `selfHeal: true`, annule toute modification du Deployment faite en dehors de Git. Le redéploiement doit donc passer par Git :
- un **déclencheur d'image OpenShift** (`image.openshift.io/triggers`) réécrirait l'image du Deployment, Argo CD la remettrait aussitôt, et les deux se renverraient la balle ;
- un `oc rollout restart` avec une image `:latest` fonctionnerait, mais Git ne dirait pas quelle version tourne, et revenir en arrière serait compliqué.

Avec un tag écrit dans Git, l'historique de `values-openshift.yaml` est l'historique des déploiements.

### Les alternatives écartées

| Option | Pourquoi elle n'a pas été retenue |
|---|---|
| **Tekton (OpenShift Pipelines)** | Solution de CI « officielle » d'OpenShift, mais trop gourmande pour CRC (voir ci-dessous). Et il faudrait quand même quelque chose sur le Mac pour déclencher les builds, puisque le webhook GitHub ne peut pas joindre CRC. |
| **CronJob dans le cluster** qui interroge GitHub toutes les X minutes | Ça fonctionne, mais c'est plus bricolé et ça introduit un délai. |
| **Hook git local** (`pre-push`) qui lance `oc start-build` | Simple, mais ce n'est pas vraiment de la CI : ne se déclenche que pour les push faits depuis ce Mac. |

**Mémoire nécessaire pour Tekton (ordre de grandeur, à mesurer sur le cluster) :**
- **en permanence** : environ 1 à 1,5 Go pour les contrôleurs (pipelines, triggers, webhooks, chains, results, pipelines-as-code, opérateur, plugin console) ;
- **pendant un build** : environ 1 à 2 Go pour le pod Buildah. C'est à peu près ce que consomme déjà le build actuel, donc ce n'est pas un surcoût propre à Tekton.

Avec la configuration CRC par défaut (10,5 Go), OpenShift occupe déjà l'essentiel de la mémoire. Ajouter ~1,5 Go permanents risque de provoquer une pression mémoire (pods évincés ou impossibles à démarrer). Avec 16 Go (`crc config set memory 16384`, ce qui oblige à recréer le cluster), ça deviendrait raisonnable. Pour mesurer :

```bash
oc adm top node                           # marge disponible aujourd'hui
oc adm top pods -n openshift-pipelines    # consommation réelle de Tekton, une fois installé
```

Le runner sur le Mac, lui, ne consomme presque rien côté cluster : il réutilise le BuildConfig existant.

### Sécurité : le dépôt est public
Un runner auto-hébergé exécute le code des workflows **sur le Mac**. Sur un dépôt public, une PR venant d'un fork pourrait contenir un workflow qui cible ce runner. Protections :
- dans GitHub, **Settings → Actions → General → Require approval for all outside collaborators** : aucun workflow de fork ne tourne sans approbation ;
- de préférence, faire tourner le runner sous un **compte macOS dédié** ;
- le token du cluster n'est pas transmis aux workflows de forks, et ses droits sont limités.

Installation pas à pas, vérification et dépannage : [`openshift/CI.md`](../openshift/CI.md).

## 6. Au quotidien

| Je veux… | Je fais… |
|---|---|
| Déployer une modification du code | `git push` sur `main` : la CI reconstruit l'image, écrit son tag dans le chart, Argo CD redéploie |
| Revenir à la version précédente | `git revert` du dernier commit `ci: déploie l'image …`, puis push |
| Changer un réglage (ex. l'IP du Mac) | modifier `helm/ollama-agent/values-openshift.yaml`, puis commit et push : Argo CD synchronise |
| Relancer un build à la main | onglet **Actions** → *Build image OpenShift* → *Run workflow* |
| Voir l'URL de l'appli | `oc get route ollama-agent -n ollama-agent -o jsonpath='{.spec.host}'` |
| Voir l'état du déploiement | `oc get application ollama-agent -n openshift-gitops` (Argo CD) |

## Où trouver quoi

| Sujet | Fichier |
|---|---|
| Prérequis CRC, disque/mémoire, connectivité vers Ollama, build manuel | [`openshift/README.md`](../openshift/README.md) |
| Chart Helm : contenu, réglages, migration, rollback | [`helm/README.md`](../helm/README.md) |
| Argo CD : Application, RBAC, UI | [`argocd/README.md`](../argocd/README.md) |
| CI : installation du runner, secrets, sécurité, dépannage | [`openshift/CI.md`](../openshift/CI.md) |
