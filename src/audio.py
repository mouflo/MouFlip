"""Volume des clips : même méthode et même niveau que les thèmes de MouFlopening (ReplayGain, 89 dB de référence), mesurés sur tes clips actuels
(médiane 90 dB, la plupart entre 89,4 et 90,7). Seule la piste AUDIO est refaite : l'image est recopiée telle quelle (aucun réencodage vidéo).
Un limiteur à -1 dBFS évite la saturation quand il faut monter le volume d'un morceau dynamique."""
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

REFERENCE_DB = 89.0          # niveau pour lequel ReplayGain annonce un gain de 0 dB
TOLERANCE_DB = 0.5           # un clip déjà à ±0,5 dB de la cible n'est pas retouché
PALIER_DB = 1.5              # AACGain règle le niveau par paliers de 1,5 dB (comme MP3Gain) : au mieux à ±0,75 dB de la cible
AACGAIN = Path(__file__).resolve().parent.parent / "bin" / "aacgain"
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


def aacgain_present() -> bool:
    return AACGAIN.is_file() and os.access(AACGAIN, os.X_OK)


def codec_audio(path) -> str:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        return ((json.loads(r.stdout or "{}").get("streams") or [{}])[0].get("codec_name") or "").lower()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


def normaliser_sans_perte(src, dst, cible_db: float = REFERENCE_DB) -> dict:
    """Réglage du volume SANS réencodage (AACGain, comme MP3Gain pour les thèmes) : seul un réglage de niveau est modifié dans la piste AAC,
    par paliers de 1,5 dB. L'audio est sorti du clip, réglé, puis remis avec l'image (recopiée). Lève AudioError si ce n'est pas possible
    (pas d'AACGain, son qui n'est pas de l'AAC…) : l'appelant se rabat alors sur le réencodage."""
    if not aacgain_present():
        raise AudioError("AACGain n'est pas installé")
    if codec_audio(src) != "aac":
        raise AudioError("la piste audio n'est pas de l'AAC")
    avant, crete = mesurer(src)
    paliers = round((cible_db - avant) / PALIER_DB)
    if paliers > 0 and crete > 0:                         # monter le volume ne doit pas saturer (comme l'option « sans écrêtage » de MP3Gain)
        paliers = min(paliers, int(math.floor(20 * math.log10(1.0 / crete) / PALIER_DB)))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(dst.parent)) as tmp:
        piste = Path(tmp) / "piste.m4a"
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:a:0", "-vn", "-c:a", "copy", str(piste)],
                           check=True, capture_output=True, text=True, timeout=600)
            if paliers != 0:
                r = subprocess.run([str(AACGAIN), "-q", "-g", str(paliers), "-c", str(piste)], capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    raise AudioError("AACGain a échoué : " + (r.stderr or r.stdout)[-200:].replace("\n", " "))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(piste), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(dst)],
                           check=True, capture_output=True, text=True, timeout=1800)
        except subprocess.CalledProcessError as e:
            raise AudioError("ffmpeg a échoué : " + (e.stderr or "")[-300:].replace("\n", " "))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AudioError(f"réglage sans perte impossible : {e}")
    apres, _ = mesurer(dst)
    logger.info("[Audio] %s : %.1f dB → %.1f dB (cible %.1f, %+d palier(s) AACGain, sans perte)", dst.name, avant, apres, cible_db, paliers)
    return {"avant": round(avant, 1), "apres": round(apres, 1), "gain": round(paliers * PALIER_DB, 2), "retouche": paliers != 0, "limiteur": False,
            "methode": "sans perte (AACGain)"}


def normaliser(src, dst, cible_db: float = REFERENCE_DB, debit: str = "192k", sans_perte: bool = True) -> dict:
    """Écrit dst (mkv) = image de src recopiée + audio ramené à cible_db. De préférence SANS PERTE (AACGain, piste AAC) ; sinon la piste audio
    est refaite en AAC. Retourne {avant, apres, gain, retouche, limiteur, methode}. Ne modifie jamais src. Lève AudioError."""
    if sans_perte:
        try:
            return normaliser_sans_perte(src, dst, cible_db)
        except AudioError as e:
            logger.info("[Audio] réglage sans perte impossible (%s) : la piste audio est refaite en AAC", e)
    r = _normaliser_reencode(src, dst, cible_db, debit)
    r["methode"] = f"réencodé (AAC {debit})"
    return r


def _normaliser_reencode(src, dst, cible_db: float = REFERENCE_DB, debit: str = "192k") -> dict:
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
