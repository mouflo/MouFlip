"""Télécharger un clip (yt-dlp, avec les cookies réglés) à la MEILLEURE qualité : résolution la plus haute puis débit le plus élevé
(« -S res,tbr »), vidéo + audio fusionnés en mkv SANS réencodage. Tout se passe dans un dossier de travail, jamais dans « Video clip »."""
import json
import logging
import shutil
import subprocess
from pathlib import Path

from src import ytcookies

logger = logging.getLogger(__name__)
ESPACE_MIN_GO = 3.0


class TelechargementError(Exception):
    pass


def place_libre_go(dossier) -> float:
    d = Path(dossier)
    d.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(d).free / 1e9


def options(dossier: Path, progression=None) -> dict:
    o = {"format": "bv*+ba/b", "format_sort": ["res", "tbr"], "merge_output_format": "mkv", "outtmpl": str(dossier / "source.%(ext)s"),
         "noplaylist": True, "quiet": True, "no_warnings": True, "retries": 5, "fragment_retries": 5, "socket_timeout": 30,
         "writethumbnail": True, "overwrites": True, "noprogress": True}
    if progression:
        o["progress_hooks"] = [progression]
    o.update(ytcookies.opts())
    return o


def telecharger(video_id: str, dossier, progression=None, ydl_cls=None) -> dict:
    """Télécharge https://www.youtube.com/watch?v=<video_id> dans `dossier`. Retourne {fichier, vignette, titre, chaine, duree}. Lève TelechargementError."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    if place_libre_go(dossier) < ESPACE_MIN_GO:
        raise TelechargementError(f"pas assez de place sur le serveur ({place_libre_go(dossier):.1f} Go libres, il en faut {ESPACE_MIN_GO:.0f})")
    if ydl_cls is None:
        from yt_dlp import YoutubeDL as ydl_cls
    try:
        with ydl_cls(options(dossier, progression)) as ydl:
            infos = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    except Exception as e:
        raise TelechargementError(f"YouTube : {str(e)[:220]}")
    fichiers = sorted(p for p in dossier.glob("source.*") if p.suffix.lower() in (".mkv", ".mp4", ".webm"))
    if not fichiers:
        raise TelechargementError("le téléchargement n'a produit aucun fichier vidéo")
    vignettes = [p for p in dossier.glob("source.*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    return {"fichier": str(fichiers[0]), "vignette": str(vignettes[0]) if vignettes else "", "titre": (infos or {}).get("title", ""),
            "chaine": (infos or {}).get("channel", ""), "duree": (infos or {}).get("duration") or 0}


def sonder(fichier) -> dict:
    """ffprobe : ce que le .nfo doit dire (codec, débit, taille, durée de la vidéo et de l'audio)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(fichier)], capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise TelechargementError(f"fichier illisible (ffprobe) : {e}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise TelechargementError("le fichier n'a aucune piste vidéo")
    fps = 0
    try:
        n, m = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = round(float(n) / float(m), 3) if float(m) else 0
    except ValueError:
        pass
    duree = float((d.get("format") or {}).get("duration") or v.get("duration") or 0)
    debit_total = int(float((d.get("format") or {}).get("bit_rate") or 0))
    debit_audio = int(float((a or {}).get("bit_rate") or (a or {}).get("tags", {}).get("BPS") or 0))
    return {"duree": duree, "taille": int(float((d.get("format") or {}).get("size") or 0)),
            "video": {"codec": v.get("codec_name"), "largeur": v.get("width"), "hauteur": v.get("height"), "fps": fps,
                      "debit": int(float(v.get("bit_rate") or 0)) or max(0, debit_total - debit_audio),
                      "aspect": v.get("display_aspect_ratio") or "16:9"},
            "audio": ({"codec": a.get("codec_name"), "debit": debit_audio, "canaux": a.get("channels"), "echantillonnage": a.get("sample_rate")} if a else {})}


def vignette_en_jpg(vignette: str, fichier_video: str, dst) -> str:
    """Poster : la vignette YouTube convertie en JPEG ; à défaut une image prise au tiers de la vidéo. Retourne le chemin, ou '' si impossible."""
    dst = Path(dst)
    essais = []
    if vignette and Path(vignette).exists():
        essais.append(["ffmpeg", "-y", "-loglevel", "error", "-i", vignette, "-q:v", "2", str(dst)])
    try:
        duree = float(sonder(fichier_video)["duree"])
    except TelechargementError:
        duree = 0
    essais.append(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(duree / 3 if duree else 5), "-i", str(fichier_video), "-frames:v", "1", "-q:v", "2", str(dst)])
    for cmd in essais:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            if dst.exists() and dst.stat().st_size > 0:
                return str(dst)
        except (OSError, subprocess.SubprocessError):
            continue
    return ""
