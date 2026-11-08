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

from src import audio, convention, formats, images_perso, nfo, noms, rangement, telechargement

logger = logging.getLogger(__name__)

_VERROU = threading.RLock()
TRAVAUX: dict[str, dict] = {}
_FILE: "queue.Queue[str]" = queue.Queue()
_CFG = {"dossier": None, "cible_db": audio.REFERENCE_DB, "debit": "192k", "racine": None, "emby": None, "ecriture": True}
ETATS_ACTIFS = ("attente", "telechargement", "reduction", "audio")
SLOTS_FANART = ("fanart", "fanart2", "fanart3", "fanart4")          # fanart.jpg puis extrafanart/fanart1..3.jpg (voir rangement.IMAGES_GROUPE)
TYPES_IMAGES = ("vignette", "banniere", "logo")
NOMS_FICHIERS = dict(rangement.IMAGES_GROUPE)


def configurer(dossier_donnees, racine_fn, cible_fn, debit_fn, emby, sans_perte_fn=lambda: True, reduction_fn=lambda: {"actif": False, "crf": 30}, ecriture=True):
    """racine_fn() = dossier « Video clip » ; cible_fn() = niveau en dB ; debit_fn() = débit AAC ; emby = client Emby."""
    # ecriture=False : l'état est seulement LU (c'est le cas quand l'appli est simplement importée, par exemple par les tests) ; le vrai service ne l'écrit
    # qu'après demarrer(). Avant, un simple import réécrivait le fichier d'état du service en marquant ses clips en cours comme « interrompus ».
    _CFG.update(ecriture=ecriture, dossier=Path(dossier_donnees) / "travail", racine=racine_fn, cible=cible_fn, debit=debit_fn, emby=emby, sans_perte=sans_perte_fn, reduction=reduction_fn)
    _CFG["dossier"].mkdir(parents=True, exist_ok=True)
    _charger()


def _fichier_etat() -> Path:
    return _CFG["dossier"] / "etat.json"


def _sauver():
    if not _CFG.get("ecriture", True):
        return
    tmp = _fichier_etat().with_suffix(".tmp")
    tmp.write_text(json.dumps(TRAVAUX, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _fichier_etat())


MAX_REPRISES = 2


def _charger(reprise: bool = False):
    """Lit l'état. reprise=True (au démarrage du vrai service seulement) : un clip qui travaillait quand l'appli s'est arrêtée REPREND tout seul
    (jusqu'à 2 fois, puis il est signalé en erreur : un clip qui fait tomber l'appli ne tourne pas en boucle)."""
    with _VERROU:
        TRAVAUX.clear()
        try:
            TRAVAUX.update(json.loads(_fichier_etat().read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        if not reprise:
            return
        for c in TRAVAUX.values():
            if c["etat"] in ETATS_ACTIFS:                            # l'appli s'est arrêtée pendant ce clip
                c["essais"] = c.get("essais", 0) + 1
                if c["essais"] <= MAX_REPRISES:
                    c.update(etat="attente", progres=0, message="Reprise après un redémarrage de l'appli…")
                else:
                    c.update(etat="erreur", message="Interrompu plusieurs fois par un redémarrage : relance-le.")
        _sauver()


def public(c: dict) -> dict:
    """Ce que la page voit (sans les textes des .nfo)."""
    return {k: v for k, v in c.items() if k not in ("contexte", "choix", "posters")}


def lister() -> list[dict]:
    with _VERROU:
        return [public(c) for c in sorted(TRAVAUX.values(), key=lambda c: c["cree"])]


def _fichier_choix() -> Path:
    return _CFG["dossier"].parent / "choix_images.json"


def reduction_voulue(c) -> tuple[bool, int]:
    """(réduire ?, niveau crf) pour CE clip : « non » = jamais ; un niveau choisi pour le clip = oui à ce niveau, même si le réglage général est coupé ;
    « defaut » (ou rien) = le réglage général de ⚙️ Réglages."""
    mode = c.get("reduction_mode") or "defaut"
    if mode == "non":
        return False, 0
    if mode in telechargement.CRF_REDUCTION:
        return True, telechargement.CRF_REDUCTION[mode]
    red = _CFG["reduction"]()
    return bool(red.get("actif")), int(red.get("crf", 30))


def _choix_groupe(mbid) -> dict:
    try:
        return json.loads(_fichier_choix().read_text(encoding="utf-8")).get(mbid, {})
    except (OSError, ValueError):
        return {}


def _choix_par_defaut(ctx) -> dict:
    """Les choix d'images : ceux déjà faits pour ce groupe (réutilisés pour ses autres clips), sinon « le premier de chaque type » (None)."""
    memo = _choix_groupe(ctx.get("mbid", ""))
    return {"images": {**{t: None for t in TYPES_IMAGES}, "fanart": None, **(memo.get("images") or {})},
            "album": {"couverture": None, "cdart": None}, "biographie": None, "poster": None, "vignette_clip": None,
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
             "qualite": clip.get("qualite", ""), "reduction_mode": clip.get("reduction_mode", "defaut"), "etat": "attente", "progres": 0, "message": "En attente…",
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
    cands = formats.candidats(c["groupe"], c["titre"], chaines=(chaines_fn(c["contexte"]) if chaines_fn else ()))
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


def _rg(c) -> str:
    """Le groupe de sorties (album) du clip, celui qui a des images chez fanart.tv de préférence. Un clip lancé sans identifiant d'album, ou dont l'album
    n'a pas de pochette, est retrouvé d'après la liste enregistrée du groupe (même titre, puis même album) : l'appli répare seule."""
    ctx = c["contexte"]
    images = ctx.get("images_album") or {}
    if rg_fn and (not c.get("rg") or not images.get(c["rg"])):
        try:
            r = rg_fn(ctx, c["titre"], c["album"])
        except Exception:
            r = ""
        if r and r != c.get("rg"):
            c["rg"] = r
            if c.get("choix"):
                c["choix"]["album"] = {"couverture": None, "cdart": None}          # les choix faits avant visaient un autre album (ou aucun) : on repart des propositions
    return c.get("rg") or ""


def _perso(c, type_) -> list[dict]:
    """Les images ajoutées à la main pour ce type : [{url: « perso:<id> », source: « toi », apercu}]. Pochette et cdart : du même album seulement."""
    mbid = c["contexte"].get("mbid", "")
    return [{"url": f"perso:{e['id']}", "source": "toi", "apercu": f"/api/perso/{mbid}/{e['id']}"}
            for e in images_perso.lister(mbid, type_, noms.normaliser(c["album"]) if type_ in ("couverture", "cdart") else "")]


def _resoudre_url(c, url):
    """« perso:<id> » devient le fichier local (« file://… ») ; une adresse d'Internet reste telle quelle."""
    if isinstance(url, str) and url.startswith("perso:"):
        p = images_perso.chemin(c["contexte"].get("mbid", ""), url[len("perso:"):])
        return f"file://{p}" if p else ""
    return url


def _candidats(c, type_) -> list[dict]:
    """Toutes les images proposées pour un type : les bases d'images, puis celles ajoutées à la main (en dernier : jamais choisies par défaut)."""
    ctx = c["contexte"]
    if type_ in ("vignette", "banniere", "logo", "fanart"):
        base = list((ctx.get("candidats") or {}).get(type_) or [])
    elif type_ == "couverture":
        album = (ctx.get("images_album") or {}).get(_rg(c)) or {}
        base = [{"url": u, "source": "fanart.tv"} for u in album.get("couverture_liste", [])]
        if couvertures_fn:
            try:
                for x in couvertures_fn(_rg(c), c["groupe"], c["album"]):
                    if x["url"] not in [b["url"] for b in base]:
                        base.append(x)
            except Exception:
                pass
    else:   # cdart
        album = (ctx.get("images_album") or {}).get(_rg(c)) or {}
        base = [{"url": u, "source": "fanart.tv"} for u in album.get("cdart_liste", [])]
    return base + _perso(c, type_)


def _dossiers(c):
    ctx = c["contexte"]
    racine = Path(_CFG["racine"]())
    groupe = Path(ctx.get("dossier_groupe") or str(racine / noms.propre(c["groupe"])))
    return racine, groupe, groupe / noms.propre(c["album"])


def _images_effectives(c):
    """(images du groupe, images de l'album, clés à remplacer) d'après les choix. None = « le premier de chaque type » ; "" = aucune ; une adresse (ou
    « perso:<id> » : une image ajoutée à la main) = celle-là. Une image DÉJÀ présente n'est remplacée que si l'utilisatrice a choisi une autre image."""
    ctx, choix = c["contexte"], c.get("choix") or {}
    _racine, groupe, dossier_album = _dossiers(c)
    images, remplacer = {}, set()
    for t in TYPES_IMAGES:
        voulu = (choix.get("images") or {}).get(t)
        cands = _candidats(c, t)
        premiere = (cands[0]["url"] if cands else None) or (ctx.get("images") or {}).get(t)
        url = _resoudre_url(c, premiere if voulu is None else voulu)
        if url:
            images[t] = url
            if voulu and (groupe / NOMS_FICHIERS[t]).exists():
                remplacer.add(t)
    fonds = (choix.get("images") or {}).get("fanart")
    cands_f = _candidats(c, "fanart")
    urls = [x["url"] for x in cands_f[:4]] if fonds is None else list(fonds)
    if not urls and fonds is None:
        urls = [u for k, u in (ctx.get("images") or {}).items() if k.startswith("fanart")]
    libres = [cle for cle in SLOTS_FANART if not (groupe / NOMS_FICHIERS[cle]).exists()]            # les fonds déjà là ne sont jamais remplacés
    for cle, u in zip(libres, urls):
        r = _resoudre_url(c, u)
        if r:
            images[cle] = r
    images_album = {}
    for cle, fichier, nom_remp in (("couverture", "folder.jpg", "album_couverture"), ("cdart", "cdart.png", "album_cdart")):
        voulu = (choix.get("album") or {}).get(cle)
        cands = _candidats(c, cle)
        url = _resoudre_url(c, (cands[0]["url"] if cands else None) if voulu is None else voulu)
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


def _chemin_vignette_clip(c) -> str:
    """La vignette (Thumb d'Emby) : l'image du clip choisie dans la fiche ; par défaut la vignette YouTube (16:9), sinon la première image du clip."""
    posters = c.get("posters") or []
    voulu = (c.get("choix") or {}).get("vignette_clip")
    for p in posters:
        if p["id"] == voulu:
            return p["fichier"]
    return posters[0]["fichier"] if posters else ""


def poster_choisi_id(c, images_album=None) -> str | None:
    """Quel poster pour le clip : celui choisi dans la fiche ; par défaut la POCHETTE de l'album (comme les autres clips de la médiathèque) quand on en a une,
    sinon la vignette du clip."""
    voulu = (c.get("choix") or {}).get("poster")
    if voulu:
        return voulu
    if images_album is None:
        images_album = _images_effectives(c)[1]
    return "pochette" if images_album.get("couverture") else ((c.get("posters") or [{}])[0].get("id"))


def construire_plan(c) -> list[dict]:
    racine, _g, _a = _dossiers(c)
    images, images_album, remplacer = _images_effectives(c)
    voulu = poster_choisi_id(c, images_album)
    poster = images_album["couverture"] if voulu == "pochette" and images_album.get("couverture") else _chemin_poster(c)
    return rangement.plan(racine, c["contexte"].get("dossier_groupe") or "", c["groupe"], c["album"], c["titre"], c["fichier"], poster,
                          _nfo_clip(c), _nfo_artiste(c), images, images_album, remplacer, thumb=_chemin_vignette_clip(c))


def _preparer(c):
    dossier = _CFG["dossier"] / c["id"]
    try:
        _maj(c, etat="telechargement", progres=0, message="Recherche de la vidéo officielle…")
        c["video_id"] = _choisir_video(c)
        _maj(c, message="Téléchargement…")
        t = telechargement.telecharger(c["video_id"], dossier, _progression(c), format_id=c.get("format_id", ""))
        source = Path(t["fichier"])
        reduction = {}
        veut, crf = reduction_voulue(c)
        if veut:
            _maj(c, etat="reduction", progres=0, message="Réduction de la taille du clip (environ 10 minutes)…")
            def avance(pct):
                c["progres"], c["message"] = pct, f"Réduction de la taille du clip… {pct} % (environ 10 minutes)"
            reduit = dossier / "reduit.mkv"
            reduction = telechargement.reduire(source, reduit, crf, avance)
            if reduction["fait"]:
                try:
                    source.unlink()
                except OSError:
                    pass
                source = reduit
        _maj(c, etat="audio", progres=100, message="Mesure et réglage du volume…")
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
        _maj(c, fichier=str(fini), poster=poster, posters=posters, sonde=sonde, reduction=reduction, audio=a, titre_youtube=t["titre"], chaine=t["chaine"])
        c["plan"] = [{k: v for k, v in l.items() if k not in ("contenu",)} for l in construire_plan(c)]
        v = sonde["video"]
        taille_txt = (f" (taille {reduction['avant'] / 1e6:.0f} → {reduction['apres'] / 1e6:.0f} Mo)" if reduction.get("fait") else
                      (f" (taille non réduite : {reduction['raison']})" if reduction else ""))
        msg = f"Prêt à ranger : {v['hauteur']}p {v['codec']}{taille_txt}, volume {a['avant']} → {a['apres']} dB ({a.get('methode', '')})" + (", limiteur actif" if a["limiteur"] else "") + "."
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
    """À appeler une seule fois, par le vrai service : les clips interrompus par un redémarrage reprennent, puis la file de travail démarre."""
    _CFG["ecriture"] = True
    _charger(reprise=True)
    with _VERROU:
        for tid, c in sorted(TRAVAUX.items(), key=lambda x: x[1]["cree"]):
            if c["etat"] == "attente":
                _FILE.put(tid)
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


apres_rangement_fn = None


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
    if apres_rangement_fn:
        try:
            apres_rangement_fn(c)                                    # ex. le son studio part tout seul dans la Musique (réglage de l'utilisatrice)
        except Exception as e:
            logger.warning("[Travaux] suite du rangement : %s", e)
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
chaines_fn = None               # app.py y branche « quelles chaînes YouTube sont officielles pour ce groupe ? » (contexte -> ensemble d'identifiants)
couvertures_fn = None           # app.py y branche « pochettes d'album trouvées ailleurs » (rg, groupe, album -> [{url, source}], mis en cache)
rg_fn = None                    # app.py y branche « quel groupe de sorties (album) pour ce clip ? » (contexte, titre, album -> id)


def _posters_fiche(c, images_album) -> list[dict]:
    """Les posters proposés : d'abord la POCHETTE de l'album (celle qui sera posée, comme dans le reste de la médiathèque), puis les images du clip."""
    sortie = []
    if images_album.get("couverture"):
        choisi = (c.get("choix") or {}).get("album", {}).get("couverture")
        cands = _candidats(c, "couverture")
        x = next((x for x in cands if x["url"] == choisi), None) if choisi else (cands[0] if cands else None)
        sortie.append({"id": "pochette", "label": "Pochette de l'album : comme tes autres clips", "apercu": (x or {}).get("apercu") or (x or {}).get("url") or images_album["couverture"]})
    return sortie + [{"id": p["id"], "label": p["label"]} for p in (c.get("posters") or [])]


def fiche(tid) -> dict | None:
    with _VERROU:
        c = TRAVAUX.get(tid)
    if not c or c["etat"] != "pret":
        return None
    ctx, choix = c["contexte"], c.get("choix") or {}
    racine, groupe, dossier_album = _dossiers(c)
    images, images_album, remplacer = _images_effectives(c)
    plan = construire_plan(c)

    def bloc(type_, nom_fichier, dossier, candidats, choisi, actuelle_remplacee):
        return {"type": type_, "fichier": nom_fichier, "existant": _existant(Path(dossier) / nom_fichier), "candidats": candidats, "choisi": choisi,
                "remplace": actuelle_remplacee}

    groupe_images = {}
    for t in TYPES_IMAGES:
        groupe_images[t] = bloc(t, NOMS_FICHIERS[t], groupe, _candidats(c, t), (choix.get("images") or {}).get(t), t in remplacer)
    fonds_existants = [{"fichier": NOMS_FICHIERS[cle], **_existant(groupe / NOMS_FICHIERS[cle])} for cle in SLOTS_FANART]
    albums = {"couverture": bloc("couverture", "folder.jpg", dossier_album, _candidats(c, "couverture"), (choix.get("album") or {}).get("couverture"), "album_couverture" in remplacer),
              "cdart": bloc("cdart", "cdart.png", dossier_album, _candidats(c, "cdart"), (choix.get("album") or {}).get("cdart"), "album_cdart" in remplacer)}
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
                 "volume": c.get("audio") or {}, "reduction": c.get("reduction") or {}, "destination": nom_video, "nfo": _nfo_clip(c),
                 "posters": _posters_fiche(c, images_album),
                 "poster_choisi": poster_choisi_id(c, images_album),
                 "vignettes": [{"id": p["id"], "label": p["label"]} for p in (c.get("posters") or [])],
                 "vignette_choisie": next((p["id"] for p in (c.get("posters") or []) if p["fichier"] == _chemin_vignette_clip(c)), None),
                 "dossier_album_existe": dossier_album.exists()},
        "groupe": {"nom": c["groupe"], "pays": (ctx.get("fiche") or {}).get("pays", ""), "debut": (ctx.get("fiche") or {}).get("debut", ""),
                   "genre": ctx.get("genre", ""), "biographie": bio, "biographie_defaut": ctx.get("biographie", ""),
                   "biographie_langue": ctx.get("biographie_langue", ""), "biographie_source": ctx.get("biographie_source", ""), "dossier": str(groupe), "dossier_existe": groupe.exists(),
                   "artist_nfo_existe": (groupe / "artist.nfo").exists(), "biographie_existante": nfo.lire_biographie(groupe / "artist.nfo") if (groupe / "artist.nfo").exists() else "", "images": groupe_images, "fanart": {
                       "candidats": _candidats(c, "fanart"), "choisis": choix.get("images", {}).get("fanart"), "existants": fonds_existants,
                       "libres": sum(1 for f in fonds_existants if not f["present"])}},
        "album": albums,
        "emby_artiste": emby_artiste_fn(nom_artiste(c)) if emby_artiste_fn else None, "artiste": nom_artiste(c),
        "plan": [{k: v for k, v in l.items() if k not in ("contenu",)} for l in plan],
    }


def _urls_permises(c) -> set:
    """Toutes les images PROPOSÉES (jamais une adresse libre) : bases d'images, autres banques de pochettes, images ajoutées à la main."""
    ok = set()
    for t in ("vignette", "banniere", "logo", "fanart", "couverture", "cdart"):
        ok |= {x["url"] for x in _candidats(c, t)}
    return ok


def _memoriser_choix(c):
    """Les choix d'images du groupe (pas l'album : sa pochette est propre à chaque album) sont réutilisés pour ses autres clips."""
    memo = {}
    try:
        memo = json.loads(_fichier_choix().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    choix = c.get("choix") or {}
    memo[c["contexte"].get("mbid", "")] = {"images": choix.get("images", {}), "artiste": choix.get("artiste")}
    tmp = _fichier_choix().with_suffix(".tmp")
    tmp.write_text(json.dumps(memo, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _fichier_choix())


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
    if "edition" in donnees:                                                        # titre, album, année corrigés dans la fiche avant le rangement
        ed = donnees["edition"] or {}
        titre = str(ed.get("titre", c["titre"])).strip()
        album = str(ed.get("album", c["album"])).strip()
        annee = str(ed.get("annee", c["annee"])).strip()
        if not titre or not album or len(titre) > 160 or len(album) > 160 or any(x in titre + album for x in "\n\r\t"):
            return None, "Le titre et l'album doivent être renseignés (160 caractères au plus, sur une ligne)."
        if annee and not (len(annee) == 4 and annee.isdigit()):
            return None, "L'année s'écrit avec 4 chiffres (ou reste vide)."
        if album != c["album"]:                                                      # un autre album : sa pochette, son dossier, son groupe de sorties changent
            c["album"], c["rg"] = album, ""
            choix["album"] = {"couverture": None, "cdart": None}
            if rg_fn:
                try:
                    c["rg"] = rg_fn(c["contexte"], titre, album) or ""
                except Exception:
                    c["rg"] = ""
        c["titre"], c["annee"] = titre, annee
    if "vignette_clip" in donnees:
        if donnees["vignette_clip"] is not None and donnees["vignette_clip"] not in {p["id"] for p in c.get("posters") or []}:
            return None, "Vignette inconnue."
        choix["vignette_clip"] = donnees["vignette_clip"]
    if "poster" in donnees:
        if donnees["poster"] is not None and donnees["poster"] not in {p["id"] for p in c.get("posters") or []} | {"pochette"}:
            return None, "Poster inconnu."
        choix["poster"] = donnees["poster"]
    c["poster"] = _chemin_poster(c)
    _memoriser_choix(c)
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


def ajouter_image(tid, type_, octets: bytes, nom: str = ""):
    """Ajoute une image envoyée (ou lue depuis une adresse) et la CHOISIT. Retourne (fiche, erreur). Le poster reste propre au clip ; les autres types sont
    gardés pour l'artiste (la pochette et le cdart pour l'album) et resservent aux clips suivants."""
    with _VERROU:
        c = TRAVAUX.get(tid)
    if not c or c["etat"] != "pret":
        return None, "Ce clip n'est pas prêt."
    if type_ not in images_perso.TYPES:
        return None, "Type d'image inconnu."
    try:
        if type_ in ("poster", "thumb"):
            contenu, ext = images_perso.preparer(type_, octets)
            dossier = _CFG["dossier"] / c["id"]
            dossier.mkdir(parents=True, exist_ok=True)
            pid = f"perso{len([p for p in c.get('posters') or [] if p['id'].startswith('perso')]) + 1}"
            fichier = dossier / f"poster_{pid}.{ext}"
            fichier.write_bytes(contenu)
            c.setdefault("posters", []).append({"id": pid, "label": "Image ajoutée par toi", "fichier": str(fichier)})
            c.setdefault("choix", _choix_par_defaut(c["contexte"]))["vignette_clip" if type_ == "thumb" else "poster"] = pid
            c["poster"] = _chemin_poster(c)
            _maj(c)
            return fiche(tid), ""
        entree = images_perso.ajouter(c["contexte"].get("mbid", ""), type_, octets, nom, noms.normaliser(c["album"]))
    except images_perso.ImageError as e:
        return None, str(e)
    url = f"perso:{entree['id']}"
    choix = c.setdefault("choix", _choix_par_defaut(c["contexte"]))
    if type_ in TYPES_IMAGES:
        choix["images"][type_] = url
    elif type_ in ("couverture", "cdart"):
        choix["album"][type_] = url
    elif type_ == "fanart":
        _racine, groupe, _a = _dossiers(c)
        libres = sum(1 for cle in SLOTS_FANART if not (groupe / NOMS_FICHIERS[cle]).exists())
        if libres == 0:
            return None, "Les fonds d'écran existent déjà dans le dossier du groupe : ils ne sont jamais remplacés."
        actuels = choix["images"].get("fanart")
        actuels = [x["url"] for x in _candidats(c, "fanart")[:libres] if x["url"] != url] if actuels is None else [u for u in actuels if u != url]
        choix["images"]["fanart"] = ([url] + actuels)[:libres]                       # la nouvelle prend la première place libre
    _memoriser_choix(c)                                                              # retenue pour les autres clips du groupe
    c["plan"] = [{k: v for k, v in l.items() if k not in ("contenu",)} for l in construire_plan(c)]
    _maj(c)
    return fiche(tid), ""


def mettre_a_la_convention(tid) -> dict:
    """Pour un clip DÉJÀ rangé de la liste : voir `convention` (poster = pochette de l'album, ancienne image 16:9 = vignette). Retourne {ok, message}."""
    with _VERROU:
        c = TRAVAUX.get(tid)
    if not c or c["etat"] != "range":
        return {"ok": False, "error": "Ce clip n'est pas rangé."}
    video = next((Path(l["chemin"]) for l in (c.get("plan") or []) if l["type"] == "video"), None)
    if not video or not video.exists():
        return {"ok": False, "error": "Le clip rangé est introuvable (le partage est-il monté ?). Rien n'a été modifié."}
    e = convention.examiner(video)
    if e is None:
        poster, folder = video.parent / f"{video.stem}-poster.jpg", video.parent / "folder.jpg"
        if not poster.exists() or not folder.exists():
            return {"ok": False, "error": "Il manque le poster ou la pochette de l'album : rien n'a été modifié."}
        try:
            from PIL import Image
            with Image.open(folder) as im:
                if not 0.8 <= im.size[0] / im.size[1] <= 1.25:
                    return {"ok": False, "error": f"L'image de l'album n'est pas une pochette carrée ({im.size[0]}×{im.size[1]}) : rien n'a été modifié."}
        except OSError:
            pass
        return {"ok": True, "message": "Déjà à la convention : rien à faire."}
    faits = convention.appliquer_un(e, _CFG["dossier"].parent / "sauvegardes")
    emby = rangement.prevenir_emby(_CFG["emby"], str(video.parent.parent))
    return {"ok": True, "message": "; ".join(faits).capitalize() + ". " + emby}


def mettre_tous_a_la_convention() -> dict:
    """Applique la convention à tous les clips rangés de la liste. Retourne {ok, faits, deja, refuses: [(titre, raison)]}."""
    with _VERROU:
        ids = [(tid, c["titre"]) for tid, c in TRAVAUX.items() if c["etat"] == "range"]
    faits, deja, refuses = 0, 0, []
    for tid, titre in ids:
        r = mettre_a_la_convention(tid)
        if not r["ok"]:
            refuses.append((titre, r["error"]))
        elif r["message"].startswith("Déjà"):
            deja += 1
        else:
            faits += 1
    return {"ok": True, "faits": faits, "deja": deja, "refuses": refuses, "total": len(ids)}
