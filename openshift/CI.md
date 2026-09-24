# CI : reconstruire l'image à chaque changement de code

À chaque push sur `main` qui touche le code de l'appli, GitHub Actions reconstruit l'image dans le registre interne d'OpenShift, lui donne un tag unique, puis écrit ce tag dans le chart Helm. Argo CD voit le changement dans Git et redéploie l'appli.

```
push sur main ──► GitHub Actions ──► runner sur le Mac (même machine que CRC)
                                        │ 1. oc start-build ollama-agent --from-dir=.
                                        ▼
                               image dans le registre interne
                                        │ 2. oc tag … ollama-agent:<sha>   (tag unique = SHA du commit)
                                        │ 3. commit sur main : image.tag: "<sha>"
                                        │    dans helm/ollama-agent/values-openshift.yaml
                                        ▼
                               Argo CD voit le commit ──► nouveau pod avec l'image <sha>
```

C'est **Git qui décide de l'image qui tourne** : l'historique de `values-openshift.yaml` dit quelle version a été déployée et quand, et un `git revert` du commit de la CI revient à l'image précédente.

Fichiers :
- `.github/workflows/build.yml` : le workflow ;
- `openshift/ci-serviceaccount.yaml` : le compte de service utilisé par le workflow, avec des droits limités ;
- `.github/actionlint.yaml` : déclare le label `crc` du runner, pour `actionlint`.

## Pourquoi un runner sur le Mac

CRC tourne sur le Mac et n'est pas joignable depuis Internet. Un webhook GitHub vers le BuildConfig, ou un runner hébergé par GitHub, ne peuvent donc pas atteindre le cluster. Le runner auto-hébergé fonctionne dans l'autre sens : c'est lui qui se connecte à GitHub pour récupérer les jobs, puis il parle au cluster localement avec `oc`.

## Pourquoi un tag écrit dans Git

Argo CD gère le Deployment avec `selfHeal: true` (voir [`argocd/README.md`](../argocd/README.md)) : tout ce qui modifie le Deployment en dehors de Git est annulé. Le redéploiement doit donc passer par Git.

- **Pas de déclencheur d'image OpenShift** (`image.openshift.io/triggers`) : il réécrirait l'image du Deployment, Argo CD la remettrait aussitôt, et les deux se renverraient la balle.
- **Pas de `:latest`** : un tag qui change de contenu ne dit pas quelle version tourne, et ne permet pas de revenir en arrière proprement. Chaque build a son tag (SHA court du commit), qui ne change jamais. D'où `imagePullPolicy: IfNotPresent`.
- **Le tag part du digest du build**, pas de `:latest` : si deux builds se suivaient de près, on étiquette bien l'image produite par *ce* build.

## Ce qui déclenche un build

Un push sur `main` qui modifie `Dockerfile`, `.dockerignore`, `requirements.txt`, un fichier `*.py` à la racine, `frontend/**` ou le workflow lui-même. Un changement dans `helm/` ou `argocd/` ne lance **pas** de build : c'est Argo CD qui s'en charge. C'est aussi pour ça que le commit de la CI (qui ne touche que `helm/`) ne relance pas le workflow en boucle.

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
- ajouter un tag à l'ImageStream `ollama-agent`.

Il ne peut ni modifier le Deployment (c'est Argo CD qui le fait), ni supprimer d'objets, ni lire les secrets, ni toucher aux autres namespaces.

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
- Le workflow a le droit d'écrire dans le dépôt (`contents: write`), pour pousser le commit du tag. Ce droit n'est donné qu'aux push sur `main` et aux lancements manuels, pas aux PR.

### 5. Si `main` est protégée

Le workflow pousse directement sur `main`. Si une règle de protection de branche l'interdit, autorisez `github-actions[bot]` à contourner la règle (**Settings** → **Branches** / **Rules**), ou demandez-moi une variante qui ouvre une PR au lieu de pousser.

## Vérifier

1. Onglet **Actions** → **Build image OpenShift** → **Run workflow** sur `main`.
2. Le job doit passer par les étapes connexion → build → tag → mise à jour du chart.
3. Un commit `ci: déploie l'image ollama-agent:<sha>` apparaît sur `main`.
4. Côté cluster, après la synchronisation d'Argo CD (quelques minutes, ou `argocd app sync ollama-agent` pour forcer) :
   ```bash
   oc get istag -n ollama-agent                                  # ollama-agent:<sha> existe
   oc get deploy ollama-agent -n ollama-agent \
     -o jsonpath='{.spec.template.spec.containers[0].image}'      # se termine par :<sha>
   oc get pods -n ollama-agent                                   # nouveau pod, âge récent
   ```

## Revenir à une version précédente

```bash
git revert <commit "ci: déploie l'image ollama-agent:<sha>">   # remet le tag précédent
git push                                                        # Argo CD redéploie l'ancienne image
```

## Ménage dans les anciennes images

Chaque build ajoute un tag à l'ImageStream, et les anciennes images restent dans le registre interne. Sur CRC, où le disque est compté, supprimez de temps en temps les vieux tags (`oc tag -d ollama-agent:<ancien-sha> -n ollama-agent`) puis lancez le nettoyage du registre en tant qu'administrateur (`oc adm prune images --confirm`).

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Le job reste en attente (*Waiting for a runner*) | Mac en veille, runner arrêté (`./svc.sh status`), ou label `crc` absent |
| `oc: command not found` | CRC installé sous un autre utilisateur, ou `~/.crc/bin/oc` absent (`crc setup`) |
| `error: You must be logged in` / `Unauthorized` | `OPENSHIFT_TOKEN` absent ou invalide, ou cluster recréé (`crc delete`) : refaire l'étape 1 et mettre à jour le secret |
| `Unable to connect to the server` | CRC arrêté (`crc start`) |
| `forbidden` | `ci-serviceaccount.yaml` pas appliqué (ou pas ré-appliqué après une mise à jour) dans `ollama-agent` |
| Push refusé 3 fois / `protected branch` | `main` protégée : voir l'étape 5 |
| Commit poussé mais pod inchangé | Argo CD n'a pas encore synchronisé (`oc get application ollama-agent -n openshift-gitops`), ou l'Application suit une autre source (`application-raw.yaml`) |
| Build en échec | voir les logs du job ou `oc logs build/<nom> -n ollama-agent` (souvent la pression disque, voir `openshift/README.md`) |
