# Argo CD `ollama-agent`

`argocd/application.yaml` fait piloter le chart Helm (`helm/ollama-agent/`) par Argo CD, à la place de `helm install`/`helm upgrade` lancés à la main. Le **build ne change pas** : on garde `openshift/buildconfig.yaml` et `oc start-build` (voir [`openshift/README.md`](../openshift/README.md)).

Testé avec **OpenShift GitOps** (l'opérateur Argo CD packagé pour OpenShift), namespace `openshift-gitops`, déjà installé sur le cluster CRC.

## Pourquoi

| Avec `helm upgrade` à la main | Avec Argo CD |
|---|---|
| Il faut se souvenir de relancer la commande après chaque `git push` | Sync automatique dès qu'un commit arrive sur `main` |
| Un `oc edit`/`oc scale` fait à la main reste en place tant que personne ne relance `helm upgrade` | `selfHeal: true` : tout écart par rapport à git est corrigé automatiquement |
| Pas de vue d'ensemble de ce qui est déployé où | UI Argo CD : état de sync, historique, diff |

## `argocd/application.yaml`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ollama-agent
  namespace: openshift-gitops
spec:
  project: default
  source:
    repoURL: https://github.com/nicolas-budin/ollama_agent.git
    targetRevision: main
    path: helm/ollama-agent
    helm:
      valueFiles:
        - values-openshift.yaml
  destination:
    server: https://kubernetes.default.svc
    namespace: ollama-agent
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- `repoURL`/`targetRevision`/`path` : Argo CD clone directement `https://github.com/nicolas-budin/ollama_agent.git` sur la branche `main`, chart au chemin `helm/ollama-agent`. Le repo est **public**, aucun credential à configurer côté Argo CD.
- `helm.valueFiles` : réutilise `values-openshift.yaml`, le même fichier que pour `helm install`/`helm upgrade` manuel.
- `syncPolicy.automated` : sync automatique dès qu'un commit change quelque chose sous `helm/ollama-agent/` sur `main`, `prune: true` supprime les objets retirés du chart, `selfHeal: true` corrige tout changement fait à la main sur le cluster.
- `syncOptions: [CreateNamespace=true]` : Argo CD crée le namespace `ollama-agent` s'il n'existe pas déjà (il existe déjà ici, créé par `oc new-project`).

## Deux sources possibles : Helm ou YAML brut

`argocd/application-raw.yaml` est une variante de `argocd/application.yaml` qui pointe
directement sur `openshift/deployment.yaml` (`source.directory`, pas Helm) au lieu du
chart. Les deux fichiers définissent une `Application` du **même nom** (`ollama-agent`,
namespace `openshift-gitops`) : comme un seul objet de ce nom peut exister dans le
cluster, appliquer l'un ou l'autre **remplace** la source suivie par cette Application,
plutôt que de créer deux Applications concurrentes qui se disputeraient les mêmes objets
(`Deployment`/`Service`/`Route` dans `ollama-agent`).

```bash
oc apply -f argocd/application.yaml       # suit le chart Helm
oc apply -f argocd/application-raw.yaml   # suit openshift/deployment.yaml directement
```

Passer de l'un à l'autre déclenche simplement un nouveau sync Argo CD sur la nouvelle
source — pas besoin de `helm uninstall` ni de nettoyage manuel entre les deux (contrairement
à la migration depuis une release Helm CLI ci-dessous, qui concernait un système *externe*
à Argo CD).

## Migration depuis une release Helm manuelle

Une release Helm installée à la main (`helm install`/`helm upgrade`) laisse des labels/annotations Helm CLI sur les objets. Si Argo CD reprend les mêmes objets sans qu'on l'ait désinstallée d'abord, ça peut créer des conflits de propriété entre les deux systèmes. Désinstaller proprement avant de laisser Argo CD tout recréer depuis git :

```bash
helm uninstall ollama-agent -n ollama-agent
```

(Le namespace `ollama-agent` lui-même reste — Argo CD y redéploiera dedans.)

Puis enregistrer l'Application — elle doit exister comme objet dans le cluster (namespace `openshift-gitops`), pas seulement dans git, pour qu'Argo CD la prenne en compte :

```bash
oc apply -f argocd/application.yaml
```

## Droits RBAC sur le namespace cible

Par défaut, l'instance OpenShift GitOps ne peut gérer que les namespaces qu'on lui a
explicitement autorisés — sans ça, la sync reste bloquée avec des erreurs de ce genre
(visibles dans `oc get application ollama-agent -n openshift-gitops -o yaml`, sous
`status.operationState.message`) :

```text
services is forbidden: User "system:serviceaccount:openshift-gitops:openshift-gitops-argocd-application-controller"
cannot create resource "services" ... in namespace "ollama-agent"
```

(même chose pour `deployments` et `routes` — Argo CD retry automatiquement, mais échoue à
chaque tentative tant que le droit manque). Corrige en labellisant le namespace cible :

```bash
oc label namespace ollama-agent argocd.argoproj.io/managed-by=openshift-gitops
```

Ce label déclenche (via l'opérateur GitOps) la création automatique d'un `RoleBinding`
donnant les droits `admin` au service account d'Argo CD dans ce namespace. Une fois posé,
la sync en cours (retry automatique) doit passer sans autre intervention — pas besoin de
relancer manuellement `oc apply -f argocd/application.yaml`.

## Au quotidien

```bash
# Nouveau code applicatif : le build ne passe pas par Argo CD. Automatique à chaque
# push sur main via GitHub Actions (voir openshift/CI.md), ou à la main :
oc start-build ollama-agent --from-dir=. --follow
oc rollout restart deployment/ollama-agent

# Changer un réglage (ex. l'IP du Mac qui a changé) : commit + push sur main,
# Argo CD synchronise tout seul — plus besoin de `helm upgrade --set ...` à la main
git commit -am "ollama-agent: nouvelle IP du Mac"
git push

# Forcer une sync immédiate sans attendre le prochain cycle de polling
argocd app sync ollama-agent
```

## Vérification

```bash
oc get application ollama-agent -n openshift-gitops   # SYNC STATUS: Synced, HEALTH STATUS: Healthy
oc get deploy,svc,route -n ollama-agent                # mêmes objets qu'avant, gérés par Argo CD
oc get route ollama-agent -n ollama-agent -o jsonpath='{.spec.host}'   # tester le chat, aucune régression attendue
```

Test du self-heal :

```bash
oc scale deploy/ollama-agent -n ollama-agent --replicas=0
# Attendre quelques secondes/minutes : Argo CD doit remettre replicas=1 tout seul,
# conformément à ce que dit git (via `oc get application ollama-agent -n openshift-gitops -w`)
```

## UI Argo CD

```bash
oc get route -n openshift-gitops openshift-gitops-server -o jsonpath='{.spec.host}'
oc extract secret/openshift-gitops-cluster -n openshift-gitops --to=- --keys=admin.password
# Login : admin / le mot de passe ci-dessus
```
