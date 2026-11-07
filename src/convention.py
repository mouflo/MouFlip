"""Mettre des clips DÉJÀ rangés à la façon de la médiathèque : « <clip>-poster.jpg » = la pochette carrée de l'album (comme folder.jpg), et l'ancienne image
paysage 16:9 du clip devient sa vignette « <clip>-thumb.jpg » (le « Thumb » d'Emby). Travaille directement sur les dossiers (pas sur la liste de MouFlip).
Jamais d'écrasement sans sauvegarde ; un album sans pochette carrée n'est pas touché."""
import shutil
import time
from pathlib import Path

from PIL import Image

VIDEOS = {".mkv", ".mp4", ".webm", ".avi", ".m4v", ".mov"}
IGNORES = {"@eadir", "extrafanart", "thumbs.db"}


def _taille(p: Path):
    with Image.open(p) as im:
        return im.size


def examiner(video: Path) -> dict | None:
    """Ce qu'il faut faire pour ce clip, ou None s'il est déjà à la convention (ou s'il n'y a rien de sûr à faire). {video, poster, thumb, folder, vignette, remplacer}."""
    album, stem = video.parent, video.stem
    poster, thumb, folder = album / f"{stem}-poster.jpg", album / f"{stem}-thumb.jpg", album / "folder.jpg"
    if not poster.is_file() or not folder.is_file():
        return None
    try:
        lp, hp = _taille(poster)
        lf, hf = _taille(folder)
    except OSError:
        return None
    if not 0.8 <= lf / hf <= 1.25:                                           # folder.jpg n'est pas une pochette carrée : on n'y touche pas
        return None
    vignette = lp / hp >= 1.4 and not thumb.exists()                         # l'ancien poster est l'image paysage du clip : elle devient la vignette
    remplacer = poster.read_bytes() != folder.read_bytes()
    if not vignette and not remplacer:
        return None
    return {"video": str(video), "poster": str(poster), "thumb": str(thumb), "folder": str(folder), "vignette": vignette, "remplacer": remplacer}


def analyser(dossier_groupe) -> list[dict]:
    """Tous les clips du dossier d'un groupe qui ne sont pas encore à la convention (lecture seule). [{titre, album, + examiner()}]."""
    racine = Path(dossier_groupe) if dossier_groupe else None
    if not racine or not racine.is_dir():
        return []
    sortie = []
    try:
        for album in sorted(racine.iterdir()):
            if not album.is_dir() or album.name.lower() in IGNORES:
                continue
            for v in sorted(album.iterdir()):
                if v.is_file() and v.suffix.lower() in VIDEOS:
                    e = examiner(v)
                    if e:
                        sortie.append({"titre": v.stem.split(" - ")[-1].strip() or v.stem, "album": album.name, **e})
    except OSError:
        pass
    return sortie


def appliquer_un(e: dict, dossier_sauvegarde) -> list[str]:
    """Fait ce que `examiner` a annoncé pour un clip. Retourne ce qui a été fait. L'ancien poster est sauvegardé AVANT d'être remplacé."""
    poster, thumb, folder = Path(e["poster"]), Path(e["thumb"]), Path(e["folder"])
    faits = []
    if e["vignette"] and not thumb.exists():
        thumb.write_bytes(poster.read_bytes())
        faits.append("vignette « -thumb.jpg » créée avec l'ancienne image du clip")
    if e["remplacer"] and poster.read_bytes() != folder.read_bytes():
        sauv = Path(dossier_sauvegarde) / time.strftime("%Y-%m-%d_%H%M%S")
        sauv.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(poster, sauv / f"{poster.parent.name} - {poster.name}")
        poster.write_bytes(folder.read_bytes())
        faits.append("poster remplacé par la pochette de l'album (l'ancien est sauvegardé)")
    return faits


def appliquer(dossier_groupe, dossier_sauvegarde) -> dict:
    """Met tous les clips du groupe à la convention. Retourne {total, faits: [(titre, [actions])], erreurs: [(titre, message)]}."""
    faits, erreurs = [], []
    lot = analyser(dossier_groupe)
    for e in lot:
        try:
            actions = appliquer_un(e, dossier_sauvegarde)
            if actions:
                faits.append((e["titre"], actions))
        except OSError as ex:
            erreurs.append((e["titre"], str(ex)))
    return {"total": len(lot), "faits": faits, "erreurs": erreurs}
