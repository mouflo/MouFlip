"""Ranger un clip téléchargé dans « Video clip/<Groupe>/<Album>/ » : plan (rien n'est écrit), puis exécution.
Règles : on ne remplace JAMAIS un fichier existant (il est signalé et laissé tel quel), le clip est COPIÉ (pas déplacé : le NAS refuse parfois
de renommer), la taille copiée est vérifiée, les images du groupe ne sont posées que si elles manquent."""
import logging
import os
import shutil
import time
from pathlib import Path

import requests

from src import noms

logger = logging.getLogger(__name__)
UA = "MouFlip/0.1 (appli personnelle de rangement de clips)"

# image TheAudioDB -> (nom du fichier dans le dossier du groupe)
IMAGES_GROUPE = (("vignette", "folder.jpg"), ("banniere", "banner.jpg"), ("logo", "logo.png"), ("fanart", "fanart.jpg"),
                 ("fanart2", "extrafanart/fanart1.jpg"), ("fanart3", "extrafanart/fanart2.jpg"), ("fanart4", "extrafanart/fanart3.jpg"))


def plan(racine, dossier_groupe_existant, groupe, album, titre, fichier_video, poster=None, nfo_clip="", nfo_artiste="", images_groupe=None, images_album=None,
         remplacer=()) -> list[dict]:
    """La liste exacte de ce qui serait créé. Chaque ligne : {type, chemin, existe, cle?, remplacer?, taille?}. Aucune écriture.
    `remplacer` : clés d'images que l'utilisatrice a CHOISI de remplacer (vignette, banniere, logo, album_couverture, album_cdart) : l'ancienne est sauvegardée."""
    racine = Path(racine)
    g = racine / (Path(dossier_groupe_existant).name if dossier_groupe_existant else noms.propre(groupe))
    a = g / noms.propre(album)
    base = noms.nom_de_clip(groupe, album, titre, Path(fichier_video).suffix.lstrip(".") or "mkv")
    stem = Path(base).stem
    lignes = []

    def ajouter(type_, chemin, **extra):
        lignes.append({"type": type_, "chemin": str(chemin), "existe": Path(chemin).exists(), **extra})

    if not g.exists():
        ajouter("dossier", g)
    if not a.exists():
        ajouter("dossier", a)
    ajouter("video", a / base, source=str(fichier_video), taille=Path(fichier_video).stat().st_size if Path(fichier_video).exists() else 0)
    if nfo_clip:
        ajouter("nfo", a / f"{stem}.nfo", contenu=nfo_clip)
    if poster:
        ajouter("image", a / f"{stem}-poster.jpg", source=str(poster))
        if not (images_album or {}).get("couverture"):
            ajouter("image", a / "folder.jpg", source=str(poster))          # pas de pochette connue : le poster du premier clip (jamais remplacé ensuite)
    if (images_album or {}).get("couverture"):
        ajouter("image", a / "folder.jpg", url=images_album["couverture"], cle="album_couverture", remplacer="album_couverture" in remplacer)
    if (images_album or {}).get("cdart"):
        ajouter("image", a / "cdart.png", url=images_album["cdart"], cle="album_cdart", remplacer="album_cdart" in remplacer)
    if nfo_artiste:
        ajouter("nfo", g / "artist.nfo", contenu=nfo_artiste)
    for cle, nom in IMAGES_GROUPE:
        url = (images_groupe or {}).get(cle)
        if url:
            ajouter("image", g / nom, url=url, cle=cle, remplacer=cle in remplacer)
    return lignes


def _copier_verifie(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if dst.stat().st_size != src.stat().st_size:
        try:
            dst.unlink()
        except OSError:
            pass
        raise OSError(f"copie incomplète vers {dst} ({dst.stat().st_size if dst.exists() else 0} octets sur {src.stat().st_size})")


def _telecharger_image(url: str):
    """Octets de l'image, ou (None, message) si indisponible."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    if r.status_code != 200 or not r.content:
        return None, f"image indisponible ({r.status_code})"
    return r.content, ""


def executer(lignes: list[dict], sauvegarde=None) -> dict:
    """Écrit ce que le plan annonce. Ne remplace rien d'existant, SAUF une image que l'utilisatrice a choisi de remplacer (ligne « remplacer ») :
    l'ancienne est alors copiée dans `sauvegarde` d'abord, et la nouvelle doit être récupérée entièrement avant que l'ancienne soit touchée.
    Retourne {faits, ignores, erreurs} (listes de messages)."""
    faits, ignores, erreurs = [], [], []
    for l in lignes:
        chemin = Path(l["chemin"])
        try:
            if l["type"] == "dossier":
                chemin.mkdir(parents=True, exist_ok=True)
                faits.append(f"dossier {chemin.name}")
                continue
            if chemin.exists() and not (l.get("remplacer") and l["type"] == "image" and sauvegarde):
                ignores.append(f"{chemin.name} existe déjà : laissé tel quel")
                continue
            if l["type"] == "video":
                _copier_verifie(Path(l["source"]), chemin)
            elif l["type"] == "nfo":
                chemin.parent.mkdir(parents=True, exist_ok=True)
                chemin.write_text(l["contenu"], encoding="utf-8")
            elif l["type"] == "image":
                if l.get("source"):
                    _copier_verifie(Path(l["source"]), chemin)
                else:
                    contenu, pb = _telecharger_image(l["url"])
                    if contenu is None:
                        ignores.append(f"{chemin.name} : {pb}" + (" (l'ancienne est gardée)" if chemin.exists() else ""))
                        continue
                    if chemin.exists():                                    # remplacement voulu : sauvegarde d'abord
                        dossier_sauv = Path(sauvegarde) / time.strftime("%Y-%m-%d_%H%M%S")
                        dossier_sauv.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(chemin, dossier_sauv / f"{chemin.parent.name} - {chemin.name}")
                        faits.append(f"{chemin.name} remplacée (l'ancienne est dans {dossier_sauv})")
                        chemin.write_bytes(contenu)
                        continue
                    chemin.parent.mkdir(parents=True, exist_ok=True)
                    chemin.write_bytes(contenu)
            faits.append(chemin.name)
        except (OSError, requests.RequestException) as e:
            logger.error("Rangement : %s : %s", chemin, e)
            erreurs.append(f"{chemin.name} : {e}")
            if l["type"] == "video":
                break                                    # sans le clip, le reste n'a pas de sens
    return {"faits": faits, "ignores": ignores, "erreurs": erreurs}


def prevenir_emby(emby, chemin_groupe: str) -> str:
    """Demande à Emby de relire le dossier du groupe (POST /Library/Media/Updated). Ne lève jamais : retourne un message."""
    if not emby.configured:
        return "Emby n'est pas configuré (clé API) : pense à actualiser le dossier à la main."
    try:
        emby._request("POST", "/Library/Media/Updated", json={"Updates": [{"Path": str(chemin_groupe), "UpdateType": "Created"}]})
        return "Emby est prévenu : il relit le dossier du groupe."
    except Exception as e:
        return f"Emby n'a pas pu être prévenu ({e}) : le clip est rangé, actualise le dossier à la main."
