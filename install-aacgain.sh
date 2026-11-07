#!/bin/bash
# Installe AACGain (réglage du volume des pistes AAC SANS réencodage) dans /opt/mouflip/bin, depuis la version publiée sur
# https://github.com/dgilman/aacgain/releases (2.0.0, Linux 64 bits). La somme de contrôle est vérifiée avant l'installation.
# Usage :  bash /opt/mouflip/install-aacgain.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SHA="f8a4a54c65ecb20f4de4754dabb7ecc5975fcfb59353095c1310f9db2a5b152a"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -sL -o "$TMP/aacgain" "https://github.com/dgilman/aacgain/releases/download/2.0.0/aacgain-2.0.0-linux-amd64"
echo "$SHA  $TMP/aacgain" | sha256sum -c - >/dev/null || { echo "❌ Somme de contrôle différente : rien n'a été installé."; exit 1; }
mkdir -p "$DIR/bin"; install -m 755 "$TMP/aacgain" "$DIR/bin/aacgain"
echo "✅ AACGain installé dans $DIR/bin/aacgain"
