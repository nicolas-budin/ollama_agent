# CI : reconstruire l'image à chaque changement de code

À chaque push sur `main` qui touche le code de l'appli, GitHub Actions reconstruit l'image dans le registre interne d'OpenShift, puis redémarre l'appli pour qu'elle utilise la nouvelle image.

```
push sur main ──► GitHub Actions ──► runner sur le Mac (même machine que CRC)
                                        │ oc start-build ollama-agent --from-dir=. --follow --wait
                                        ▼
                               ImageStream ollama-agent:latest mise à jour
                                        │ oc rollout restart deployment/ollama-agent
                                        ▼
                               nouveau pod avec la nouvelle image
```

Fichiers :
- `.github/workflows/build.yml` : le workflow ;
- `openshift/ci-serviceaccount.yaml` : le compte de service utilisé par le workflow, avec des droits limités ;
- `.github/actionlint.yaml` : déclare le label `crc` du runner, pour `actionlint`.

## Pourquoi un runner sur le Mac

CRC tourne sur le Mac et n'est pas joignable depuis Internet. Un webhook GitHub vers le BuildConfig, ou un runner hébergé par GitHub, ne peuvent donc pas atteindre le cluster. Le runner auto-hébergé fonctionne dans l'autre sens : c'est lui qui se connecte à GitHub pour récupérer les jobs, puis il parle au cluster localement avec `oc`.

## Pourquoi `oc rollout restart` et pas un déclencheur d'image

Argo CD gère le Deployment avec `selfHeal: true` (voir [`argocd/README.md`](../argocd/README.md)). Un déclencheur d'image OpenShift (`image.openshift.io/triggers`) réécrirait l'image du Deployment. Argo CD la remettrait aussitôt à la valeur de Git, et les deux se renverraient la balle. `oc rollout restart` n'ajoute qu'une annotation, `kubectl.kubernetes.io/restartedAt`, qu'Argo CD laisse en place. Comme l'image est `:latest` avec `imagePullPolicy: Always`, le nouveau pod récupère la nouvelle image.

## Ce qui déclenche un build

Un push sur `main` qui modifie `Dockerfile`, `.dockerignore`, `requirements.txt`, un fichier `*.py` à la racine, `frontend/**` ou le workflow lui-même. Un changement dans `helm/` ou `argocd/` ne lance **pas** de build : c'est Argo CD qui s'en charge.

On peut aussi lancer le workflow à la main : onglet **Actions** → **Build image OpenShift** → **Run workflow**.

## Installation (une seule fois)

### 1. Compte de service dans le cluster

```bash
eval $(crc oc-env)
oc login -u kubeadmin https://api.crc.testing:6443
oc apply -n ollama-agent -f openshift/ci-serviceaccount.yaml
oc extract secret/github-ci-token -n ollama-agent --keys=token --to=-   # affiche le token
```

Ce compte peut uniquement, dans le namespace `ollama-agent` :
- lancer un build binaire depuis le BuildConfig et suivre ses logs ;
- redémarrer le Deployment et suivre son redémarrage.

Il ne peut ni supprimer d'objets, ni lire les secrets, ni toucher aux autres namespaces.

### 2. Secret et variable GitHub

Dans le dépôt GitHub, allez dans **Settings** → **Secrets and variables** → **Actions** :

| Type | Nom | Valeur |
|---|---|---|
| Secret | `OPENSHIFT_TOKEN` | le token affiché à l'étape 1 |
| Variable | `OPENSHIFT_SERVER` | `https://api.crc.testing:6443` |

### 3. Runner sur le Mac

1. Dans GitHub, allez dans **Settings** → **Actions** → **Runners** → **New self-hosted runner** → **macOS**. GitHub affiche les commandes de téléchargement et un token d'enregistrement valable une heure.
2. Lancez-les sur le Mac, dans un dossier dédié (ex. `~/actions-runner`), en ajoutant le label `crc` à la configuration :
   ```bash
   ./config.sh --url https://github.com/nicolas-budin/ollama_agent --token <TOKEN_AFFICHÉ> --labels crc
   ```
   Le runner reçoit automatiquement les labels `self-hosted` et `macOS`. Le workflow demande `[self-hosted, macOS, crc]`.
3. Installez-le comme service, pour qu'il démarre avec la session :
   ```bash
   ./svc.sh install
   ./svc.sh start
   ```

Le workflow utilise le `oc` fourni par CRC (`~/.crc/bin/oc`). Le runner doit donc tourner sous l'utilisateur qui a installé CRC. Il utilise un kubeconfig temporaire propre au job et ne touche pas à votre `~/.kube/config`.

### 4. Sécurité : le dépôt est public

Un runner auto-hébergé exécute le code des workflows **sur votre Mac**. Sur un dépôt public, quelqu'un pourrait ouvrir une PR depuis un fork contenant un workflow qui cible ce runner. À faire :

- **Settings** → **Actions** → **General** → *Fork pull request workflows from outside collaborators* → **Require approval for all outside collaborators**. Aucun workflow de fork ne tourne sans votre accord explicite. **N'approuvez jamais une PR externe qui modifie `.github/`.**
- De préférence, faites tourner le runner sous un **compte macOS dédié**, sans accès à vos fichiers personnels. CRC doit alors être installé et lancé sous ce même compte.
- Le secret `OPENSHIFT_TOKEN` n'est pas transmis aux workflows déclenchés par des forks, et ses droits sont limités (étape 1).

## Vérifier

1. Onglet **Actions** → **Build image OpenShift** → **Run workflow** sur `main`.
2. Le job doit passer par les étapes connexion → build → redéploiement.
3. Côté cluster :
   ```bash
   oc get builds -n ollama-agent                        # le nouveau build est Complete
   oc get pods -n ollama-agent                          # nouveau pod, âge de quelques secondes
   ```

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Le job reste en attente (*Waiting for a runner*) | Mac en veille, runner arrêté (`./svc.sh status`), ou label `crc` absent |
| `oc: command not found` | CRC installé sous un autre utilisateur, ou `~/.crc/bin/oc` absent (`crc setup`) |
| `error: You must be logged in` / `Unauthorized` | `OPENSHIFT_TOKEN` absent ou invalide, ou cluster recréé (`crc delete`) : refaire l'étape 1 et mettre à jour le secret |
| `Unable to connect to the server` | CRC arrêté (`crc start`) |
| `forbidden` | `ci-serviceaccount.yaml` pas appliqué dans `ollama-agent` |
| Build en échec | voir les logs du job ou `oc logs build/<nom> -n ollama-agent` (souvent la pression disque, voir `openshift/README.md`) |
