"""Le SON STUDIO d'un clip (pas la bande-son du clip, qui a parfois des bruits et des pauses) : la version « Artiste – Topic » de YouTube,
c'est-à-dire le titre tel que le label l'a publié. Téléchargé dans data/sons/<id>/, volume réglé (89 dB, AACGain sans perte), puis rangé dans
la bibliothèque « Musique » SEULEMENT sur confirmation : Artiste/Album/« Artiste - Album - NN - Titre.mp3|m4a », jamais de remplacement d'un fichier existant."""
import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from src import audio, existants, playlists, rangement, ytcookies
from src.noms import normaliser, propre

logger = logging.getLogger(__name__)
_VERROU = threading.RLock()
SONS: dict[str, dict] = {}
ACTIFS = ("attente", "recherche", "telechargement", "conversion", "volume")
_CFG = {"dossier": None, "racine_musique": lambda: "", "racine_clips": lambda: "", "reglage": lambda: {"format": "m4a", "debit_m4a": "256k", "debit_mp3": "320k"},
        "cible": lambda: audio.REFERENCE_DB, "emby": None, "ecriture": True, "pistes": None}
FORMATS = ("m4a", "mp3")


class SonError(Exception):
    pass


def configurer(dossier_donnees, racine_musique_fn, racine_clips_fn, reglage_fn, cible_fn, emby, pistes_fn=None, ecriture=True):
    _CFG.update(dossier=Path(dossier_donnees) / "sons", racine_musique=racine_musique_fn, racine_clips=racine_clips_fn, reglage=reglage_fn, cible=cible_fn,
                emby=emby, pistes=pistes_fn, ecriture=ecriture)
    _charger()


def _fichier_etat() -> Path:
    return _CFG["dossier"] / "etat.json"


def _sauver():
    if not _CFG["ecriture"]:
        return
    _CFG["dossier"].mkdir(parents=True, exist_ok=True)
    tmp = _fichier_etat().with_suffix(".tmp")
    with _VERROU:
        tmp.write_text(json.dumps(SONS, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _fichier_etat())


def _charger():
    try:
        d = json.loads(_fichier_etat().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    with _VERROU:
        SONS.clear()
        SONS.update(d)
        for c in SONS.values():                                   # un redémarrage a coupé un travail : il reprend depuis le début
            if c["etat"] in ACTIFS:
                c["etat"], c["message"] = "attente", "Reprise après un redémarrage."


def _maj(c, **kw):
    with _VERROU:
        c.update(kw)
    _sauver()


def lister() -> list[dict]:
    with _VERROU:
        return [dict(c) for c in SONS.values()]


def occupe() -> list[str]:
    return [f"{c['artiste']} - {c['titre']}" for c in lister() if c["etat"] in ACTIFS]


# ---------- recherche de la version studio ----------
def _titre_nu(t):
    return playlists.titre_cle(t)


def _est_audio_label(e: dict, na: str) -> bool:
    """Le son publié par le label via la distribution musicale de YouTube (description « Provided to YouTube by… », ou chaîne « … - Topic »),
    et dont l'artiste est bien celui qu'on cherche."""
    chaine = normaliser(e.get("channel") or e.get("uploader") or "")
    artistes = normaliser(str(e.get("artist") or ""))
    topic = chaine.endswith("topic") and (chaine[:-5].strip() in na or na in chaine[:-5].strip()) and chaine[:-5].strip() != ""
    fourni = (e.get("description") or "").lstrip().lower().startswith("provided to youtube by")
    qui = bool(artistes) and (na in artistes or artistes.split()[0:1] == na.split()[0:1] and len(na) > 2) or (chaine and (na in chaine or chaine in na))
    return topic or (fourni and bool(qui))


def choisir(artiste: str, titre: str, duree: float, resultats: list[dict]) -> dict | None:
    """Le son du label (voir _est_audio_label) du même titre, sans remix, live ni instrumental, de durée plausible ; le plus proche de la durée du clip d'abord."""
    na, nt = normaliser(artiste), _titre_nu(titre)
    bons = []
    for e in resultats:
        t = e.get("track") or e.get("title") or ""
        if _titre_nu(t) != nt or playlists.version_alternative(e.get("title") or t) or not _est_audio_label(e, na):
            continue
        d = e.get("duration") or 0
        if duree and d and not 0.55 * duree <= d <= 1.4 * duree:
            continue
        bons.append((abs((d or duree or 0) - (duree or 0)), e))
    if not bons:
        return None
    e = min(bons, key=lambda x: x[0])[1]
    return {"id": e.get("id"), "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"), "duration": e.get("duration"), "album": e.get("album") or ""}


def trouver(artiste: str, titre: str, duree: float = 0, chercher=None, detailler=None) -> dict | None:
    """YouTube Musique (recherche « titres ») : les morceaux de ce nom, puis la fiche détaillée de chacun jusqu'au premier qui est le son du label."""
    from yt_dlp import YoutubeDL
    base = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 25}
    base.update(ytcookies.opts())

    def _chercher(q):
        return YoutubeDL({**base, "extract_flat": True}).extract_info(f"https://music.youtube.com/search?q={q.replace(' ', '+')}#songs", download=False)

    def _detailler(i):
        return YoutubeDL({**base, "noplaylist": True}).extract_info(f"https://www.youtube.com/watch?v={i}", download=False)
    r = (chercher or _chercher)(f"{artiste} {titre}") or {}
    nt = _titre_nu(titre)
    candidats = [e for e in r.get("entries") or [] if e and _titre_nu(e.get("title") or "") == nt][:6]
    na = normaliser(artiste)
    for e in candidats:
        try:
            info = (detailler or _detailler)(e["id"]) or {}
        except Exception as ex:
            logger.info("[Sons] fiche illisible (%s) : %s", e.get("id"), str(ex)[:100])
            continue
        v = choisir(artiste, titre, duree, [{**info, "id": info.get("id") or e["id"]}])
        if v:
            return v
    return None


# ---------- fichier ----------
def _sonder(chemin) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,bit_rate:format=duration", "-of", "json", str(chemin)],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout or "{}")
    s = (d.get("streams") or [{}])[0]
    return {"codec": s.get("codec_name", ""), "debit": int(s.get("bit_rate") or 0), "duree": float((d.get("format") or {}).get("duration") or 0)}


def convertir(src, dst, format_: str, debit: str, etiquettes: dict) -> str:
    """Écrit dst (m4a ou mp3) avec les étiquettes. m4a : le son AAC de YouTube est RECOPIÉ tel quel (aucune perte), sinon refait en AAC ; mp3 : toujours refait."""
    meta = []
    for k, v in etiquettes.items():
        if v:
            meta += ["-metadata", f"{k}={v}"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-map", "0:a:0", "-map_metadata", "-1"]
    if format_ == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", debit, "-id3v2_version", "3"]
        methode = f"MP3 {debit}"
    elif _sonder(src)["codec"] == "aac":
        cmd += ["-c:a", "copy"]
        methode = "AAC d'origine recopié"
    else:
        cmd += ["-c:a", "aac", "-b:a", debit]
        methode = f"AAC {debit}"
    cmd += meta + [str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
    except subprocess.CalledProcessError as e:
        raise SonError("ffmpeg a échoué : " + (e.stderr or "")[-250:].replace("\n", " "))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SonError(f"conversion impossible : {e}")
    return methode


def regler_volume(chemin, cible_db: float, debit: str = "256k") -> dict:
    """Volume ramené à cible_db : AACGain règle le niveau SANS réencodage (m4a et mp3) ; sinon le son est refait avec un gain."""
    avant, crete = audio.mesurer(chemin)
    paliers = round((cible_db - avant) / audio.PALIER_DB)
    if paliers > 0 and crete > 0:
        paliers = min(paliers, int(math.floor(20 * math.log10(1.0 / crete) / audio.PALIER_DB)))
    if paliers == 0:
        return {"avant": round(avant, 1), "apres": round(avant, 1), "methode": "déjà au bon niveau"}
    if audio.aacgain_present():
        r = subprocess.run([str(audio.AACGAIN), "-q", "-s", "s", "-g", str(paliers), "-c", str(chemin)], capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            apres, _ = audio.mesurer(chemin)
            return {"avant": round(avant, 1), "apres": round(apres, 1), "methode": "sans perte (AACGain)"}
        logger.info("[Sons] AACGain a échoué (%s) : son refait", (r.stderr or r.stdout)[-150:])
    tmp = Path(chemin).with_name("volume" + Path(chemin).suffix)
    codec = ["-c:a", "libmp3lame", "-b:a", "320k"] if str(chemin).endswith(".mp3") else ["-c:a", "aac", "-b:a", debit]
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(chemin), "-map_metadata", "0", "-af", f"volume={cible_db - avant:.2f}dB,alimiter=limit=0.89:level=disabled"]
                       + codec + [str(tmp)], check=True, capture_output=True, text=True, timeout=900)
    except subprocess.CalledProcessError as e:
        raise SonError("réglage du volume impossible : " + (e.stderr or "")[-200:].replace("\n", " "))
    os.replace(tmp, chemin)
    apres, _ = audio.mesurer(chemin)
    return {"avant": round(avant, 1), "apres": round(apres, 1), "methode": "son refait avec un gain"}


# ---------- travaux ----------
def ajouter(artiste: str, mbid: str, clip: dict, alias=()) -> dict:
    """Met un morceau en file d'attente pour son son studio. Refuse un doublon actif ou déjà prêt."""
    with _VERROU:
        for c in SONS.values():
            if c["mbid"] == mbid and normaliser(c["titre"]) == normaliser(clip["titre"]) and c["etat"] in ACTIFS + ("pret",):
                return c
        tid = uuid.uuid4().hex[:10]
        c = {"id": tid, "artiste": artiste, "alias": list(alias), "mbid": mbid, "titre": clip["titre"], "album": clip.get("album") or clip["titre"],
             "annee": str(clip.get("annee") or ""), "rg": clip.get("rg") or "", "duree": clip.get("duree") or 0, "etat": "attente", "message": "En attente.",
             "cree": time.time()}
        SONS[tid] = c
    _sauver()
    _reveiller()
    return c


def _dossier_artiste(c) -> Path:
    racine = Path(_CFG["racine_musique"]())
    return existants.dossier_du_groupe(racine, [c["artiste"]] + c.get("alias", [])) or (racine / propre(c["artiste"]))


def _deja_dans_la_musique(c) -> str:
    """Un morceau de ce titre existe déjà dans le dossier de l'artiste (au plus 2 niveaux) : chemin, sinon ''."""
    d = _dossier_artiste(c)
    if not d.is_dir():
        return ""
    nt = _titre_nu(c["titre"])
    try:
        for f in list(d.glob("*/*")) + list(d.glob("*")):
            if f.suffix.lower() in (".mp3", ".m4a", ".flac", ".opus") and _titre_nu(f.stem.split(" - ")[-1]) == nt:
                return str(f)
    except OSError:
        pass
    return ""


def _preparer(c):
    existant = _deja_dans_la_musique(c)
    if existant:
        return _maj(c, etat="deja", message=f"Déjà dans ta musique : {Path(existant).name}")
    _maj(c, etat="recherche", message="Recherche de la version studio…")
    v = trouver(c["artiste"], c["titre"], c.get("duree") or 0)
    if not v:
        return _maj(c, etat="erreur", message="Pas de version studio « Topic » trouvée sur YouTube pour ce titre (rien n'est proposé à la place : ni live, ni remix).")
    rep = _CFG["dossier"] / c["id"]
    shutil.rmtree(rep, ignore_errors=True)
    rep.mkdir(parents=True, exist_ok=True)
    _maj(c, etat="telechargement", message=f"Téléchargement : {v['title']} ({v['channel']})", video=v)
    from yt_dlp import YoutubeDL
    o = {"format": "bestaudio[acodec^=mp4a]/bestaudio", "outtmpl": str(rep / "source.%(ext)s"), "noplaylist": True, "quiet": True, "no_warnings": True,
         "retries": 5, "socket_timeout": 30, "overwrites": True, "noprogress": True}
    o.update(ytcookies.opts())
    try:
        with YoutubeDL(o) as y:
            y.extract_info(f"https://www.youtube.com/watch?v={v['id']}", download=True)
    except Exception as e:
        raise SonError(f"YouTube : {str(e)[:200]}")
    sources_ = [p for p in rep.glob("source.*")]
    if not sources_:
        raise SonError("le téléchargement n'a produit aucun fichier")
    reglage = _CFG["reglage"]()
    fmt = reglage["format"] if reglage["format"] in FORMATS else "m4a"
    num, total = (1, 1)
    if _CFG["pistes"] and c.get("rg"):
        try:
            num, total = _CFG["pistes"](c["rg"], c["titre"]) or (1, 1)
        except Exception as e:
            logger.info("[Sons] numéro de piste introuvable (%s) : 01", e)
    _maj(c, etat="conversion", message="Conversion et étiquettes…", piste=num, pistes=total, format=fmt)
    final = rep / f"final.{fmt}"
    methode = convertir(sources_[0], final, fmt, reglage["debit_mp3"] if fmt == "mp3" else reglage["debit_m4a"],
                        {"title": c["titre"], "artist": c["artiste"], "album_artist": c["artiste"], "album": c["album"], "date": c["annee"], "track": f"{num}/{total}"})
    _maj(c, etat="volume", message="Réglage du volume…")
    vol = regler_volume(final, _CFG["cible"](), reglage["debit_m4a"])
    for p in sources_:
        p.unlink(missing_ok=True)
    info = _sonder(final)
    _maj(c, etat="pret", fichier=str(final), taille=final.stat().st_size, volume=vol, conversion=methode, debit=info["debit"], duree_son=info["duree"],
         message=f"Prêt à ranger : {fmt.upper()} · {methode} · volume {vol['avant']} → {vol['apres']} dB ({vol['methode']}).")


_REVEIL = threading.Event()
_DEMARRE = [False]


def _reveiller():
    _REVEIL.set()


def _boucle():
    while True:
        _REVEIL.wait(timeout=30)
        _REVEIL.clear()
        while True:
            with _VERROU:
                suivant = next((c for c in SONS.values() if c["etat"] == "attente"), None)
            if not suivant:
                break
            try:
                _preparer(suivant)
            except (SonError, audio.AudioError) as e:
                _maj(suivant, etat="erreur", message=str(e))
            except Exception as e:
                logger.exception("[Sons] %s", suivant.get("titre"))
                _maj(suivant, etat="erreur", message=f"Erreur inattendue : {e}")


def demarrer():
    if not _DEMARRE[0]:
        _DEMARRE[0] = True
        _CFG["ecriture"] = True
        threading.Thread(target=_boucle, daemon=True).start()
        _reveiller()


def relancer(tid) -> dict:
    with _VERROU:
        c = SONS.get(tid)
    if not c or c["etat"] not in ("erreur", "deja"):
        return {"ok": False, "error": "Ce son ne peut pas être relancé."}
    _maj(c, etat="attente", message="En attente.")
    _reveiller()
    return {"ok": True}


def supprimer(tid) -> dict:
    with _VERROU:
        c = SONS.get(tid)
        if not c or c["etat"] in ACTIFS:
            return {"ok": False, "error": "Introuvable, ou en cours de traitement."}
        SONS.pop(tid)
    shutil.rmtree(_CFG["dossier"] / tid, ignore_errors=True)
    _sauver()
    return {"ok": True}


def plan(c) -> dict:
    """Où le fichier sera rangé (aucune écriture)."""
    d = _dossier_artiste(c)
    album = None
    if d.is_dir():
        album = next((a for a in sorted(d.iterdir()) if a.is_dir() and a.name != "@eaDir" and normaliser(a.name) == normaliser(c["album"])), None)
    album = album or d / propre(c["album"])
    ext = Path(c.get("fichier", "x.m4a")).suffix
    nom = propre(f"{c['artiste']} - {c['album']} - {int(c.get('piste') or 1):02d} - {c['titre']}") + ext
    return {"dossier_artiste": str(d), "dossier_album": str(album), "chemin": str(album / nom), "album_nouveau": not album.exists(), "existe": (album / nom).exists()}


def ranger(tid) -> dict:
    with _VERROU:
        c = SONS.get(tid)
    if not c or c["etat"] != "pret":
        return {"ok": False, "error": "Ce son n'est pas prêt à ranger."}
    racine = Path(_CFG["racine_musique"]())
    if not racine.is_dir():
        return {"ok": False, "error": f"Le dossier de la musique « {racine} » est introuvable (le partage est-il monté ?). Rien n'a été écrit."}
    src = Path(c["fichier"])
    if not src.exists():
        return {"ok": False, "error": "Le fichier préparé a disparu : relance ce son."}
    p = plan(c)
    dest = Path(p["chemin"])
    if dest.exists():
        return {"ok": False, "error": f"{dest.name} existe déjà : rien n'a été remplacé."}
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name("." + dest.name + ".part")
        shutil.copyfile(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            tmp.unlink(missing_ok=True)
            raise OSError("copie incomplète")
        os.replace(tmp, dest)
        if p["album_nouveau"]:                                         # pochette : celle de l'album rangé avec le clip, si elle existe (jamais de remplacement)
            clips = existants.dossier_du_groupe(_CFG["racine_clips"](), [c["artiste"]] + c.get("alias", []))
            for nom in ("folder.jpg", "folder.png"):
                cible = dest.parent / nom
                origine = (clips / propre(c["album"]) / nom) if clips else None
                if origine and origine.exists() and not cible.exists():
                    shutil.copyfile(origine, cible)
                    break
    except OSError as e:
        return {"ok": False, "error": f"Rangement impossible : {e}"}
    emby = rangement.prevenir_emby(_CFG["emby"], p["dossier_artiste"]) if _CFG["emby"] else ""
    shutil.rmtree(_CFG["dossier"] / tid, ignore_errors=True)
    _maj(c, etat="range", message=f"Rangé : {dest.name}", chemin=str(dest), fichier="")
    logger.info("[Sons] rangé : %s - %s (%s)", c["artiste"], c["titre"], emby)
    return {"ok": True, "chemin": str(dest), "emby": emby}


def ranger_tous() -> dict:
    """Range tous les sons prêts (un par un, chacun refusé s'il existe déjà). La confirmation se fait côté page."""
    res = {"ranges": 0, "erreurs": []}
    for c in lister():
        if c["etat"] == "pret":
            r = ranger(c["id"])
            if r["ok"]:
                res["ranges"] += 1
            else:
                res["erreurs"].append(f"{c['titre']} : {r['error']}")
    return res
