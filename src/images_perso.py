"""Images ajoutées à la main (fichier envoyé depuis le téléphone, ou adresse d'une image) : pochette, logo, bannière, fonds d'écran, poster.
Rangées dans data/images_perso/<id de l'artiste>/ avec un index ; vérifiées avec Pillow (une vraie image, pas trop grosse), remises au format
qu'Emby attend (logo et cdart en PNG, le reste en JPEG), et jamais lues depuis une adresse du réseau local."""
import hashlib
import io
import ipaddress
import json
import logging
import os
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
UA = "MouFlip/0.1 (appli personnelle de rangement de clips)"
TYPES = ("logo", "vignette", "banniere", "fanart", "couverture", "cdart", "poster")
FORMAT = {"logo": "PNG", "cdart": "PNG"}                         # les autres : JPEG
MAX_OCTETS = 15 * 1024 * 1024
MIN_COTE, MAX_COTE = 100, 4000
_DOSSIER = {"racine": None}


class ImageError(Exception):
    pass


def configurer(dossier_donnees):
    _DOSSIER["racine"] = Path(dossier_donnees) / "images_perso"


def _dossier(mbid: str) -> Path:
    return _DOSSIER["racine"] / mbid


def _index(mbid: str) -> list[dict]:
    if _DOSSIER["racine"] is None:                                  # pas configuré (script, essai) : aucune image ajoutée
        return []
    try:
        return json.loads((_dossier(mbid) / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _ecrire_index(mbid: str, liste: list):
    d = _dossier(mbid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "index.tmp"
    tmp.write_text(json.dumps(liste, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "index.json")


def lister(mbid: str, type_: str = "", album_norm: str = "") -> list[dict]:
    """Les images ajoutées pour cet artiste, d'un type donné ; pochette et cdart seulement pour le même album."""
    sortie = []
    for e in _index(mbid):
        if type_ and e["type"] != type_:
            continue
        if e["type"] in ("couverture", "cdart") and album_norm and e.get("album") != album_norm:
            continue
        if (_dossier(mbid) / e["fichier"]).is_file():
            sortie.append(e)
    return sortie


def chemin(mbid: str, image_id: str) -> Path | None:
    for e in _index(mbid):
        if e["id"] == image_id and (_dossier(mbid) / e["fichier"]).is_file():
            return _dossier(mbid) / e["fichier"]
    return None


def _hote_public(url: str):
    """Lève ImageError si l'adresse n'est pas http(s) ou pointe vers le réseau local (on ne lit jamais autre chose qu'Internet)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ImageError("Colle l'adresse d'une image (https://…).")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        raise ImageError("Cette adresse est introuvable.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ImageError("Cette adresse n'est pas une adresse d'Internet : elle est refusée.")


def telecharger(url: str) -> tuple[bytes, str]:
    """(octets, nom) d'une image prise sur Internet. Lève ImageError (en français)."""
    url = (url or "").strip()
    _hote_public(url)
    try:
        with requests.get(url, headers={"User-Agent": UA}, timeout=20, stream=True, allow_redirects=False) as r:
            if 300 <= r.status_code < 400:                        # une redirection pourrait mener au réseau local : on la refait en la vérifiant
                suite = r.headers.get("Location", "")
                _hote_public(suite)
                return telecharger_sans_redirection(suite)
            if r.status_code != 200:
                raise ImageError(f"Le site a répondu {r.status_code}.")
            donnees = b""
            for morceau in r.iter_content(65536):
                donnees += morceau
                if len(donnees) > MAX_OCTETS:
                    raise ImageError("Cette image est trop grosse (15 Mo au plus).")
    except requests.RequestException as e:
        raise ImageError(f"Impossible de lire cette adresse : {str(e)[:100]}")
    return donnees, urlparse(url).path.rsplit("/", 1)[-1] or "image"


def telecharger_sans_redirection(url: str) -> tuple[bytes, str]:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=False)
    except requests.RequestException as e:
        raise ImageError(f"Impossible de lire cette adresse : {str(e)[:100]}")
    if r.status_code != 200 or len(r.content) > MAX_OCTETS:
        raise ImageError("Image illisible ou trop grosse.")
    return r.content, urlparse(url).path.rsplit("/", 1)[-1] or "image"


def preparer(type_: str, octets: bytes) -> tuple[bytes, str]:
    """Vérifie l'image et la remet au format attendu (PNG pour logo et cdart, sinon JPEG). Retourne (octets, extension). Lève ImageError."""
    contenu, ext, _taille = _preparer(type_, octets)
    return contenu, ext


def _preparer(type_: str, octets: bytes):
    if type_ not in TYPES:
        raise ImageError("Type d'image inconnu.")
    if not octets or len(octets) > MAX_OCTETS:
        raise ImageError("Image vide ou trop grosse (15 Mo au plus).")
    try:
        im = Image.open(io.BytesIO(octets))
        im.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("Ce fichier n'est pas une image lisible (JPEG, PNG, WebP…).")
    im = ImageOps.exif_transpose(im)
    if min(im.size) < MIN_COTE:
        raise ImageError(f"Image trop petite ({im.size[0]}×{im.size[1]} px) : au moins {MIN_COTE} px de côté.")
    if max(im.size) > MAX_COTE:
        im.thumbnail((MAX_COTE, MAX_COTE), Image.LANCZOS)
    fmt = FORMAT.get(type_, "JPEG")
    if fmt == "JPEG":
        if im.mode in ("RGBA", "LA", "P"):
            fond = Image.new("RGB", im.size, (0, 0, 0))
            im = im.convert("RGBA")
            fond.paste(im, mask=im.split()[-1])
            im = fond
        else:
            im = im.convert("RGB")
    else:
        im = im.convert("RGBA")
    tampon = io.BytesIO()
    im.save(tampon, fmt, **({"quality": 93, "optimize": True} if fmt == "JPEG" else {"optimize": True}))
    return tampon.getvalue(), ("png" if fmt == "PNG" else "jpg"), im.size


def ajouter(mbid: str, type_: str, octets: bytes, nom: str = "", album_norm: str = "") -> dict:
    """Vérifie l'image, la remet au bon format et l'enregistre. Retourne l'entrée {id, type, album, fichier, nom, largeur, hauteur}. Lève ImageError."""
    contenu, ext, taille = _preparer(type_, octets)
    image_id = hashlib.sha1(contenu).hexdigest()[:12]
    d = _dossier(mbid)
    d.mkdir(parents=True, exist_ok=True)
    fichier = f"{type_}_{image_id}.{ext}"
    (d / fichier).write_bytes(contenu)
    entree = {"id": image_id, "type": type_, "album": album_norm if type_ in ("couverture", "cdart") else "", "fichier": fichier,
              "nom": (nom or "")[:120], "largeur": taille[0], "hauteur": taille[1], "ajoute": time.strftime("%Y-%m-%d")}
    liste = [e for e in _index(mbid) if e["id"] != image_id]
    liste.append(entree)
    _ecrire_index(mbid, liste)
    return entree
