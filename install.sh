#!/bin/bash
# Installation de MouFlip sur un serveur Debian/Ubuntu (conteneur LXC Proxmox ou autre), à lancer en root
# une fois après le clone du dépôt (par exemple dans /opt/mouflip) :   bash /opt/mouflip/install.sh
set -e

echo "🎤 Installation de MouFlip"
echo "=========================="

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# 1. Paquets du système (ffmpeg règle le volume et fabrique les images ; curl télécharge les outils)
echo "📦 Paquets du système…"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ffmpeg >/dev/null
echo "   ✅ python3-venv, pip, git, curl, ffmpeg"

# 2. Environnement Python de l'appli
if [ ! -x venv/bin/python ]; then
    echo "🐍 Création de l'environnement Python…"
    python3 -m venv venv
fi
echo "📥 Dépendances Python (quelques minutes la première fois)…"
venv/bin/python -m pip install -q --upgrade pip
venv/bin/python -m pip install -q -r requirements.txt
mkdir -p data && chmod 700 data
[ -f config.json ] || cp config.example.json config.json
chmod +x set-login.sh set-secret.sh install-aacgain.sh 2>/dev/null || true

# 3. deno : YouTube a besoin d'un moteur JavaScript pour que yt-dlp voie tous les formats (sans lui, certains manquent)
if ! command -v deno >/dev/null 2>&1; then
    echo "🦕 Installation de deno (moteur JavaScript pour yt-dlp)…"
    TMP="$(mktemp -d)"
    curl -sL -o "$TMP/deno.zip" "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip"
    python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/deno.zip" "$TMP"
    install -m 755 "$TMP/deno" /usr/local/bin/deno
    rm -rf "$TMP"
fi
echo "   ✅ deno $(deno --version | head -1 | awk '{print $2}')"

# 4. AACGain : réglage du volume des clips SANS réencoder le son (facultatif ; sans lui le son est refait en AAC)
echo "🔊 AACGain…"
bash install-aacgain.sh || echo "   ⚠️ AACGain non installé : le volume sera réglé en refaisant le son (qualité légèrement moindre)."

# 5. Identifiant (facultatif ici : il peut aussi se créer à la première ouverture de la page, depuis le réseau local)
if ! grep -q "^APP_PASSWORD_HASH=" data/secrets.env 2>/dev/null; then
    read -r -p "🔐 Créer l'identifiant de connexion maintenant ? (o/N) " rep || rep=""
    if [ "$rep" = "o" ] || [ "$rep" = "O" ]; then
        bash set-login.sh
    else
        echo "   → Tu le créeras à la première ouverture de la page (depuis ton réseau local)."
    fi
fi

# 6. Service systemd (démarrage automatique)
echo "📋 Service systemd…"
sed "s#/opt/mouflip#$REPO_DIR#g" mouflip.service > /etc/systemd/system/mouflip.service
systemctl daemon-reload
systemctl enable -q mouflip
systemctl restart mouflip

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "✅ Installation terminée !"
echo "🌐 Ouvre http://${IP:-localhost}:8004 depuis ton réseau local."
echo "   Puis ⚙️ Réglages : dossier des clips, adresse et clé d'Emby, clé fanart.tv, cookies YouTube."
echo "📋 Journal de l'appli : journalctl -u mouflip -f"
