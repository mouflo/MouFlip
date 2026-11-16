# Installation de MouFlip

## Ce qu'il faut
- Un serveur **Debian 12 / Ubuntu** (un conteneur LXC suffit), accès root, Python 3.9 ou plus.
- Ton serveur **Emby** (adresse et clé API) et l'accès au dossier « Video clip » de ta médiathèque (monté sur le serveur, par exemple `/mnt/nas/Emby-Media/Video clip`).
- De la place libre pour les téléchargements en cours (**3 Go au moins** : un clip en 4K pèse 250 à 450 Mo).

## Installation rapide
```bash
git clone https://github.com/mouflo/MouFlip.git /opt/mouflip
bash /opt/mouflip/install.sh
```
Le script installe ffmpeg, l'environnement Python, **deno** (YouTube en a besoin pour que yt-dlp voie tous les formats), **AACGain** (volume sans perte), propose de créer l'identifiant, et active le service `mouflip` (port **8004**).

## Réglages (⚙️ dans la page)
| Réglage | Pour quoi |
|---|---|
| Emby : adresse + clé API | prévenir Emby après un rangement, vérifier le nom de l'artiste |
| Dossier des clips | où sont rangés les clips (« Video clip ») |
| fanart.tv | logos, bannières, fonds, pochettes (clé personnelle gratuite sur fanart.tv) |
| TheAudioDB | clé personnelle facultative (la clé publique de test est utilisée sinon) |
| Cookies YouTube | formats Premium (fichier `cookies.txt` au format Netscape exporté de ton navigateur) |
| Réduire la taille | réencode l'image en VP9 pour descendre à 60 % de la taille environ (désactivé par défaut ; ~1 min par clip sur la carte graphique, ~10 min sur le processeur) |
| Volume des clips | niveau cible (89 dB comme les thèmes MouFlopening), réglage sans perte, débit AAC de secours |

Les clés vont dans `data/secrets.env` (droits 600, jamais sur GitHub). Tu peux aussi les écrire à la main : `bash set-secret.sh NOM "valeur"`.

## Mise à jour
```bash
cd /opt/mouflip && git pull && systemctl restart mouflip
```
yt-dlp se met à jour tout seul chaque jour (YouTube change souvent).

## Dépannage
- **Journal de l'appli** : le bouton 🩺 Journal de la page, ou `journalctl -u mouflip -n 50`.
- **« MusicBrainz est injoignable »** : leur serveur est parfois surchargé. Réessaie dans un instant ; la liste déjà trouvée pour un groupe est enregistrée et reste utilisable.
- **Aucun format vidéo visible** : vérifie que `deno` est installé (`deno --version`) et que `yt-dlp-ejs` l'est dans l'environnement (`venv/bin/pip show yt-dlp-ejs`).
- **Le partage n'est pas monté** : MouFlip le dit et n'écrit rien ; monte le partage puis recharge la page.

## Option : la carte graphique Intel pour « Réduire la taille »
Si le conteneur voit `/dev/dri/renderD128`, l'encodage VP9 se fait sur la carte (environ 12 fois plus vite). Sur l'hôte Proxmox, ajouter au fichier du conteneur (`/etc/pve/lxc/<numéro>.conf`) puis `pct reboot <numéro>` :

```
lxc.cgroup2.devices.allow: c 226:0 rwm
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file
```

Dans le conteneur : `apt install vainfo intel-media-va-driver` (le pilote des cartes Intel récentes) ; `vainfo --display drm --device /dev/dri/renderD128` doit lister `VAProfileVP9Profile0 : VAEntrypointEncSliceLP`. ⚙️ Réglages → Moteur dit si la carte est vue.

