# Déployer sur OpenShift (CRC / OpenShift Local)

Testé sur ce cluster CRC (mono-nœud, `oc` fourni par `crc oc-env`).

## Prérequis

```bash
eval $(crc oc-env)     # met oc dans le PATH
oc login -u kubeadmin  # ou oc whoami pour vérifier la session
```

⚠️ **Pression disque** : si `oc get node crc -o jsonpath='{.status.conditions}'` montre `DiskPressure=True`,
aucun pod (build ou déploiement) ne pourra être planifié. À nettoyer avant de continuer
(`crc` ne fournit pas de commande dédiée — nettoyer les images/couches inutilisées à
l'intérieur de la VM, ou augmenter `crc config set disk-size` puis `crc delete && crc start`,
ce qui recrée le cluster).

### Augmenter le disque / la mémoire / les CPU alloués à la VM CRC

Config par défaut : `memory` 10752 Mo (~10.5 Go), `disk-size` 31 Go, `cpus` 4
(`crc config get <clé>` pour voir la valeur actuelle). `crc config set` seul ne suffit
pas — CRC ne peut pas redimensionner une VM à chaud, il faut supprimer et recréer :

```bash
crc config set memory 16384      # Mo (16 Go) — ajuster selon la RAM dispo sur l'hôte
crc config set disk-size 100     # Go
crc config set cpus 6

crc stop
crc delete                       # ⚠️ supprime la VM ET tout le cluster OpenShift dedans
crc start                        # recrée le cluster avec les nouvelles valeurs (5-15 min)
```

- **Destructif** : `crc delete` supprime le cluster entier, y compris le projet
  `ollama-agent` et son `BuildConfig`/`ImageStream`. Rien n'est perdu côté code — ce repo
  contient tous les manifestes — mais il faudra refaire `oc new-project ollama-agent` +
  `oc apply -f openshift/...` + `oc start-build` après coup (sections ci-dessous).
- **Pull secret** : si `crc config get pull-secret-file` n'est pas configuré, `crc start`
  après un `crc delete` peut redemander interactivement de coller le pull secret
  (récupérable sur <https://console.redhat.com/openshift/create/local>) — le tenir prêt, ou
  lancer `crc start` dans un terminal interactif plutôt que dans un script.
- **Vérifier après coup** : `crc status` (version/état), `crc config get memory` /
  `disk-size` / `cpus` (valeurs appliquées), et
  `oc get node crc -o jsonpath='{.status.conditions}'` (`DiskPressure=False`).

## Connectivité vers Ollama

Ollama tourne sur ta machine (Mac), pas dans le cluster. Sur ce setup CRC (vfkit +
gvisor-tap-vsock), les alias habituels ne fonctionnent **pas** :

- `host.crc.testing` résout bien vers `192.168.127.254` (confirmé via `getent hosts`
  depuis un pod), mais la connexion TCP vers le port 11434 échoue — cette adresse ne
  route pas vers le vrai Ollama qui écoute sur le Mac.
- **Ce qui marche** : l'IP LAN réelle de la machine (trouvée via `ifconfig` /
  Réglages réseau, ex. `192.168.1.119`), parce qu'Ollama écoute déjà sur toutes les
  interfaces (`lsof -iTCP -sTCP:LISTEN` montre `*:11434`), et les pods CRC ont un accès
  réseau sortant normal (NAT) vers le LAN et internet.

Testé empiriquement (voir `oc exec` depuis un pod temporaire) : `echo > /dev/tcp/<IP LAN>/11434`
réussit, `echo > /dev/tcp/host.crc.testing/11434` échoue.

`openshift/deployment.yaml` référence donc directement cette IP LAN via `OLLAMA_URL`.
**Si l'IP change** (renouvellement DHCP), mets à jour cette valeur avec :

```bash
oc set env deployment/ollama-agent OLLAMA_URL=http://<nouvelle-ip>:11434/api/chat
```

Envisage une IP fixe / réservation DHCP pour la machine si ce redéploiement répété devient pénible.

## Build de l'image (registre interne)

Build binaire depuis le `Dockerfile` du repo — le cluster construit et pousse
l'image lui-même dans son registre interne, pas besoin de registre externe.
**À exécuter depuis la racine du repo** (`--from-dir=.` doit pointer sur le dossier
contenant le `Dockerfile`, pas sur `openshift/`) :

```bash
oc new-project ollama-agent   # si pas déjà fait
oc apply -f openshift/buildconfig.yaml
oc start-build ollama-agent --from-dir=. --follow
```

## Déploiement

```bash
oc apply -f openshift/deployment.yaml
oc get route ollama-agent -o jsonpath='{.spec.host}'   # URL publique
```

Pas de `securityContext` particulier nécessaire : l'image `python:3.12-slim` tourne
par défaut en root, mais l'app ne fait aucune écriture disque au runtime, donc elle
fonctionne normalement sous l'UID aléatoire non-root qu'OpenShift assigne via la SCC
`restricted` par défaut.

## Mettre à jour après un changement de code

```bash
oc start-build ollama-agent --from-dir=. --follow
oc rollout restart deployment/ollama-agent
```
