"""Les clips déjà présents dans « Video clip/<Groupe>/ » : lecture seule, jamais rien d'écrit.
Ignore ce que Synology et Emby fabriquent (@eaDir, Thumbs.db, .bif, vignettes -poster, -320-10)."""
import re
from pathlib import Path

from src.noms import normaliser

VIDEOS = {".mkv", ".mp4", ".webm", ".avi", ".m4v", ".mov", ".ts"}
_IGNORE = {"@eadir", "thumbs.db", "extrafanart", ".ds_store"}


def dossier_du_groupe(racine, noms) -> Path | None:
    """Le dossier du groupe dans la racine : comparaison sans majuscules ni accents, avec tous ses noms connus (nom, alias)."""
    racine = Path(racine)
    if not racine.is_dir():
        return None
    voulus = {normaliser(n) for n in noms if n}
    voulus.discard("")
    try:
        for d in sorted(racine.iterdir()):
            if d.is_dir() and d.name.lower() not in _IGNORE and normaliser(d.name) in voulus:
                return d
    except OSError:
        return None
    return None


def _titre_nfo(video: Path) -> str:
    nfo = video.with_suffix(".nfo")
    try:
        m = re.search(r"<title>([^<]*)</title>", nfo.read_text(encoding="utf-8-sig", errors="ignore"))
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def lister(dossier_groupe) -> list[dict]:
    """Chaque vidéo du groupe : album (dossier), nom du fichier, titre (celui du .nfo s'il existe, sinon déduit du nom)."""
    d = Path(dossier_groupe) if dossier_groupe else None
    sortie = []
    if not d or not d.is_dir():
        return sortie
    try:
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in VIDEOS:
                continue
            if any(part.lower() in _IGNORE for part in p.relative_to(d).parts):
                continue
            rel = p.relative_to(d)
            album = rel.parts[0] if len(rel.parts) > 1 else ""
            sortie.append({"fichier": str(p), "album": album, "stem": p.stem, "titre_nfo": _titre_nfo(p)})
    except OSError:
        pass
    return sortie


def _titre_du_nom(stem: str) -> str:
    """« Groupe - Album - Titre » → « Titre » (dernier morceau) ; un nom sans tiret reste entier."""
    morceaux = [m.strip() for m in re.split(r"\s+-\s+", stem) if m.strip()]
    return morceaux[-1] if morceaux else stem


def deja_la(titre: str, locaux: list[dict]) -> str:
    """Chemin du fichier local qui correspond à ce titre, sinon ''. Ordre de confiance : titre du .nfo, dernier morceau du nom du fichier,
    puis (titres de 2 mots ou plus seulement) titre entier retrouvé quelque part dans le nom."""
    n = normaliser(titre)
    if len(n) < 2:
        return ""
    for v in locaux:
        if v["titre_nfo"] and normaliser(v["titre_nfo"]) == n:
            return v["fichier"]
    for v in locaux:
        if normaliser(_titre_du_nom(v["stem"])) == n:
            return v["fichier"]
    if len(n.split()) >= 2:
        for v in locaux:
            if (" " + n + " ") in (" " + normaliser(v["stem"]) + " "):
                return v["fichier"]
    return ""
