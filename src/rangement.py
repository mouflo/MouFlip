"""Ranger un clip téléchargé dans « Video clip/<Groupe>/<Album>/ » : plan (rien n'est écrit), puis exécution.
Règles : on ne remplace JAMAIS un fichier existant (il est signalé et laissé tel quel), le clip est COPIÉ (pas déplacé : le NAS refuse parfois
de renommer), la taille copiée est vérifiée, les images du groupe ne sont posées que si elles manquent."""
import logging
import os
import shutil
from pathlib import Path

import requests

from src import noms

logger = logging.getLogger(__name__)
UA = "MouFlip/0.1 (appli personnelle de rangement de clips)"

# image TheAudioDB -> (nom du fichier dans le dossier du groupe)
IMAGES_GROUPE = (("vignette", "folder.jpg"), ("banniere", "banner.jpg"), ("logo", "logo.png"), ("fanart", "fanart.jpg"),
                 ("fanart2", "extrafanart/fanart1.jpg"), ("fanart3", "extrafanart/fanart2.jpg"), ("fanart4", "extrafanart/fanart3.jpg"))


def plan(racine, dossier_groupe_existant, groupe, album, titre, fichier_video, poster=None, nfo_clip="", nfo_artiste="", images_groupe=None, images_album=None) -> list[dict]:
    """La liste exacte de ce qui serait créé. Chaque ligne : {type, chemin, existe, taille?}. Aucune écriture."""
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
        ajouter("image", a / "folder.jpg", url=images_album["couverture"])
    if (images_album or {}).get("cdart"):
        ajouter("image", a / "cdart.png", url=images_album["cdart"])
    if nfo_artiste:
        ajouter("nfo", g / "artist.nfo", contenu=nfo_artiste)
    for cle, nom in IMAGES_GROUPE:
        url = (images_groupe or {}).get(cle)
        if url:
            ajouter("image", g / nom, url=url)
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


def executer(lignes: list[dict]) -> dict:
    """Écrit ce que le plan annonce. Ne remplace rien d'existant. Retourne {faits, ignores, erreurs} (listes de messages)."""
    faits, ignores, erreurs = [], [], []
    for l in lignes:
        chemin = Path(l["chemin"])
        try:
            if l["type"] == "dossier":
                chemin.mkdir(parents=True, exist_ok=True)
                faits.append(f"dossier {chemin.name}")
                continue
            if chemin.exists():
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
                    r = requests.get(l["url"], headers={"User-Agent": UA}, timeout=30)
                    if r.status_code != 200 or not r.content:
                        ignores.append(f"{chemin.name} : image indisponible ({r.status_code})")
                        continue
                    chemin.parent.mkdir(parents=True, exist_ok=True)
                    chemin.write_bytes(r.content)
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
