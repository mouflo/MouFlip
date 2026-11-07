"""File de travaux de MouFlip : un clip à la fois, en deux temps.
1. PRÉPARER (automatique) : télécharger (meilleure qualité) → volume ramené à la cible → poster → fichiers .nfo prêts. Tout dans data/travail/<id>/ ;
   rien n'est écrit dans « Video clip ».
2. RANGER (sur confirmation, clip par clip) : copie dans « Video clip/<Groupe>/<Album>/ », puis Emby est prévenu. Rien d'existant n'est remplacé.
L'état est gardé dans data/travail/etat.json : un redémarrage ne perd pas les clips déjà prêts (un clip interrompu est signalé)."""
import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

from src import audio, formats, nfo, noms, rangement, telechargement

logger = logging.getLogger(__name__)

_VERROU = threading.RLock()
TRAVAUX: dict[str, dict] = {}
_FILE: "queue.Queue[str]" = queue.Queue()
_CFG = {"dossier": None, "cible_db": audio.REFERENCE_DB, "debit": "192k", "racine": None, "emby": None}
ETATS_ACTIFS = ("attente", "telechargement", "audio")


def configurer(dossier_donnees, racine_fn, cible_fn, debit_fn, emby, sans_perte_fn=lambda: True):
    """racine_fn() = dossier « Video clip » ; cible_fn() = niveau en dB ; debit_fn() = débit AAC ; emby = client Emby."""
    _CFG.update(dossier=Path(dossier_donnees) / "travail", racine=racine_fn, cible=cible_fn, debit=debit_fn, emby=emby, sans_perte=sans_perte_fn)
    _CFG["dossier"].mkdir(parents=True, exist_ok=True)
    _charger()


def _fichier_etat() -> Path:
    return _CFG["dossier"] / "etat.json"


def _sauver():
    tmp = _fichier_etat().with_suffix(".tmp")
    tmp.write_text(json.dumps(TRAVAUX, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _fichier_etat())


def _charger():
    with _VERROU:
        TRAVAUX.clear()
        try:
            TRAVAUX.update(json.loads(_fichier_etat().read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        for c in TRAVAUX.values():
            if c["etat"] in ETATS_ACTIFS:                            # l'appli s'est arrêtée pendant ce clip
                c["etat"], c["message"] = "erreur", "Interrompu par un redémarrage : relance-le."
        _sauver()


def public(c: dict) -> dict:
    """Ce que la page voit (sans les textes des .nfo)."""
    return {k: v for k, v in c.items() if k not in ("contexte",)}


def lister() -> list[dict]:
    with _VERROU:
        return [public(c) for c in sorted(TRAVAUX.values(), key=lambda c: c["cree"])]


def ajouter(groupe: dict, clip: dict) -> dict:
    """groupe = contexte du groupe (nom, mbid, dossier existant, fiche, images…) ; clip = {titre, album, annee, mbid, video_id}."""
    with _VERROU:
        for c in TRAVAUX.values():                                   # le même clip déjà en cours ou prêt n'est pas refait
            if c["groupe"] == groupe["nom"] and c["titre"] == clip["titre"] and c["etat"] in ETATS_ACTIFS + ("pret",):
                return public(c)
        tid = uuid.uuid4().hex[:10]
        c = {"id": tid, "cree": time.time(), "groupe": groupe["nom"], "titre": clip["titre"], "album": clip["album"], "annee": clip.get("annee", ""),
             "mbid_clip": clip.get("mbid", ""), "rg": clip.get("rg", ""), "video_id": clip.get("video_id", ""), "etat": "attente", "progres": 0, "message": "En attente…",
             "contexte": groupe}
        TRAVAUX[tid] = c
        _sauver()
    _FILE.put(tid)
    return public(c)


def _maj(c, **kw):
    with _VERROU:
        c.update(kw)
        _sauver()


def _progression(c):
    def hook(d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if tot:
                c["progres"] = int(100 * (d.get("downloaded_bytes") or 0) / tot)
                c["message"] = f"Téléchargement… {c['progres']} %"
    return hook


def _choisir_video(c) -> str:
    if c.get("video_id"):
        return c["video_id"]
    cands = formats.candidats(c["groupe"], c["titre"])
    sures = [x for x in cands if x.get("officielle")]
    if not sures:
        raise telechargement.TelechargementError("pas de vidéo officielle sûre : regarde-les avec « 👁️ Voir le clip » et choisis la bonne")
    return sures[0]["id"]


def construire_plan(c) -> list[dict]:
    ctx = c["contexte"]
    racine = _CFG["racine"]()
    sonde = c.get("sonde") or {}
    fiche = ctx.get("fiche", {})
    nclip = nfo.nfo_clip(c["titre"], c["annee"], c["groupe"], c["album"], sonde, c.get("mbid_clip", ""), c.get("video_id", ""))
    dossier_groupe = ctx.get("dossier_groupe") or ""
    chemin_groupe = dossier_groupe or str(Path(racine) / noms.propre(c["groupe"]))
    nart = nfo.nfo_artiste(c["groupe"], fiche.get("mbid", ""), (ctx.get("genre") or "K-Pop"), fiche.get("pays", ""), fiche.get("debut", ""),
                           ctx.get("biographie", ""), (ctx.get("images") or {}).get("vignette", ""), chemin_groupe)
    return rangement.plan(racine, dossier_groupe, c["groupe"], c["album"], c["titre"], c["fichier"], c.get("poster"), nclip, nart, ctx.get("images"),
                          (ctx.get("images_album") or {}).get(c.get("rg") or ""))


def _preparer(c):
    dossier = _CFG["dossier"] / c["id"]
    try:
        _maj(c, etat="telechargement", progres=0, message="Recherche de la vidéo officielle…")
        c["video_id"] = _choisir_video(c)
        _maj(c, message="Téléchargement…")
        t = telechargement.telecharger(c["video_id"], dossier, _progression(c))
        _maj(c, etat="audio", progres=100, message="Mesure et réglage du volume…")
        source = Path(t["fichier"])
        fini = dossier / "clip.mkv"
        a = audio.normaliser(source, fini, float(_CFG["cible"]()), _CFG["debit"](), bool(_CFG["sans_perte"]()))
        poster = telechargement.vignette_en_jpg(t["vignette"], str(fini), dossier / "poster.jpg")
        sonde = telechargement.sonder(fini)
        try:
            source.unlink()
        except OSError:
            pass
        _maj(c, fichier=str(fini), poster=poster, sonde=sonde, audio=a, titre_youtube=t["titre"], chaine=t["chaine"])
        c["plan"] = [{k: v for k, v in l.items() if k not in ("contenu",)} for l in construire_plan(c)]
        v = sonde["video"]
        msg = f"Prêt à ranger : {v['hauteur']}p {v['codec']}, volume {a['avant']} → {a['apres']} dB ({a.get('methode', '')})" + (", limiteur actif" if a["limiteur"] else "") + "."
        _maj(c, etat="pret", progres=100, message=msg)
        logger.info("[Travaux] %s - %s : %s", c["groupe"], c["titre"], msg)
    except (telechargement.TelechargementError, audio.AudioError) as e:
        logger.warning("[Travaux] %s - %s : %s", c["groupe"], c["titre"], e)
        _maj(c, etat="erreur", message=f"Impossible : {e}")
    except Exception as e:
        logger.exception("[Travaux] erreur inattendue")
        _maj(c, etat="erreur", message=f"Erreur inattendue : {type(e).__name__}: {e}")


def _boucle():
    while True:
        tid = _FILE.get()
        with _VERROU:
            c = TRAVAUX.get(tid)
        if c and c["etat"] == "attente":
            _preparer(c)


def demarrer():
    threading.Thread(target=_boucle, daemon=True, name="travaux").start()


def occupe() -> list[str]:
    with _VERROU:
        return [f"{c['groupe']} - {c['titre']}" for c in TRAVAUX.values() if c["etat"] in ETATS_ACTIFS]


def relancer(tid) -> dict:
    with _VERROU:
        c = TRAVAUX.get(tid)
        if not c or c["etat"] != "erreur":
            return {"ok": False, "error": "Rien à relancer."}
        c.update(etat="attente", progres=0, message="En attente…")
        _sauver()
    _FILE.put(tid)
    return {"ok": True}


def ranger(tid) -> dict:
    """Écrit dans « Video clip » (seulement ce que le plan annonce), prévient Emby, nettoie le dossier de travail."""
    with _VERROU:
        c = TRAVAUX.get(tid)
    if not c or c["etat"] != "pret":
        return {"ok": False, "error": "Ce clip n'est pas prêt à ranger."}
    racine = Path(_CFG["racine"]())
    if not racine.is_dir():
        return {"ok": False, "error": f"Le dossier des clips « {racine} » est introuvable (le partage est-il monté ?). Rien n'a été écrit."}
    if not Path(c["fichier"]).exists():
        return {"ok": False, "error": "Le fichier préparé a disparu : relance le clip."}
    res = rangement.executer(construire_plan(c))
    if res["erreurs"]:
        _maj(c, message="Rangement incomplet : " + " ; ".join(res["erreurs"]), resultat=res)
        return {"ok": False, "error": "Rangement incomplet : " + " ; ".join(res["erreurs"]), "resultat": res}
    plan = construire_plan(c)
    dossier_groupe = str(Path(next(l["chemin"] for l in plan if l["type"] == "video")).parent.parent)
    emby = rangement.prevenir_emby(_CFG["emby"], dossier_groupe)
    shutil.rmtree(_CFG["dossier"] / c["id"], ignore_errors=True)
    _maj(c, etat="range", message="Rangé. " + emby, resultat=res, fichier="", poster="")
    logger.info("[Travaux] rangé : %s - %s (%s)", c["groupe"], c["titre"], emby)
    return {"ok": True, "resultat": res, "emby": emby}


def supprimer(tid) -> dict:
    with _VERROU:
        c = TRAVAUX.get(tid)
        if not c or c["etat"] in ETATS_ACTIFS:
            return {"ok": False, "error": "Un clip en cours de traitement ne peut pas être retiré."}
        del TRAVAUX[tid]
        _sauver()
    shutil.rmtree(_CFG["dossier"] / tid, ignore_errors=True)
    return {"ok": True}
