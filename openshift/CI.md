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

#### Qui peut modifier le dépôt

Le dépôt est public, mais **seuls ses collaborateurs peuvent y pousser des commits**. Aujourd'hui, il n'y en a qu'un : son propriétaire (liste dans **Settings** → **Collaborators**).

| Action | Possible pour un inconnu ? |
|---|---|
| Lire le code, le cloner | oui |
| Faire une copie sur son compte (*fork*) et la modifier | oui, sur **sa** copie uniquement |
| Ouvrir une issue ou proposer une PR | oui, mais rien n'entre dans le code sans que le propriétaire fusionne la PR |
| Pousser un commit sur `main` ou une autre branche | **non** |

Peuvent aussi écrire dans le dépôt, au nom du propriétaire :
- les **applications GitHub** installées avec un droit d'écriture (liste dans **Settings** → **GitHub Apps**) ;
- les **clés de déploiement** en écriture (**Settings** → **Deploy keys**) : vérifier qu'il n'y en a pas d'inconnue ;
- **ce workflow de CI**, via son jeton temporaire (voir plus bas).

#### Le vrai risque : le runner exécute du code sur le Mac

Personne d'extérieur ne peut écrire dans le dépôt. En revanche, un runner auto-hébergé exécute le code des workflows **directement sur le Mac**. Sur un dépôt public, n'importe qui peut ouvrir une PR depuis un fork. Si cette PR ajoute un workflow qui cible ce runner (`runs-on: self-hosted`), ce code pourrait tourner sur le Mac.

**À faire avant d'installer le runner :**
- **Settings** → **Actions** → **General** → *Fork pull request workflows from outside collaborators* → **Require approval for all outside collaborators**. Aucun workflow venant d'un fork ne tourne sans approbation explicite.
- **Ne jamais approuver** l'exécution des workflows d'une PR externe qui modifie le dossier `.github/`. Dans le doute, lire le diff avant de cliquer sur *Approve and run*.
- De préférence, faire tourner le runner sous un **compte macOS dédié**, sans accès aux fichiers personnels. CRC doit alors être installé et lancé sous ce même compte.

Ce qui limite les dégâts, même en cas d'erreur :
- le secret `OPENSHIFT_TOKEN` n'est **jamais transmis** aux workflows déclenchés par des forks ;
- ce token n'a que des droits minimaux dans le seul namespace `ollama-agent` (étape 1).

#### Le droit d'écriture du workflow

Le workflow doit pousser le commit qui met à jour `image.tag`. Pour ça, la ligne `permissions: contents: write` donne le droit d'écriture au **jeton temporaire** que GitHub fournit à chaque exécution (`GITHUB_TOKEN`) :
- ce jeton ne donne accès **qu'à ce dépôt** et **expire à la fin du job** ;
- il n'obtient ce droit que pour les push sur `main` et les lancements manuels. GitHub ne donne jamais de droit d'écriture au jeton des PR venant de forks ;
- un commit poussé avec ce jeton ne déclenche pas d'autre workflow (protection de GitHub contre les boucles) ;
- aucun réglage supplémentaire n'est nécessaire dans GitHub : sur un dépôt personnel, la ligne `permissions:` suffit.

### 5. Si `main` est protégée

`main` n'est pas protégée aujourd'hui, donc rien à faire. Si une règle du type « PR obligatoire avant fusion » est activée un jour, le push direct de la CI sera refusé : le job échouera à l'étape « Mise à jour du tag dans le chart Helm », après trois tentatives. Trois solutions :

1. **Laisser `main` sans protection.** C'est le plus simple pour un projet personnel : seuls les collaborateurs peuvent pousser.
2. **Autoriser une exception pour la CI** dans la règle (**Settings** → **Rules**, liste *Bypass*). Selon ce que GitHub permet d'y ajouter, il faudra peut-être donner à la CI une clé de déploiement en écriture plutôt que le jeton automatique.
3. **Faire ouvrir une PR par la CI** au lieu de pousser directement : chaque déploiement se valide d'un clic. Plus de contrôle, mais ce n'est plus automatique.

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
