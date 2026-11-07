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
SLOTS_FANART = ("fanart", "fanart2", "fanart3", "fanart4")          # fanart.jpg puis extrafanart/fanart1..3.jpg (voir rangement.IMAGES_GROUPE)
TYPES_IMAGES = ("vignette", "banniere", "logo")
NOMS_FICHIERS = dict(rangement.IMAGES_GROUPE)


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
    return {k: v for k, v in c.items() if k not in ("contexte", "choix", "posters")}


def lister() -> list[dict]:
    with _VERROU:
        return [public(c) for c in sorted(TRAVAUX.values(), key=lambda c: c["cree"])]


def _fichier_choix() -> Path:
    return _CFG["dossier"].parent / "choix_images.json"


def _choix_groupe(mbid) -> dict:
    try:
        return json.loads(_fichier_choix().read_text(encoding="utf-8")).get(mbid, {})
    except (OSError, ValueError):
        return {}


def _choix_par_defaut(ctx) -> dict:
    """Les choix d'images : ceux déjà faits pour ce groupe (réutilisés pour ses autres clips), sinon « le premier de chaque type » (None)."""
    memo = _choix_groupe(ctx.get("mbid", ""))
    return {"images": {**{t: None for t in TYPES_IMAGES}, "fanart": None, **(memo.get("images") or {})},
            "album": {"couverture": None, "cdart": None, **(memo.get("album") or {})}, "biographie": None, "poster": None,
            "artiste": memo.get("artiste")}


def ajouter(groupe: dict, clip: dict) -> dict:
    """groupe = contexte du groupe (nom, mbid, dossier existant, fiche, images…) ; clip = {titre, album, annee, mbid, video_id}."""
    with _VERROU:
        for c in TRAVAUX.values():                                   # le même clip déjà en cours ou prêt n'est pas refait
            if c["groupe"] == groupe["nom"] and c["titre"] == clip["titre"] and c["etat"] in ETATS_ACTIFS + ("pret",):
                return public(c)
        tid = uuid.uuid4().hex[:10]
        c = {"id": tid, "cree": time.time(), "groupe": groupe["nom"], "titre": clip["titre"], "album": clip["album"], "annee": clip.get("annee", ""),
             "mbid_clip": clip.get("mbid", ""), "rg": clip.get("rg", ""), "video_id": clip.get("video_id", ""), "format_id": clip.get("format_id", ""),
             "qualite": clip.get("qualite", ""), "etat": "attente", "progres": 0, "message": "En attente…",
             "choix": _choix_par_defaut(groupe), "contexte": groupe}
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


def _chemin_poster(c) -> str:
    """Le poster choisi parmi ceux proposés (par défaut : la vignette YouTube, sinon la première image)."""
    posters = c.get("posters") or []
    voulu = (c.get("choix") or {}).get("poster")
    for p in posters:
        if p["id"] == voulu:
            return p["fichier"]
    return posters[0]["fichier"] if posters else (c.get("poster") or "")


def _dossiers(c):
    ctx = c["contexte"]
    racine = Path(_CFG["racine"]())
    groupe = Path(ctx.get("dossier_groupe") or str(racine / noms.propre(c["groupe"])))
    return racine, groupe, groupe / noms.propre(c["album"])


def _images_effectives(c):
    """(images du groupe, images de l'album, clés à remplacer) d'après les choix. None = « le premier de chaque type » ; "" = aucune ; une adresse =
    celle-là. Une image DÉJÀ présente n'est remplacée que si l'utilisatrice a choisi une autre image (jamais par défaut)."""
    ctx, choix = c["contexte"], c.get("choix") or {}
    _racine, groupe, dossier_album = _dossiers(c)
    cands = ctx.get("candidats") or {}
    images, remplacer = {}, set()
    for t in TYPES_IMAGES:
        voulu = (choix.get("images") or {}).get(t)
        premiere = ((cands.get(t) or [{}])[0].get("url")) or (ctx.get("images") or {}).get(t)
        url = premiere if voulu is None else voulu
        if url:
            images[t] = url
            if voulu and (groupe / NOMS_FICHIERS[t]).exists():
                remplacer.add(t)
    fonds = (choix.get("images") or {}).get("fanart")
    urls = [x["url"] for x in (cands.get("fanart") or [])[:4]] if fonds is None else list(fonds)
    if not urls and fonds is None:
        urls = [u for k, u in (ctx.get("images") or {}).items() if k.startswith("fanart")]
    libres = [cle for cle in SLOTS_FANART if not (groupe / NOMS_FICHIERS[cle]).exists()]            # les fonds déjà là ne sont jamais remplacés
    for cle, u in zip(libres, urls):
        images[cle] = u
    album = (ctx.get("images_album") or {}).get(c.get("rg") or "") or {}
    images_album = {}
    for cle, fichier, nom_remp in (("couverture", "folder.jpg", "album_couverture"), ("cdart", "cdart.png", "album_cdart")):
        voulu = (choix.get("album") or {}).get(cle)
        url = album.get(cle) if voulu is None else voulu
        if url:
            images_album[cle] = url
            if voulu and (dossier_album / fichier).exists():
                remplacer.add(nom_remp)
    return images, images_album, remplacer


def _nfo_artiste(c) -> str:
    ctx = c["contexte"]
    fiche = ctx.get("fiche", {})
    _racine, groupe, _a = _dossiers(c)
    bio = (c.get("choix") or {}).get("biographie")
    images = _images_effectives(c)[0]
    return nfo.nfo_artiste(nom_artiste(c), fiche.get("mbid", ""), (ctx.get("genre") or "K-Pop"), fiche.get("pays", ""), fiche.get("debut", ""),
                           ctx.get("biographie", "") if bio is None else bio, images.get("vignette") or (ctx.get("images") or {}).get("vignette", ""), str(groupe))


def nom_artiste(c) -> str:
    """Le nom écrit dans les .nfo : celui choisi dans la fiche (écriture d'Emby), sinon celui de MusicBrainz."""
    return ((c.get("choix") or {}).get("artiste") or c["groupe"]).strip()


def _nfo_clip(c) -> str:
    return nfo.nfo_clip(c["titre"], c["annee"], nom_artiste(c), c["album"], c.get("sonde") or {}, c.get("mbid_clip", ""), c.get("video_id", ""))


def construire_plan(c) -> list[dict]:
    racine, _g, _a = _dossiers(c)
    images, images_album, remplacer = _images_effectives(c)
    return rangement.plan(racine, c["contexte"].get("dossier_groupe") or "", c["groupe"], c["album"], c["titre"], c["fichier"], _chemin_poster(c),
                          _nfo_clip(c), _nfo_artiste(c), images, images_album, remplacer)


def _preparer(c):
    dossier = _CFG["dossier"] / c["id"]
    try:
        _maj(c, etat="telechargement", progres=0, message="Recherche de la vidéo officielle…")
        c["video_id"] = _choisir_video(c)
        _maj(c, message="Téléchargement…")
        t = telechargement.telecharger(c["video_id"], dossier, _progression(c), format_id=c.get("format_id", ""))
        _maj(c, etat="audio", progres=100, message="Mesure et réglage du volume…")
        source = Path(t["fichier"])
        fini = dossier / "clip.mkv"
        a = audio.normaliser(source, fini, float(_CFG["cible"]()), _CFG["debit"](), bool(_CFG["sans_perte"]()))
        poster = telechargement.vignette_en_jpg(t["vignette"], str(fini), dossier / "poster.jpg")
        posters = ([{"id": "yt", "label": "Vignette YouTube", "fichier": poster}] if poster else []) + \
                  [{"id": f"f{x['pourcent']}", "label": f"Image à {x['pourcent']} %", "fichier": x["fichier"]} for x in telechargement.images_extraites(str(fini), dossier)]
        sonde = telechargement.sonder(fini)
        try:
            source.unlink()
        except OSError:
            pass
        _maj(c, fichier=str(fini), poster=poster, posters=posters, sonde=sonde, audio=a, titre_youtube=t["titre"], chaine=t["chaine"])
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
    res = rangement.executer(construire_plan(c), _CFG["dossier"].parent / "sauvegardes")
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


# ----------------------------------------------------------------------------------------------------------------------------------------
# La fiche : tout ce qui sera récupéré et visible dans Emby, avec les choix d'images
# ----------------------------------------------------------------------------------------------------------------------------------------
def _existant(chemin: Path) -> dict:
    return {"present": chemin.exists(), "chemin": str(chemin)} if chemin.exists() else {"present": False}


emby_artiste_fn = None          # app.py y branche la recherche d'artiste dans Emby (nom -> état)


def fiche(tid) -> dict | None:
    with _VERROU:
        c = TRAVAUX.get(tid)
    if not c or c["etat"] != "pret":
        return None
    ctx, choix = c["contexte"], c.get("choix") or {}
    racine, groupe, dossier_album = _dossiers(c)
    cands = ctx.get("candidats") or {}
    images, images_album, remplacer = _images_effectives(c)
    plan = construire_plan(c)
    album = (ctx.get("images_album") or {}).get(c.get("rg") or "") or {}

    def bloc(type_, nom_fichier, dossier, candidats, choisi, actuelle_remplacee):
        return {"type": type_, "fichier": nom_fichier, "existant": _existant(Path(dossier) / nom_fichier), "candidats": candidats, "choisi": choisi,
                "remplace": actuelle_remplacee}

    groupe_images = {}
    for t in TYPES_IMAGES:
        groupe_images[t] = bloc(t, NOMS_FICHIERS[t], groupe, cands.get(t) or [], (choix.get("images") or {}).get(t), t in remplacer)
    fonds_existants = [{"fichier": NOMS_FICHIERS[cle], **_existant(groupe / NOMS_FICHIERS[cle])} for cle in SLOTS_FANART]
    albums = {"couverture": bloc("couverture", "folder.jpg", dossier_album, [{"url": u, "source": "fanart.tv"} for u in album.get("couverture_liste", [])],
                                 (choix.get("album") or {}).get("couverture"), "album_couverture" in remplacer),
              "cdart": bloc("cdart", "cdart.png", dossier_album, [{"url": u, "source": "fanart.tv"} for u in album.get("cdart_liste", [])],
                            (choix.get("album") or {}).get("cdart"), "album_cdart" in remplacer)}
    s = c.get("sonde") or {}
    v, a = s.get("video") or {}, s.get("audio") or {}
    bio = ctx.get("biographie", "") if choix.get("biographie") is None else choix["biographie"]
    nom_video = next((l["chemin"] for l in plan if l["type"] == "video"), "")
    return {
        "id": c["id"], "etat": c["etat"],
        "clip": {"titre": c["titre"], "album": c["album"], "annee": c["annee"], "groupe": c["groupe"], "chaine": c.get("chaine", ""), "titre_youtube": c.get("titre_youtube", ""),
                 "video_id": c.get("video_id", ""), "duree": s.get("duree", 0), "taille": s.get("taille", 0),
                 "video": {"resolution": f"{v.get('hauteur')}p" if v.get("hauteur") else "", "largeur": v.get("largeur"), "hauteur": v.get("hauteur"),
                           "codec": v.get("codec"), "debit": v.get("debit"), "fps": v.get("fps")},
                 "audio": {"codec": a.get("codec"), "debit": a.get("debit"), "canaux": a.get("canaux")},
                 "volume": c.get("audio") or {}, "destination": nom_video, "nfo": _nfo_clip(c),
                 "posters": [{"id": p["id"], "label": p["label"]} for p in (c.get("posters") or [])],
                 "poster_choisi": choix.get("poster") or ((c.get("posters") or [{}])[0].get("id") if c.get("posters") else None),
                 "dossier_album_existe": dossier_album.exists()},
        "groupe": {"nom": c["groupe"], "pays": (ctx.get("fiche") or {}).get("pays", ""), "debut": (ctx.get("fiche") or {}).get("debut", ""),
                   "genre": ctx.get("genre", ""), "biographie": bio, "biographie_defaut": ctx.get("biographie", ""),
                   "biographie_langue": ctx.get("biographie_langue", ""), "dossier": str(groupe), "dossier_existe": groupe.exists(),
                   "artist_nfo_existe": (groupe / "artist.nfo").exists(), "images": groupe_images, "fanart": {
                       "candidats": cands.get("fanart") or [], "choisis": choix.get("images", {}).get("fanart"), "existants": fonds_existants,
                       "libres": sum(1 for f in fonds_existants if not f["present"])}},
        "album": albums,
        "emby_artiste": emby_artiste_fn(nom_artiste(c)) if emby_artiste_fn else None, "artiste": nom_artiste(c),
        "plan": [{k: v for k, v in l.items() if k not in ("contenu",)} for l in plan],
    }


def _urls_permises(c) -> set:
    ctx = c["contexte"]
    ok = {x["url"] for lst in (ctx.get("candidats") or {}).values() for x in lst}
    for a in (ctx.get("images_album") or {}).values():
        ok |= set(a.get("couverture_liste", [])) | set(a.get("cdart_liste", []))
    return ok


def enregistrer_choix(tid, donnees: dict):
    """Valide et retient les choix de la fiche. Seules des images PROPOSÉES sont acceptables (jamais une adresse libre). Retourne (fiche, erreur)."""
    with _VERROU:
        c = TRAVAUX.get(tid)
    if not c or c["etat"] != "pret":
        return None, "Ce clip n'est pas prêt."
    permises = _urls_permises(c)
    choix = c.setdefault("choix", _choix_par_defaut(c["contexte"]))
    images = donnees.get("images") or {}
    for t in TYPES_IMAGES:
        if t in images:
            v = images[t]
            if v is not None and v != "" and v not in permises:
                return None, f"Image non proposée pour « {t} »."
            choix["images"][t] = v
    if "fanart" in images:
        v = images["fanart"]
        if v is not None:
            if not isinstance(v, list) or len(v) > 4 or any(u not in permises for u in v):
                return None, "Fonds d'écran non valides (4 au plus, parmi ceux proposés)."
        choix["images"]["fanart"] = v
    album = donnees.get("album") or {}
    for cle in ("couverture", "cdart"):
        if cle in album:
            v = album[cle]
            if v is not None and v != "" and v not in permises:
                return None, f"Image d'album non proposée ({cle})."
            choix["album"][cle] = v
    if "biographie" in donnees:
        bio = donnees["biographie"]
        if bio is not None and (not isinstance(bio, str) or len(bio) > 20000):
            return None, "Texte de biographie trop long (20 000 caractères au plus)."
        choix["biographie"] = bio
    if "artiste" in donnees:
        nom = donnees["artiste"]
        if nom is not None and (not isinstance(nom, str) or not nom.strip() or len(nom) > 120 or "\n" in nom):
            return None, "Nom d'artiste invalide."
        choix["artiste"] = nom.strip() if nom else None
    if "poster" in donnees:
        if donnees["poster"] is not None and donnees["poster"] not in {p["id"] for p in c.get("posters") or []}:
            return None, "Poster inconnu."
        choix["poster"] = donnees["poster"]
    c["poster"] = _chemin_poster(c)
    memo = {}
    try:
        memo = json.loads(_fichier_choix().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    memo[c["contexte"].get("mbid", "")] = {"images": choix["images"], "album": choix["album"], "artiste": choix.get("artiste")}        # réutilisés pour les autres clips du groupe
    tmp = _fichier_choix().with_suffix(".tmp")
    tmp.write_text(json.dumps(memo, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _fichier_choix())
    c["plan"] = [{k: v for k, v in l.items() if k not in ("contenu",)} for l in construire_plan(c)]
    _maj(c)
    return fiche(tid), ""


def fichier_poster(tid, pid) -> str:
    with _VERROU:
        c = TRAVAUX.get(tid)
    for p in (c or {}).get("posters") or []:
        if p["id"] == pid and Path(p["fichier"]).is_file():
            return p["fichier"]
    return ""
