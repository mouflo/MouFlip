"""Télécharger un clip (yt-dlp, avec les cookies réglés) à la MEILLEURE qualité : résolution la plus haute puis débit le plus élevé
(« -S res,tbr »), vidéo + audio fusionnés en mkv SANS réencodage. L'audio AAC est préféré à l'Opus : AACGain règle son volume sans perte. Tout se passe dans un dossier de travail, jamais dans « Video clip »."""
import json
import logging
import re
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


def format_demande(format_id: str = "") -> str:
    """Sans choix : la meilleure image (résolution puis débit) + le son AAC. Avec un choix : CETTE image + le son AAC (jamais autre chose en silence)."""
    if format_id and re.fullmatch(r"[\w.-]{1,20}", format_id):
        return f"{format_id}+ba[acodec^=mp4a]/{format_id}+ba"
    return "bv*+ba[acodec^=mp4a]/bv*+ba/b"


def options(dossier: Path, progression=None, format_id: str = "") -> dict:
    o = {"format": format_demande(format_id), "format_sort": ["res", "tbr"], "merge_output_format": "mkv", "outtmpl": str(dossier / "source.%(ext)s"),
         "noplaylist": True, "quiet": True, "no_warnings": True, "retries": 5, "fragment_retries": 5, "socket_timeout": 30,
         "writethumbnail": True, "overwrites": True, "noprogress": True}
    if progression:
        o["progress_hooks"] = [progression]
    o.update(ytcookies.opts())
    return o


def telecharger(video_id: str, dossier, progression=None, ydl_cls=None, format_id: str = "") -> dict:
    """Télécharge https://www.youtube.com/watch?v=<video_id> dans `dossier`. Retourne {fichier, vignette, titre, chaine, duree}. Lève TelechargementError."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    if place_libre_go(dossier) < ESPACE_MIN_GO:
        raise TelechargementError(f"pas assez de place sur le serveur ({place_libre_go(dossier):.1f} Go libres, il en faut {ESPACE_MIN_GO:.0f})")
    if ydl_cls is None:
        from yt_dlp import YoutubeDL as ydl_cls
    try:
        with ydl_cls(options(dossier, progression, format_id)) as ydl:
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
    if a and not (a.get("bit_rate") or (a.get("tags") or {}).get("BPS")) and duree > 0:          # mkv n'indique pas le débit audio : somme des paquets / durée
        try:
            pk = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=size", "-of", "csv=p=0", str(fichier)],
                                capture_output=True, text=True, timeout=120).stdout.split()
            a = {**a, "bit_rate": int(sum(int(x) for x in pk if x.isdigit()) * 8 / duree)}
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
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


def images_extraites(fichier_video, dossier, combien: int = 4) -> list[dict]:
    """Quelques images prises dans la vidéo elle-même (à 12, 35, 58 et 80 % de sa durée) : autant de posters possibles. [{pourcent, fichier}]."""
    try:
        duree = float(sonder(fichier_video)["duree"])
    except TelechargementError:
        return []
    sortie = []
    for i, pct in enumerate((12, 35, 58, 80)[:combien]):
        dst = Path(dossier) / f"image_{pct}.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(duree * pct / 100), "-i", str(fichier_video), "-frames:v", "1",
                            "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", str(dst)], check=True, capture_output=True, timeout=120)
            if dst.exists() and dst.stat().st_size > 0:
                sortie.append({"pourcent": pct, "fichier": str(dst)})
        except (OSError, subprocess.SubprocessError):
            continue
    return sortie


# ----------------------------------------------------------------------------------------------------------------------------------------
# Réduire la taille : l'image est réencodée en VP9 (le codec de tes clips actuels) à qualité constante ; le son est recopié tel quel.
# Mesuré sur LEMONADE (3 passages de 20 s, SSIM image par image) : crf 30 = 50 à 53 % de la taille d'origine, qualité égale à l'AV1 de YouTube
# (SSIM 0,988-0,993) ; crf 27 = 61 % (SSIM 0,994) ; crf 34 = 41 % ; crf 38 = 32 %. Environ 8 images/s sur 8 cœurs : ~10 min pour un clip 4K de 3 min.
# ----------------------------------------------------------------------------------------------------------------------------------------
CRF_REDUCTION = {"leger": 30, "tres_peu_de_perte": 27, "plus_petit": 36}
MEMOIRE_MIN_GO = 2.5


def memoire_disponible_go() -> float:
    try:
        for ligne in open("/proc/meminfo"):
            if ligne.startswith("MemAvailable:"):
                return int(ligne.split()[1]) / 1e6
    except (OSError, ValueError):
        pass
    return 99.0


def reduire(source, dst, crf: int = 30, progression=None) -> dict:
    """Écrit dst (mkv) = image de `source` réencodée en VP9 (crf donné) + son recopié. Retourne {fait, raison, avant, apres, crf}.
    Ne réencode PAS : un clip déjà en AV1 (déjà compact), un manque de mémoire, un résultat qui n'est pas au moins 10 % plus petit (dst n'est alors pas
    créé et `source` reste le fichier à utiliser). Lève TelechargementError si ffmpeg échoue. `progression(pourcent)` est appelé pendant le travail."""
    source, dst = Path(source), Path(dst)
    avant = source.stat().st_size
    info = sonder(source)
    codec = (info["video"].get("codec") or "").lower()
    if codec == "av1":
        return {"fait": False, "raison": "déjà en AV1 (déjà compact)", "avant": avant, "apres": avant, "crf": crf}
    if memoire_disponible_go() < MEMOIRE_MIN_GO:
        return {"fait": False, "raison": f"pas assez de mémoire libre ({memoire_disponible_go():.1f} Go, il en faut {MEMOIRE_MIN_GO})", "avant": avant, "apres": avant, "crf": crf}
    duree = float(info.get("duree") or 0)
    cmd = ["nice", "-n", "10", "ffmpeg", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(int(crf)), "-row-mt", "1", "-cpu-used", "3", "-threads", "8", "-tile-columns", "2", "-g", "240",
           "-pix_fmt", "yuv420p", "-c:a", "copy", str(dst)]
    try:
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            for ligne in proc.stdout:
                if progression and duree and ligne.startswith("out_time_us="):
                    try:
                        progression(min(99, int(100 * int(ligne.split("=")[1]) / 1e6 / duree)))
                    except ValueError:
                        pass
            erreur = proc.stderr.read() or ""
            proc.wait(timeout=3600)
        if proc.returncode != 0:
            raise TelechargementError("ffmpeg a échoué : " + erreur[-300:].replace("\n", " "))
    except (OSError, subprocess.SubprocessError) as e:
        raise TelechargementError(f"réduction de la taille impossible : {e}")
    apres = dst.stat().st_size if dst.exists() else avant
    if apres > avant * 0.9:
        try:
            dst.unlink()
        except OSError:
            pass
        return {"fait": False, "raison": f"gain trop faible ({apres / avant:.0%} de l'original) : fichier d'origine gardé", "avant": avant, "apres": avant, "crf": crf}
    return {"fait": True, "raison": "", "avant": avant, "apres": apres, "crf": crf}
