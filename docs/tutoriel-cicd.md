# Tutoriel : mettre en place le CI/CD, étape par étape

Ce document raconte, dans l'ordre où ça a été fait, comment on est passé d'un `oc apply -f openshift/deployment.yaml` lancé à la main à un pipeline complet : `git push` → nouvelle image → nouveau pod, sans autre intervention. Chaque étape explique le problème rencontré et pourquoi ce choix plutôt qu'un autre. Pour les commandes exactes et à jour, chaque section renvoie vers la doc de référence du sujet plutôt que de les recopier ici.

Si tu veux reproduire ça sur un autre projet, suis les étapes dans cet ordre : chacune suppose que la précédente est en place.

## Point de départ

L'appli tournait sur OpenShift (CRC) via deux commandes manuelles :

```bash
oc apply -f openshift/buildconfig.yaml
oc start-build ollama-agent --from-dir=. --follow
oc apply -f openshift/deployment.yaml
```

Ça marche, mais tout changement (nouveau code, IP d'Ollama qui change) demande de relancer ces commandes à la main. L'objectif : que `git push` suffise.

## Étape 1 — Un chart Helm plutôt que des YAML bruts

**Problème** : `oc apply -f deployment.yaml` code en dur l'IP d'Ollama et n'a pas d'historique — un `oc set env` fait à la main pour corriger l'IP n'est enregistré nulle part, et un `oc apply` suivant peut l'écraser sans qu'on s'en aperçoive.

**Solution** : un chart Helm (`helm/ollama-agent/`) qui remplace `Deployment`/`Service`/`Route`, avec l'IP d'Ollama comme un réglage (`ollamaUrl` dans `values-openshift.yaml`) plutôt qu'une valeur codée en dur. Le `BuildConfig`/`ImageStream` restent en dehors du chart : Helm gère le déploiement, pas le build.

Testé d'abord à la main (`helm install`, puis `helm upgrade` à chaque changement) — ce qui a tout de suite fait ressortir le problème suivant : il faut se souvenir de relancer `helm upgrade` après chaque changement. D'où l'étape 2.

Détails du chart, migration depuis les YAML bruts, structure des `values*.yaml` : [`helm/README.md`](../helm/README.md).

## Étape 2 — Argo CD : Git décide de ce qui est déployé

**Problème** : avec Helm seul, il faut relancer `helm upgrade` à la main à chaque changement. Rien n'empêche non plus un `oc scale`/`oc edit` fait par erreur de rester en place indéfiniment.

**Solution** : Argo CD (déjà installé sur CRC via l'opérateur **OpenShift GitOps**) suit le chart Helm sur la branche `main` et applique tout seul ce qu'il y trouve :
- **sync automatique** : un commit sous `helm/` est déployé sans commande manuelle ;
- **`selfHeal: true`** : un changement fait à la main sur le cluster est annulé pour revenir à ce que dit Git ;
- **`prune: true`** : un objet retiré du chart est supprimé du cluster.

Concrètement, `argocd/application.yaml` déclare une `Application` Argo CD qui pointe sur `helm/ollama-agent` + `values-openshift.yaml`. Comme Argo CD lit Git lui-même, aucune commande à relancer après un `git push`.

**Piège rencontré à l'installation** : Argo CD refusait de créer les objets, avec une erreur `services is forbidden ... in namespace "ollama-agent"`. Par défaut, OpenShift GitOps n'a le droit de gérer que les namespaces explicitement autorisés. Fix : `oc label namespace ollama-agent argocd.argoproj.io/managed-by=openshift-gitops`, qui déclenche la création automatique du `RoleBinding` nécessaire.

*Pour comprendre : Argo CD ne fait ni `helm install` ni `helm upgrade` — il fait l'équivalent de `helm template` (rendu des manifestes) puis applique/diff lui-même. Il n'y a donc pas de release Helm côté cluster : c'est l'objet `Application` qui devient la source de vérité sur l'état déployé.*

Détails, RBAC, variante sans Helm (`argocd/application-raw.yaml`) : [`argocd/README.md`](../argocd/README.md).

## Étape 3 — Le problème du déclenchement : CRC n'est pas joignable depuis Internet

À ce stade, la **configuration** (Helm) est automatiquement synchronisée par Argo CD. Il manque encore la partie **code** : quand `ollama_client.py` change, il faut reconstruire l'image.

**Problème** : les deux façons habituelles de déclencher un build depuis GitHub ne marchent pas ici :
- un **webhook GitHub → BuildConfig** : GitHub ne peut pas appeler l'API de CRC, qui tourne sur le Mac derrière son réseau local ;
- un **runner hébergé par GitHub** (`ubuntu-latest`) : il ne peut pas non plus joindre CRC.

**Solution retenue** : un **runner GitHub Actions auto-hébergé**, installé sur le Mac qui fait tourner CRC. La connexion se fait dans l'autre sens — c'est le runner qui appelle GitHub, jamais l'inverse — donc aucun port à ouvrir sur le Mac.

Alternatives écartées (Tekton, CronJob qui interroge GitHub, hook `pre-push`) et pourquoi : [`docs/deploiement-openshift.md`](deploiement-openshift.md#les-alternatives-écartées).

## Étape 4 — Installer le runner

Deux prérequis côté cluster et côté GitHub, avant le runner lui-même :

1. **Un compte de service dédié** (`openshift/ci-serviceaccount.yaml`) : le runner ne doit pas se connecter au cluster avec un compte admin. Ce compte n'a le droit, dans le seul namespace `ollama-agent`, que de lancer un build et de taguer l'image — pas de toucher au Deployment (c'est le travail d'Argo CD), pas de lire les secrets, pas d'accès aux autres namespaces.
2. **Un secret et une variable GitHub** : le token du compte de service en `secrets.OPENSHIFT_TOKEN`, l'URL de l'API CRC en `vars.OPENSHIFT_SERVER`.

*Pour comprendre la différence entre les deux : une **variable** est une valeur non sensible, visible en clair dans les settings et dans les logs. Un **secret** est chiffré, illisible une fois enregistré, masqué dans les logs, et jamais transmis aux workflows déclenchés par une PR venant d'un fork. Règle simple : si la valeur donne accès à quelque chose, c'est un secret.*

3. **Le runner lui-même** : un petit programme téléchargé depuis GitHub (**Settings → Actions → Runners → New self-hosted runner**), configuré avec `--labels crc` (obligatoire : le workflow demande `runs-on: [self-hosted, macOS, crc]`), puis lancé en service (`./svc.sh install && ./svc.sh start`) pour qu'il tourne en permanence.

*Pour comprendre l'authentification : l'enregistrement (`./config.sh --token <TOKEN>`) utilise un token GitHub valable 1 heure, qui sert une seule fois à faire générer par le runner une paire de clés RSA — la clé publique part chez GitHub, la clé privée reste sur le Mac (`actions-runner/.credentials_rsaparams`). Ensuite, à chaque connexion, le runner signe une requête avec cette clé privée pour obtenir un jeton de courte durée. Ces fichiers de credentials sont aussi sensibles qu'un mot de passe : `actions-runner/` doit être dans `.gitignore` (un oubli à ce sujet a été corrigé dans ce repo — le checkout du runner avait été committé par erreur comme sous-module Git).*

**Sécurité, dépôt public** : un runner auto-hébergé exécute le code des workflows **sur le Mac**. Sur un dépôt public, une PR venant d'un fork pourrait contenir un workflow ciblant ce runner. Avant d'installer quoi que ce soit : **Settings → Actions → General → Require approval for all outside collaborators**.

Installation complète pas à pas (choix du compte macOS, empêcher la veille, dépannage) : [`openshift/CI.md`](../openshift/CI.md).

## Étape 5 — Le workflow de build (`.github/workflows/build.yml`)

Avec le runner en place, reste à écrire ce qu'il doit faire. Trois choix structurants, chacun motivé par une contrainte déjà posée par Argo CD :

**Un tag unique par build (le SHA court du commit), jamais `:latest`.** Argo CD, avec `selfHeal: true`, annule tout ce qui modifie le Deployment en dehors de Git — donc le tag de l'image doit changer *dans Git*, pas seulement dans le registre. Avec `:latest`, l'historique de Git ne dirait pas quelle version tourne, et revenir en arrière serait impossible à faire proprement.

**Le workflow écrit le tag dans le chart et pousse le commit — il ne touche jamais au Deployment.** Un déclencheur d'image OpenShift (`image.openshift.io/triggers`) réécrirait l'image du Deployment ; Argo CD la remettrait aussitôt à ce que dit Git ; les deux se renverraient la balle indéfiniment.

**Le filtre `paths` exclut `helm/`.** Le commit que le workflow pousse lui-même (`ci: déploie l'image ollama-agent:<sha>`) ne modifie que `helm/ollama-agent/values-openshift.yaml`. Comme ce dossier est exclu des déclencheurs, ce commit ne relance pas un nouveau build en boucle.

Résultat, dans l'ordre où le job les exécute :

```
oc login (compte de service)
  → oc start-build ollama-agent --from-dir=.        (nouvelle image, registre interne)
  → oc tag ollama-agent@<digest> ollama-agent:<sha>  (tag immuable)
  → sed : image.tag: "<sha>" dans values-openshift.yaml
  → git commit + git push origin main
      → Argo CD voit le commit, redéploie
```

*Pour comprendre comment ce fichier atteint le Mac : GitHub ne copie pas `build.yml` sur le runner. Il le lit lui-même côté serveur, évalue les `${{ ... }}`, et envoie au runner une liste de tâches déjà interprétées (dont les secrets nécessaires à **ce job précis**). Le runner télécharge alors les actions référencées (`actions/checkout@v4`), clone le dépôt en local, puis exécute chaque `run:` comme un script shell ordinaire. Voir le schéma complet : [`docs/deploiement-openshift.md`](deploiement-openshift.md#schémas-détaillés--la-ci-runner-et-le-cd-argo-cd).*

*Pour comprendre le vocabulaire : un **workflow** est le fichier `.yml` en entier ; un **job** (`build`) est un groupe d'étapes exécuté sur un runner ; un **step** est soit un `uses:` (une **action**, du code réutilisable écrit par quelqu'un d'autre), soit un `run:` (un script qu'on écrit soi-même).*

Pourquoi cette solution plutôt que Tekton ou un CronJob, et ce que ça change pour la mémoire du cluster : [`docs/deploiement-openshift.md`](deploiement-openshift.md#5-la-ci--reconstruire-limage-quand-le-code-change).

## Étape 6 — Les tests (`.github/workflows/ci.yml`)

Séparément du build, un deuxième workflow, `ci.yml`, tourne sur des runners **hébergés par GitHub** (`ubuntu-latest`) — pas besoin du Mac ni de CRC pour lancer des tests, puisqu'ils ne touchent jamais au cluster.

Deux jobs indépendants, `backend` (`pytest`) et `frontend` (`vitest` + `oxlint`), tous deux entièrement **mockés** : aucun test ne parle à un vrai Ollama ni ne fait de vraie requête réseau, ce qui permet de tourner à l'identique en local et en CI. Le point le plus spécifique à ce projet : `tests/test_ollama_client.py` rejoue de fausses lignes NDJSON (le format réel d'Ollama) pour vérifier la traduction NDJSON → SSE faite par `web_app.py`.

**Piège rencontré** : `web_app.py` monte `StaticFiles(directory=FRONTEND_DIST, ...)` à l'import du module. Sur un checkout frais (celui que fait la CI), `frontend/dist` n'existe pas encore (il est `gitignored`, produit par `npm run build`) — donc importer `web_app` plantait avant même qu'un test tourne. Fix : `check_dir=False` sur ce mount.

**Ce workflow tourne en parallèle du build**, sans dépendre l'un de l'autre : un push qui casse les tests ne bloque pas la construction de l'image. Pour les lier (ne builder que si les tests passent), il faudrait soit les mettre dans le même workflow avec `needs:`, soit déclencher `build.yml` avec `workflow_run` une fois `ci.yml` terminé — pas fait ici.

Détail des tests et de leurs mocks : voir la section « Tests » de `CLAUDE.md`.

## Vue d'ensemble

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
```

Schémas détaillés (chaque flèche, chaque appel) : [`docs/deploiement-openshift.md`](deploiement-openshift.md#schémas-détaillés--la-ci-runner-et-le-cd-argo-cd).

## Pour le reproduire sur un autre projet — check-list

1. Le déploiement marche déjà avec des YAML appliqués à la main (`oc apply`).
2. Chart Helm qui remplace ces YAML, avec les valeurs qui varient (URL, image, secrets) en réglages plutôt qu'en dur.
3. Argo CD installé sur le cluster (OpenShift GitOps) → une `Application` qui pointe sur le chart, sync automatique + `selfHeal`. Vérifier le label `argocd.argoproj.io/managed-by` sur le namespace cible si la sync reste bloquée avec des erreurs `forbidden`.
4. Le cluster est-il joignable depuis Internet ?
   - **Oui** → un webhook GitHub classique ou un runner hébergé par GitHub suffit.
   - **Non** (cas de ce projet) → runner auto-hébergé, avec un compte de service aux droits minimaux et le secret/variable correspondants côté GitHub.
5. Workflow de build : tag unique par commit (pas `:latest`), écrit dans le chart et poussé sur Git — jamais de modification directe du Deployment. Filtrer les déclencheurs pour exclure les fichiers que ce commit modifie lui-même.
6. Workflow de tests séparé, sur des runners hébergés par GitHub si les tests n'ont pas besoin du cluster, entièrement mocké côté réseau.
7. Sécuriser le runner auto-hébergé si le dépôt est public : approbation obligatoire pour les workflows de fork, compte système dédié de préférence.
