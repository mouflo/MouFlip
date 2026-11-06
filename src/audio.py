"""Volume des clips : même méthode et même niveau que les thèmes de MouFlopening (ReplayGain, 89 dB de référence), mesurés sur tes clips actuels
(médiane 90 dB, la plupart entre 89,4 et 90,7). Seule la piste AUDIO est refaite : l'image est recopiée telle quelle (aucun réencodage vidéo).
Un limiteur à -1 dBFS évite la saturation quand il faut monter le volume d'un morceau dynamique."""
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REFERENCE_DB = 89.0          # niveau pour lequel ReplayGain annonce un gain de 0 dB
TOLERANCE_DB = 0.5           # un clip déjà à ±0,5 dB de la cible n'est pas retouché
_GAIN = re.compile(r"track_gain\s*=\s*([+-]?\d+(?:\.\d+)?)\s*dB")
_PEAK = re.compile(r"track_peak\s*=\s*(\d+(?:\.\d+)?)")


class AudioError(Exception):
    pass


def mesurer(path) -> tuple[float, float]:
    """(niveau en dB façon MP3Gain, crête 0..1) de la première piste audio. Lève AudioError si la mesure échoue."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-vn", "-af", "replaygain", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=600).stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AudioError(f"mesure du volume impossible : {e}")
    g, p = _GAIN.search(out), _PEAK.search(out)
    if not g:
        raise AudioError("piste audio absente ou illisible (mesure du volume impossible)")
    return REFERENCE_DB - float(g.group(1)), float(p.group(1)) if p else 1.0


def normaliser(src, dst, cible_db: float = REFERENCE_DB, debit: str = "192k") -> dict:
    """Écrit dst (mkv) = image de src recopiée + audio ramené à cible_db (AAC, comme tes clips actuels).
    Retourne {avant, apres, gain, retouche, limiteur}. Ne modifie jamais src. Lève AudioError."""
    avant, _crete = mesurer(src)
    ecart = cible_db - avant
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if abs(ecart) < TOLERANCE_DB:                    # déjà au bon niveau : on recopie tout, rien n'est réencodé
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", str(dst)]
        retouche = False
    else:
        chaine = f"volume={ecart:.2f}dB,alimiter=limit=0.89:level=disabled"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
               "-af", chaine, "-c:a", "aac", "-b:a", debit, str(dst)]
        retouche = True
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise AudioError("ffmpeg est introuvable sur le serveur")
    except subprocess.CalledProcessError as e:
        raise AudioError("ffmpeg a échoué : " + (e.stderr or "")[-300:].replace("\n", " "))
    except subprocess.TimeoutExpired:
        raise AudioError("ffmpeg a pris trop de temps")
    apres, _ = mesurer(dst)
    limiteur = retouche and abs(apres - cible_db) >= 0.6
    logger.info("[Audio] %s : %.1f dB → %.1f dB (cible %.1f)%s", Path(dst).name, avant, apres, cible_db, " (limiteur actif)" if limiteur else "")
    return {"avant": round(avant, 1), "apres": round(apres, 1), "gain": round(ecart, 2) if retouche else 0.0, "retouche": retouche, "limiteur": limiteur}
