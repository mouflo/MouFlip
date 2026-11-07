#!/usr/bin/env python3
"""
MouFlip - clips vidéo K-pop pour Emby (même conception que MouFloster, MouFlopening et MouFlanga).

Premier morceau, en LECTURE SEULE : on donne un groupe, l'appli retrouve sa fiche et ses clips officiels, repère ceux déjà présents dans
« Video clip », montre où chacun serait rangé et le meilleur format disponible sur YouTube. Rien n'est téléchargé, rien n'est écrit dans « Video clip ».
"""
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from src.config import load_config, load_secrets_env

BASE_DIR = Path(__file__).resolve().parent
load_secrets_env()

from src import audio, chaines, emby_artistes, existants, formats, noms, sources, telechargement, travaux, ytcookies
from src.emby_client import EmbyClient
from src.netfix import repair_urllib3

BASE_VERSION = "0.1"
CONFIG = load_config()
EMBY = EmbyClient(CONFIG.get("emby", {}))
DOSSIER_CLIPS = (CONFIG.get("clips") or {}).get("dossier") or "/mnt/mouflosyno/Emby-Media/Video clip"
AUDIO = {"cible_db": float(((CONFIG.get("clips") or {}).get("audio") or {}).get("target_db", audio.REFERENCE_DB)),
         "debit": str(((CONFIG.get("clips") or {}).get("audio") or {}).get("bitrate", "192k")),
         "sans_perte": bool(((CONFIG.get("clips") or {}).get("audio") or {}).get("lossless", True))}


def _version() -> str:
    try:
        n = subprocess.run(["git", "-C", str(BASE_DIR), "rev-list", "--count", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        h = subprocess.run(["git", "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{BASE_VERSION}.{n or 0}" + (f" ({h})" if h else "")
    except Exception:
        return BASE_VERSION


APP_VERSION = _version()
app = Flask(__name__)


@app.before_request
def _repair_http():
    repair_urllib3()          # yt-dlp abîme urllib3 : on le répare avant chaque requête (voir src/netfix.py)


import auth
import diag

diag.setup_logging()
logger = logging.getLogger("mouflip")
auth.init_app(app, APP_VERSION)
import compression
compression.init_app(app)
import apparence
apparence.init_app(app)

FORMATS = {}                       # jobs de lecture des formats : id -> {"total", "fait", "resultats", "fini"}
_FORMATS_VERROU = threading.Lock()
_CACHE_GROUPES = {}                # (ancien cache en mémoire : remplacé par l'enregistrement sur disque ; gardé vide pour les réglages)
OUTILS_ETAT = {}


REDUCTION = {"actif": bool(((CONFIG.get("clips") or {}).get("reduction") or {}).get("actif", False)),
             "crf": int(((CONFIG.get("clips") or {}).get("reduction") or {}).get("crf", 30))}


def _roots():
    return [DOSSIER_CLIPS]


CACHE_DIR = BASE_DIR / "data" / "cache"
VERSION_LISTE = 3          # +1 quand la liste des clips s'enrichit : les listes enregistrées plus anciennes sont refaites toutes seules (si MusicBrainz répond)


def _ecrire_json(chemin: Path, data):
    import json
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, chemin)


def _lire_json(chemin: Path):
    import json
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fichier_chaines(mbid: str) -> Path:
    return CACHE_DIR / "chaines" / f"{mbid}.json"


def _chaines_officielles(mbid: str) -> list[dict]:
    """Les chaînes YouTube officielles du groupe : celles de MusicBrainz + celles ajoutées par l'utilisatrice. [{id, nom, url, source}]."""
    base = _lire_json(CACHE_DIR / "groupes" / f"{mbid}.json") or {}
    toutes = [{**x, "source": "MusicBrainz"} for x in base.get("chaines_mb", [])] + [{**x, "source": "toi"} for x in (_lire_json(_fichier_chaines(mbid)) or [])]
    vues, sortie = set(), []
    for x in toutes:
        if x.get("id") and x["id"] not in vues:
            vues.add(x["id"])
            sortie.append(x)
    return sortie


def _ids_chaines(mbid: str) -> set:
    return {x["id"] for x in _chaines_officielles(mbid)}


def _construire_base(mbid: str) -> dict:
    """Interroge MusicBrainz, TheAudioDB, fanart.tv et Wikipédia (le plus long : jusqu'à une minute). Lève SourceIndisponible si MusicBrainz ne répond pas."""
    erreurs = []
    fiche = sources.fiche_mb(mbid)
    chaines_mb = []
    for u in fiche.get("youtube", [])[:4]:                           # chaîne(s) officielle(s) que MusicBrainz connaît
        try:
            ch = chaines.resoudre(u)
            chaines_mb.append({"id": ch["id"], "nom": ch["nom"], "url": ch["url"]})
        except chaines.ChaineError:
            cid = chaines.id_depuis_url(u)
            if cid:
                chaines_mb.append({"id": cid, "nom": "", "url": u})
    ids = {x["id"] for x in chaines_mb} | {x["id"] for x in (_lire_json(_fichier_chaines(mbid)) or [])}
    clips = sources.clips_mb(mbid, fiche.get("nom", ""), decouverte=lambda pistes: formats.decouvrir(fiche.get("nom", ""), pistes, chaines=ids))
    try:
        rgs = [{"id": g["id"], "titre": g["titre"], "type": g["type"], "date": g["date"]} for g in sources._sorties(mbid)]
    except sources.SourceIndisponible:
        rgs = []
    audiodb, wiki = {}, ""
    try:
        audiodb = sources.images_audiodb(mbid)
    except sources.SourceIndisponible as e:
        erreurs.append(str(e))
    fanart = {"cle": False, "images": {}, "listes": {}, "albums": {}}
    try:
        fanart = sources.images_fanart(mbid)
    except sources.SourceIndisponible as e:
        erreurs.append(str(e))
    if not (audiodb.get("biographie")):
        try:
            wiki = sources.resume_wikipedia(fiche.get("nom", ""), "fr") or sources.resume_wikipedia(fiche.get("nom", ""), "en")
        except sources.SourceIndisponible as e:
            erreurs.append(str(e))
    return {"fiche": fiche, "clips": clips, "audiodb": audiodb, "wikipedia": wiki, "erreurs": erreurs, "fanart": fanart, "rgs": rgs, "chaines_mb": chaines_mb, "enregistre": time.time(), "v": VERSION_LISTE}


def _fichier_ajouts(mbid: str) -> Path:
    return CACHE_DIR / "ajouts" / f"{mbid}.json"


def _avec_ajouts(mbid: str, clips: list) -> list:
    """Les clips ajoutés à la main (lien YouTube) s'ajoutent à la liste du groupe, et restent après chaque « Actualiser »."""
    ajouts = _lire_json(_fichier_ajouts(mbid)) or []
    if not ajouts:
        return clips
    voulus = {noms.normaliser(a["titre"]) for a in ajouts}
    return sorted([c for c in clips if noms.normaliser(c["titre"]) not in voulus] + ajouts, key=lambda c: (c.get("annee") or "9999", noms.normaliser(c["titre"])))


def _groupe_complet(mbid: str, actualiser: bool = False) -> dict:
    """Fiche + clips d'un groupe, avec l'état « déjà dans la médiathèque » et le chemin de rangement prévu. Rien n'est écrit dans « Video clip ».
    La liste trouvée est ENREGISTRÉE sur le serveur (data/cache/groupes/<id>.json) et réutilisée tant qu'on ne demande pas de l'actualiser :
    MusicBrainz n'est pas redemandé à chaque ouverture, et si MusicBrainz est en panne, la liste enregistrée sert quand même."""
    fichier = CACHE_DIR / "groupes" / f"{mbid}.json"
    avertissement = ""
    base = None if actualiser else (_lire_json(fichier))
    ancienne = base if (base is not None and base.get("v", 1) < VERSION_LISTE) else None
    if ancienne is not None:
        base = None                                                      # liste enregistrée par une version plus ancienne : refaite (l'ancienne sert si ça échoue)
    if base is None:
        try:
            base = _construire_base(mbid)
            _ecrire_json(fichier, base)
        except sources.SourceIndisponible as e:
            base = _lire_json(fichier)
            if base is None:
                raise
            avertissement = f"MusicBrainz ne répond pas ({e}) : la liste enregistrée est gardée."
    fiche = base["fiche"]
    base = {**base, "clips": _avec_ajouts(mbid, base["clips"])}
    dossier = existants.dossier_du_groupe(DOSSIER_CLIPS, [fiche["nom"]] + fiche.get("alias", []))
    locaux = existants.lister(dossier) if dossier else []
    nom_dossier = dossier.name if dossier else fiche["nom"]
    clips = []
    for c in base["clips"]:
        local = existants.deja_la(c["titre"], locaux)
        clips.append({**c, "deja": bool(local), "fichier_local": local,
                      "chemin_prevu": noms.chemin_prevu(DOSSIER_CLIPS, nom_dossier, fiche["nom"], c["album"], c["titre"])})
    return {"fiche": fiche, "dossier_groupe": str(dossier) if dossier else "", "dossier_nouveau": not dossier, "nb_locaux": len(locaux),
            "biographie": base["audiodb"].get("biographie") or base["wikipedia"], "biographie_langue": base["audiodb"].get("biographie_langue") or ("fr" if base["wikipedia"] else ""),
            "images": {**base["audiodb"].get("images", {}), **base["fanart"]["images"]}, "images_album": base["fanart"]["albums"], "candidats": sources.candidats_images(base["audiodb"], base["fanart"]),
            "genre_audiodb": base["audiodb"].get("genre", ""), "fanart_cle": base["fanart"]["cle"], "chaines": _chaines_officielles(mbid), "clips": clips, "erreurs": base["erreurs"] + ([avertissement] if avertissement else []), "racine": DOSSIER_CLIPS,
            "racine_ok": Path(DOSSIER_CLIPS).is_dir(), "enregistre": base.get("enregistre", 0), "depuis_cache": not actualiser and not avertissement and bool(base.get("enregistre"))}


@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.route("/api/groupes/chercher")
def api_chercher():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"groupes": []})
    cle = noms.normaliser(q) or q.lower()
    fichier = CACHE_DIR / "recherches.json"
    enregistrees = _lire_json(fichier) or {}
    ancien = enregistrees.get(cle)
    if ancien and time.time() - ancien["date"] < 7 * 24 * 3600:
        return jsonify({"groupes": ancien["groupes"]})
    try:
        groupes = sources.chercher_groupes(q)
    except sources.SourceIndisponible as e:
        if ancien:                                                    # panne : la recherche enregistrée sert quand même
            return jsonify({"groupes": ancien["groupes"], "avertissement": f"MusicBrainz ne répond pas ({e}) : résultats enregistrés."})
        return jsonify({"error": f"MusicBrainz est injoignable : {e}. Réessaie dans un instant."}), 502
    if groupes:
        enregistrees[cle] = {"date": time.time(), "groupes": groupes}
        _ecrire_json(fichier, enregistrees)
    return jsonify({"groupes": groupes})


@app.route("/api/groupe/<mbid>")
def api_groupe(mbid):
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid):
        return jsonify({"error": "Identifiant de groupe invalide."}), 400
    try:
        return jsonify(_groupe_complet(mbid, actualiser=request.args.get("actualiser") == "1"))
    except sources.SourceIndisponible as e:
        return jsonify({"error": f"MusicBrainz est injoignable : {e}. Réessaie dans un instant."}), 502


def _lire_formats(job_id, groupe, titres, videos):
    for t in titres:
        r = formats.analyser(groupe, t, video_id=videos.get(t, ""))
        with _FORMATS_VERROU:
            FORMATS[job_id]["resultats"][t] = r
            FORMATS[job_id]["fait"] += 1
    with _FORMATS_VERROU:
        FORMATS[job_id]["fini"] = True


@app.route("/api/formats", methods=["POST"])
def api_formats():
    """Lance la lecture des formats (lecture seule) de quelques clips. Un seul lot à la fois."""
    b = request.get_json(silent=True) or {}
    groupe = str(b.get("groupe", "")).strip()
    titres = [str(t).strip() for t in (b.get("titres") or []) if str(t).strip()][:60]
    if not groupe or not titres:
        return jsonify({"error": "Choisis au moins un clip."}), 400
    with _FORMATS_VERROU:
        if any(not j["fini"] for j in FORMATS.values()):
            return jsonify({"error": "Une lecture de formats est déjà en cours : attends qu'elle finisse."}), 409
        job = uuid.uuid4().hex[:10]
        FORMATS[job] = {"total": len(titres), "fait": 0, "resultats": {}, "fini": False}
    videos = {str(k): str(v) for k, v in (b.get("videos") or {}).items() if isinstance(v, str)}
    threading.Thread(target=_lire_formats, args=(job, groupe, titres, videos), daemon=True).start()
    return jsonify({"job": job, "total": len(titres)})


@app.route("/api/formats/etat")
def api_formats_etat():
    with _FORMATS_VERROU:
        j = FORMATS.get(request.args.get("job", ""))
        if not j:
            return jsonify({"error": "Lot inconnu."}), 404
        return jsonify({"total": j["total"], "fait": j["fait"], "fini": j["fini"], "resultats": dict(j["resultats"])})


def _contexte_groupe(mbid: str) -> dict:
    """Tout ce qu'il faut savoir du groupe pour ranger ses clips (fiche, dossier existant, images, biographie)."""
    g = _groupe_complet(mbid)
    return {"nom": g["fiche"]["nom"], "mbid": mbid, "fiche": g["fiche"], "dossier_groupe": g["dossier_groupe"], "images": g["images"],
            "biographie": g["biographie"], "biographie_langue": g.get("biographie_langue", ""), "images_album": g.get("images_album", {}),
            "candidats": g.get("candidats", {}), "genre": (g["fiche"].get("genres") or ["K-Pop"])[0].title() if (g["fiche"].get("genres") or []) else "K-Pop"}


def _resoudre_rg(ctx: dict, titre: str, album: str) -> str:
    """Quel groupe de sorties (album) de MusicBrainz pour ce clip ? Celui du même titre dans la liste enregistrée, puis celui d'un autre clip du même
    album ; on préfère un groupe qui a des images chez fanart.tv (la pochette d'un album n'est pas toujours rangée sous le single)."""
    try:
        clips = _groupe_complet(ctx["mbid"])["clips"]
    except Exception:
        return ""
    images = ctx.get("images_album") or {}
    memes = [c["rg"] for c in clips if c.get("rg") and c["titre"] == titre]
    memes += [c["rg"] for c in clips if c.get("rg") and noms.normaliser(c["album"]) == noms.normaliser(album) and c["rg"] not in memes]
    return next((rg for rg in memes if images.get(rg)), memes[0] if memes else "")


travaux.rg_fn = _resoudre_rg
travaux.chaines_fn = lambda ctx: _ids_chaines(ctx.get("mbid", ""))


@app.route("/api/travaux", methods=["GET", "POST"])
def api_travaux():
    if request.method == "GET":
        return jsonify({"travaux": travaux.lister(), "place_go": round(__import__("src.telechargement", fromlist=["x"]).place_libre_go(BASE_DIR / "data" / "travail"), 1)})
    b = request.get_json(silent=True) or {}
    mbid, clips = str(b.get("mbid", "")), b.get("clips") or []
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not clips:
        return jsonify({"error": "Choisis au moins un clip."}), 400
    try:
        ctx = _contexte_groupe(mbid)
    except sources.SourceIndisponible as e:
        return jsonify({"error": f"MusicBrainz est injoignable : {e}"}), 502
    choix = _choix_lire().get(mbid, {})
    qualites = (_lire_json(_qualite_fichier()) or {}).get(mbid, {})
    reductions = (_lire_json(_reduction_fichier()) or {}).get(mbid, {})
    ajoutes = []
    for c in clips[:40]:
        titre = str(c.get("titre", "")).strip()
        if not titre:
            continue
        video_id = str(c.get("video_id") or choix.get(titre, "") or next((x.get("video_id") for x in _groupe_complet(mbid)["clips"] if x["titre"] == titre and x.get("video_id")), "") or "")
        q = qualites.get(titre) or {}
        if q.get("video_id") and (not video_id or video_id == q["video_id"]):          # qualité choisie sur CETTE vidéo seulement
            video_id = q["video_id"]
            fid, texte = q.get("format_id", ""), q.get("texte", "")
        else:
            fid, texte = "", ""
        ajoutes.append(travaux.ajouter(ctx, {"titre": titre, "album": str(c.get("album") or titre), "annee": str(c.get("annee") or ""),
                                              "mbid": str(c.get("mbid") or ""), "rg": str(c.get("rg") or "") or _resoudre_rg(ctx, titre, str(c.get("album") or titre)), "video_id": video_id,
                                              "format_id": fid, "qualite": texte, "reduction_mode": reductions.get(titre, "defaut")}))
    return jsonify({"travaux": ajoutes})


_RE_VIDEO = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})|^([\w-]{11})$")


@app.route("/api/ajout/analyser", methods=["POST"])
def api_ajout_analyser():
    """Un lien YouTube collé par l'utilisatrice → titre, album probable, année : de quoi ajouter à la main un clip que les bases ne connaissent pas."""
    b = request.get_json(silent=True) or {}
    mbid, lien = str(b.get("mbid", "")), str(b.get("lien", "")).strip()
    m = _RE_VIDEO.search(lien)
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not m:
        return jsonify({"error": "Colle le lien d'une vidéo YouTube (https://www.youtube.com/watch?v=… ou https://youtu.be/…)."}), 400
    vid = m.group(1) or m.group(2)
    try:
        from yt_dlp import YoutubeDL
        o = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 25}
        o.update(ytcookies.opts())
        infos = YoutubeDL(o).extract_info(f"https://www.youtube.com/watch?v={vid}", download=False) or {}
    except Exception as e:
        return jsonify({"error": f"YouTube ne donne pas cette vidéo : {str(e)[:160]}"}), 502
    base = _lire_json(CACHE_DIR / "groupes" / f"{mbid}.json") or {}
    nom = (base.get("fiche") or {}).get("nom", "")
    titre = formats.titre_du_clip(infos.get("title") or "", nom) or (infos.get("title") or "")
    date = infos.get("upload_date") or ""                                 # AAAAMMJJ
    suggere = ""
    try:
        from datetime import date as _d
        t0 = _d(int(date[:4]), int(date[4:6]), int(date[6:8]))
        proches = []
        for g in base.get("rgs") or []:
            try:
                p = (g["date"] + "-01-01").split("-") if len(g["date"]) == 4 else (g["date"] + "-01").split("-") if len(g["date"]) == 7 else g["date"].split("-")
                ecart = (_d(int(p[0]), int(p[1]), int(p[2])) - t0).days
            except (ValueError, IndexError):
                continue
            if -45 <= ecart <= 14:                                       # l'EP sort en général peu avant ou le jour de son clip
                proches.append((abs(ecart) + (0 if g["type"] in ("EP", "Album") else 20), g["titre"]))
        suggere = min(proches)[1] if proches else ""
    except (ValueError, TypeError):
        pass
    return jsonify({"video_id": vid, "titre_video": infos.get("title", ""), "chaine": infos.get("channel") or infos.get("uploader") or "", "duree": infos.get("duration") or 0,
                    "titre": titre, "annee": date[:4], "album_suggere": suggere or titre,
                    "albums": sorted({g["titre"] for g in base.get("rgs") or []} | {c["album"] for c in base.get("clips") or []})})


@app.route("/api/ajout/confirmer", methods=["POST"])
def api_ajout_confirmer():
    b = request.get_json(silent=True) or {}
    mbid, titre, album, vid = str(b.get("mbid", "")), str(b.get("titre", "")).strip(), str(b.get("album", "")).strip(), str(b.get("video_id", ""))
    annee = str(b.get("annee", "")).strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not titre or len(titre) > 160 or len(album) > 160 or not re.fullmatch(r"[\w-]{11}", vid) or (annee and not re.fullmatch(r"\d{4}", annee)):
        return jsonify({"ok": False, "error": "Titre, album (160 caractères au plus) ou année (4 chiffres) invalide."}), 400
    base = _lire_json(CACHE_DIR / "groupes" / f"{mbid}.json") or {}
    rg = next((g["id"] for g in base.get("rgs") or [] if noms.normaliser(g["titre"]) == noms.normaliser(album or titre) and g["type"] in ("EP", "Album")), "") or \
        next((g["id"] for g in base.get("rgs") or [] if noms.normaliser(g["titre"]) == noms.normaliser(album or titre)), "")
    ajouts = [a for a in (_lire_json(_fichier_ajouts(mbid)) or []) if noms.normaliser(a["titre"]) != noms.normaliser(titre)]
    ajouts.append({"titre": titre, "album": album or titre, "annee": annee, "mbid": "", "type": "clip", "source": "ajout", "duree": 0, "rg": rg, "video_id": vid})
    _ecrire_json(_fichier_ajouts(mbid), ajouts)
    d = _choix_lire()
    d.setdefault(mbid, {})[titre] = vid                                   # la vidéo collée est celle qui sera téléchargée
    _ecrire_json(_choix_fichier(), d)
    return jsonify({"ok": True})


@app.route("/api/ajout/retirer", methods=["POST"])
def api_ajout_retirer():
    b = request.get_json(silent=True) or {}
    mbid, titre = str(b.get("mbid", "")), str(b.get("titre", "")).strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid):
        return jsonify({"ok": False, "error": "Groupe invalide."}), 400
    ajouts = [a for a in (_lire_json(_fichier_ajouts(mbid)) or []) if a["titre"] != titre]
    _ecrire_json(_fichier_ajouts(mbid), ajouts)
    return jsonify({"ok": True})


def _ecrire_chaines_toi(mbid: str, liste: list):
    _ecrire_json(_fichier_chaines(mbid), liste)
    _APERCUS.clear()                                                     # les vidéos « officielles » se jugent d'après ces chaînes : on les reclasse


@app.route("/api/chaines/<mbid>")
def api_chaines(mbid):
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid):
        return jsonify({"error": "Groupe invalide."}), 400
    return jsonify({"chaines": _chaines_officielles(mbid)})


@app.route("/api/chaines/ajouter", methods=["POST"])
def api_chaines_ajouter():
    """Ajoute une chaîne YouTube officielle (la chaîne du groupe, celle de son label) d'après son adresse collée."""
    b = request.get_json(silent=True) or {}
    mbid = str(b.get("mbid", ""))
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid):
        return jsonify({"ok": False, "error": "Groupe invalide."}), 400
    try:
        ch = chaines.resoudre(str(b.get("lien", "")))
    except chaines.ChaineError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    liste = [x for x in (_lire_json(_fichier_chaines(mbid)) or []) if x["id"] != ch["id"]] + [{"id": ch["id"], "nom": ch["nom"], "url": ch["url"]}]
    _ecrire_chaines_toi(mbid, liste)
    return jsonify({"ok": True, "chaine": ch, "chaines": _chaines_officielles(mbid)})


@app.route("/api/chaines/officielle", methods=["POST"])
def api_chaines_officielle():
    """« Cette chaîne est officielle pour ce groupe » (bouton du lecteur) : sans avoir à retrouver son adresse."""
    b = request.get_json(silent=True) or {}
    mbid, cid, nom = str(b.get("mbid", "")), str(b.get("channel_id", "")), str(b.get("nom", ""))[:120]
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not chaines.ID_VALIDE.fullmatch(cid):
        return jsonify({"ok": False, "error": "Chaîne invalide."}), 400
    liste = [x for x in (_lire_json(_fichier_chaines(mbid)) or []) if x["id"] != cid] + [{"id": cid, "nom": nom, "url": f"https://www.youtube.com/channel/{cid}"}]
    _ecrire_chaines_toi(mbid, liste)
    return jsonify({"ok": True, "chaines": _chaines_officielles(mbid)})


@app.route("/api/chaines/retirer", methods=["POST"])
def api_chaines_retirer():
    """Retire une chaîne AJOUTÉE par l'utilisatrice (celles de MusicBrainz restent)."""
    b = request.get_json(silent=True) or {}
    mbid, cid = str(b.get("mbid", "")), str(b.get("id", ""))
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid):
        return jsonify({"ok": False, "error": "Groupe invalide."}), 400
    _ecrire_chaines_toi(mbid, [x for x in (_lire_json(_fichier_chaines(mbid)) or []) if x["id"] != cid])
    return jsonify({"ok": True, "chaines": _chaines_officielles(mbid)})


@app.route("/api/travaux/<tid>/fiche", methods=["GET"])
def api_travaux_fiche(tid):
    f = travaux.fiche(tid)
    return (jsonify(f), 200) if f else (jsonify({"error": "Ce clip n'est pas prêt : sa fiche n'existe pas encore."}), 404)


@app.route("/api/travaux/<tid>/choix", methods=["POST"])
def api_travaux_choix(tid):
    f, err = travaux.enregistrer_choix(tid, request.get_json(silent=True) or {})
    return (jsonify(f), 200) if f else (jsonify({"error": err}), 400)


@app.route("/api/travaux/<tid>/poster/<pid>")
def api_travaux_poster(tid, pid):
    from flask import send_file
    chemin = travaux.fichier_poster(tid, pid) if re.fullmatch(r"\w{1,12}", pid) else ""
    if not chemin:
        return jsonify({"error": "Poster introuvable."}), 404
    return send_file(chemin, mimetype="image/jpeg", max_age=60)


@app.route("/api/existant")
def api_existant():
    """Montre une image DÉJÀ présente dans « Video clip » (pour la comparer aux propositions). Seulement un .jpg/.png situé sous le dossier des clips."""
    from flask import send_file
    chemin = Path(request.args.get("chemin", ""))
    try:
        racine = Path(DOSSIER_CLIPS).resolve()
        ok = chemin.resolve().is_relative_to(racine) and chemin.suffix.lower() in (".jpg", ".jpeg", ".png") and chemin.is_file()
    except (OSError, ValueError):
        ok = False
    if not ok:
        return jsonify({"error": "Image introuvable."}), 404
    return send_file(chemin, max_age=60)


@app.route("/api/emby/artiste")
def api_emby_artiste():
    nom = (request.args.get("nom") or "").strip()
    if not nom:
        return jsonify({"error": "Nom manquant."}), 400
    return jsonify(emby_artistes.chercher(EMBY, nom))


@app.route("/api/travaux/<tid>/ranger", methods=["POST"])
def api_travaux_ranger(tid):
    r = travaux.ranger(tid)
    return jsonify(r), (200 if r.get("ok") else 409)


@app.route("/api/travaux/<tid>/relancer", methods=["POST"])
def api_travaux_relancer(tid):
    return jsonify(travaux.relancer(tid))


@app.route("/api/travaux/<tid>/supprimer", methods=["POST"])
def api_travaux_supprimer(tid):
    r = travaux.supprimer(tid)
    return jsonify(r), (200 if r.get("ok") else 409)


def _choix_fichier():
    return BASE_DIR / "data" / "choix.json"


def _choix_lire() -> dict:
    import json
    try:
        return json.loads(_choix_fichier().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.route("/api/choix/<mbid>", methods=["GET"])
def api_choix_lire(mbid):
    return jsonify({"choix": _choix_lire().get(mbid, {})})


@app.route("/api/choix", methods=["POST"])
def api_choix_ecrire():
    """Retient la vidéo choisie pour un clip (survit au rechargement de la page)."""
    import json
    b = request.get_json(silent=True) or {}
    mbid, titre, vid = str(b.get("mbid", "")), str(b.get("titre", "")).strip(), str(b.get("video_id", ""))
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not titre or not re.fullmatch(r"[\w-]{11}", vid):
        return jsonify({"ok": False, "error": "Choix invalide."}), 400
    d = _choix_lire()
    d.setdefault(mbid, {})[titre] = vid
    tmp = _choix_fichier().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _choix_fichier())
    return jsonify({"ok": True})


@app.route("/api/settings/fanart", methods=["GET", "POST"])
def api_fanart():
    """Clé personnelle fanart.tv (gratuite) : logos, bannières, fonds et pochettes soignés. Jamais renvoyée en entier."""
    cle = os.getenv("FANART_API_KEY", "").strip()
    if request.method == "GET":
        return jsonify({"configured": bool(cle), "hint": ("…" + cle[-4:]) if len(cle) >= 8 else ""})
    b = request.get_json(silent=True) or {}
    nouvelle = str(b.get("api_key", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{16,64}", nouvelle):
        return jsonify({"ok": False, "error": "Clé invalide : lettres et chiffres uniquement (colle-la sans espace ni guillemets)."}), 400
    try:
        r = __import__("requests").get("https://webservice.fanart.tv/v3/music/48646387-1664-4c9a-9139-9bfd091b823c", params={"api_key": nouvelle},
                                       headers={"User-Agent": sources.UA}, timeout=15)
    except Exception as e:
        return jsonify({"ok": False, "error": f"fanart.tv est injoignable : {e}"}), 502
    ok = r.status_code == 200
    if r.status_code in (401, 403):
        return jsonify({"ok": False, "error": "fanart.tv refuse cette clé."}), 400
    if b.get("test"):
        return jsonify({"ok": ok, "message": "Clé acceptée par fanart.tv." if ok else f"fanart.tv a répondu {r.status_code}."})
    if not ok:
        return jsonify({"ok": False, "error": f"fanart.tv a répondu {r.status_code} : rien n'a été enregistré."}), 400
    emby_settings._write_secret(BASE_DIR / "data" / "secrets.env", "FANART_API_KEY", nouvelle)
    os.environ["FANART_API_KEY"] = nouvelle
    _CACHE_GROUPES.clear()
    logger.info("Clé fanart.tv enregistrée")
    return jsonify({"ok": True, "message": "Clé enregistrée et acceptée par fanart.tv."})


def _route_cle(nom_route, variable, tester):
    """Une clé API saisie dans ⚙️ Réglages : testée auprès du service (« Tester » ne l'enregistre pas), enregistrée dans data/secrets.env (droits 600),
    jamais renvoyée en entier (seulement ses 4 derniers caractères)."""
    def vue():
        cle = os.getenv(variable, "").strip()
        if request.method == "GET":
            return jsonify({"configured": bool(cle), "hint": ("…" + cle[-4:]) if len(cle) >= 8 else ""})
        b = request.get_json(silent=True) or {}
        nouvelle = str(b.get("api_key", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", nouvelle):
            return jsonify({"ok": False, "error": "Clé invalide : lettres et chiffres uniquement (colle-la sans espace ni guillemets)."}), 400
        ok, msg = tester(nouvelle)
        if b.get("test"):
            return jsonify({"ok": ok, "message": msg})
        if not ok:
            return jsonify({"ok": False, "error": msg + ". Rien n'a été enregistré."}), 400
        emby_settings._write_secret(BASE_DIR / "data" / "secrets.env", variable, nouvelle)
        os.environ[variable] = nouvelle
        _CACHE_GROUPES.clear()
        logger.info("Clé %s enregistrée", variable)
        return jsonify({"ok": True, "message": "Clé enregistrée. " + msg})
    vue.__name__ = "cle_" + nom_route
    app.add_url_rule(f"/api/settings/{nom_route}", view_func=vue, methods=["GET", "POST"])


def _tester_http(url, **kw):
    try:
        r = __import__("requests").get(url, headers={"User-Agent": sources.UA, **kw.pop("headers", {})}, timeout=15, **kw)
    except Exception as e:
        return None, f"Service injoignable ({e})"
    return r.status_code, ""


def _tester_audiodb(cle):
    code, err = _tester_http(f"https://www.theaudiodb.com/api/v1/json/{cle}/artist-mb.php", params={"i": "48646387-1664-4c9a-9139-9bfd091b823c"})
    if code is None:
        return False, err
    return (code == 200), ("Clé acceptée par TheAudioDB." if code == 200 else f"TheAudioDB a répondu {code}")


def _tester_imvdb(cle):
    code, err = _tester_http("https://imvdb.com/api/v1/video/215233331710", headers={"IMVDB-APP-KEY": cle})
    if code is None:
        return False, err
    return (code == 200), ("Clé acceptée par IMVDb (leur recherche est coupée pour l'instant : elle servira quand elle reviendra)." if code == 200
                           else "IMVDb refuse cette clé" if code == 403 else f"IMVDb a répondu {code}")


_route_cle("theaudiodb", "THEAUDIODB_KEY", _tester_audiodb)
_route_cle("imvdb", "IMVDB_API_KEY", _tester_imvdb)


MODES_REDUCTION = ("defaut", "non") + tuple(telechargement.CRF_REDUCTION)


def _reduction_fichier():
    return BASE_DIR / "data" / "choix_reduction.json"


@app.route("/api/choix_reduction/<mbid>")
def api_choix_reduction_lire(mbid):
    return jsonify({"choix": (_lire_json(_reduction_fichier()) or {}).get(mbid, {})})


@app.route("/api/choix_reduction", methods=["POST"])
def api_choix_reduction():
    """Retient, clip par clip, comment réduire la taille : défaut (réglage général), non, ou un niveau. Un clip peut être réduit alors que le réglage général est coupé."""
    b = request.get_json(silent=True) or {}
    mbid, mode = str(b.get("mbid", "")), str(b.get("mode", "defaut"))
    titres = [str(t).strip() for t in (b.get("titres") or ([b.get("titre")] if b.get("titre") else [])) if str(t).strip()][:60]
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not titres or mode not in MODES_REDUCTION:
        return jsonify({"ok": False, "error": "Choix invalide."}), 400
    d = _lire_json(_reduction_fichier()) or {}
    for t in titres:
        if mode == "defaut":
            d.get(mbid, {}).pop(t, None)
        else:
            d.setdefault(mbid, {})[t] = mode
    _ecrire_json(_reduction_fichier(), d)
    return jsonify({"ok": True})


@app.route("/api/settings/reduction", methods=["GET", "POST"])
def api_reduction():
    """Réduire la taille des clips : l'image est réencodée en VP9 (qualité constante, 8 images/s environ), le son est recopié. Désactivé par défaut."""
    if request.method == "GET":
        return jsonify({**REDUCTION, "choix": telechargement.CRF_REDUCTION})
    b = request.get_json(silent=True) or {}
    try:
        crf = int(b.get("crf", REDUCTION["crf"]))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Choisis un niveau de qualité."}), 400
    if not 20 <= crf <= 45:
        return jsonify({"ok": False, "error": "Le niveau doit être entre 20 (très fidèle, gros fichier) et 45 (petit fichier)."}), 400
    actif = bool(b.get("actif"))
    import json
    chemin = BASE_DIR / "config.json"
    try:
        cfg = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
    except ValueError:
        return jsonify({"ok": False, "error": "config.json est illisible : rien n'a été modifié."}), 500
    cfg.setdefault("clips", {})["reduction"] = {"actif": actif, "crf": crf}
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chemin)
    REDUCTION.update(actif=actif, crf=crf)
    return jsonify({"ok": True, "message": ("Enregistré : les prochains clips auront leur taille réduite (environ 10 minutes de plus par clip)." if actif
                                              else "Enregistré : la taille des clips n'est pas réduite (le fichier de YouTube est gardé tel quel).")})


@app.route("/api/settings/audio", methods=["GET", "POST"])
def api_audio():
    """Volume des clips (même réglage que les thèmes de MouFlopening : 89 dB) et débit AAC de la piste refaite."""
    if request.method == "GET":
        return jsonify({**AUDIO, "aacgain": audio.aacgain_present()})
    b = request.get_json(silent=True) or {}
    try:
        cible = round(float(str(b.get("target_db", "")).replace(",", ".")), 1)
    except ValueError:
        return jsonify({"ok": False, "error": "Entre un nombre, par exemple 89."}), 400
    debit = str(b.get("bitrate", AUDIO["debit"])).strip().lower()
    if not 70 <= cible <= 100:
        return jsonify({"ok": False, "error": "Choisis un niveau entre 70 et 100 dB (89 est la référence habituelle)."}), 400
    if debit not in ("128k", "160k", "192k", "256k", "320k"):
        return jsonify({"ok": False, "error": "Débit audio : 128k, 160k, 192k, 256k ou 320k."}), 400
    import json
    chemin = BASE_DIR / "config.json"
    try:
        cfg = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
    except ValueError:
        return jsonify({"ok": False, "error": "config.json est illisible : rien n'a été modifié."}), 500
    cfg.setdefault("clips", {}).setdefault("audio", {}).update(target_db=cible, bitrate=debit, lossless=bool(b.get("sans_perte", AUDIO["sans_perte"])))
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chemin)
    AUDIO.update(cible_db=cible, debit=debit, sans_perte=bool(b.get("sans_perte", AUDIO["sans_perte"])))
    return jsonify({"ok": True, "message": f"Enregistré : les prochains clips seront à {cible:g} dB ("
                    + ("réglage sans perte quand le son est en AAC, sinon " if AUDIO["sans_perte"] else "") + f"AAC {debit} refait). Les clips déjà rangés ne changent pas."})


_APERCUS = {}                      # (groupe, titre) -> (heure, candidats) : 30 min


@app.route("/api/apercu", methods=["POST"])
def api_apercu():
    """Les vidéos YouTube possibles pour un clip (la plus probable d'abord), pour les REGARDER et choisir. Lecture seule."""
    b = request.get_json(silent=True) or {}
    groupe, titre = str(b.get("groupe", "")).strip(), str(b.get("titre", "")).strip()
    if not groupe or not titre:
        return jsonify({"error": "Clip inconnu."}), 400
    mbid = str(b.get("mbid", ""))
    ids = _ids_chaines(mbid) if re.fullmatch(r"[0-9a-f-]{36}", mbid) else set()
    cle = (groupe, titre, frozenset(ids))
    ancien = _APERCUS.get(cle)
    if ancien and time.time() - ancien[0] < 1800:
        return jsonify({"candidats": ancien[1]})
    try:
        c = formats.candidats(groupe, titre, chaines=ids)
    except Exception as e:
        logger.warning("Aperçu impossible pour « %s - %s » : %s", groupe, titre, e)
        return jsonify({"error": f"YouTube ne répond pas : {str(e)[:160]}"}), 502
    _APERCUS[cle] = (time.time(), c)
    return jsonify({"candidats": c})


_QUALITES = {}                     # id de vidéo -> (heure, qualités) : 1 h


@app.route("/api/apercu/qualites", methods=["POST"])
def api_qualites():
    """Toutes les qualités d'une vidéo (1080p, 1440p, 4K… avec codec, débit et taille) pour choisir laquelle télécharger. Lecture seule."""
    vid = str((request.get_json(silent=True) or {}).get("video_id", ""))
    ancien = _QUALITES.get(vid)
    if ancien and time.time() - ancien[0] < 3600:
        return jsonify(ancien[1])
    r = formats.liste_qualites(vid)
    if r.get("etat") == "ok":
        _QUALITES[vid] = (time.time(), r)
    return jsonify(r)


def _qualite_fichier():
    return BASE_DIR / "data" / "choix_qualite.json"


@app.route("/api/choix_qualite/<mbid>", methods=["GET"])
def api_choix_qualite_lire(mbid):
    return jsonify({"choix": (_lire_json(_qualite_fichier()) or {}).get(mbid, {})})


@app.route("/api/choix_qualite", methods=["POST"])
def api_choix_qualite():
    """Retient la qualité choisie pour un clip ({video_id, format_id, texte}) ; format_id vide = « la meilleure » (retire le choix)."""
    b = request.get_json(silent=True) or {}
    mbid, titre, vid, fid = str(b.get("mbid", "")), str(b.get("titre", "")).strip(), str(b.get("video_id", "")), str(b.get("format_id", ""))
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid) or not titre or (fid and (not re.fullmatch(r"[\w-]{11}", vid) or not re.fullmatch(r"[\w.-]{1,20}", fid))):
        return jsonify({"ok": False, "error": "Choix invalide."}), 400
    d = _lire_json(_qualite_fichier()) or {}
    if fid:
        d.setdefault(mbid, {})[titre] = {"video_id": vid, "format_id": fid, "texte": str(b.get("texte", ""))[:80]}
    else:
        d.get(mbid, {}).pop(titre, None)
    _ecrire_json(_qualite_fichier(), d)
    return jsonify({"ok": True})


_VERSIONS = {}                     # id de vidéo -> (heure, infos) : 1 h


@app.route("/api/apercu/version", methods=["POST"])
def api_version():
    """Format et taille d'une des vidéos possibles pour un clip (pour comparer les versions). Lecture seule."""
    vid = str((request.get_json(silent=True) or {}).get("video_id", ""))
    ancien = _VERSIONS.get(vid)
    if ancien and time.time() - ancien[0] < 3600:
        return jsonify(ancien[1])
    r = formats.infos_version(vid)
    if r.get("etat") == "ok":
        _VERSIONS[vid] = (time.time(), r)
    return jsonify(r)


def _cmd_version(cmd, *args):
    try:
        r = subprocess.run([cmd, *args], capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr).splitlines()[0].strip() if (r.stdout or r.stderr) else ""
    except Exception:
        return ""


@app.route("/api/outils")
def api_outils():
    import shutil
    from importlib.metadata import PackageNotFoundError, version
    def v(p):
        try:
            return version(p)
        except PackageNotFoundError:
            return ""
    return jsonify({"yt_dlp": v("yt-dlp"), "yt_dlp_ejs": v("yt-dlp-ejs"), "deno": (_cmd_version("deno", "--version") or "").replace("deno ", ""),
                    "ffmpeg": bool(shutil.which("ffmpeg")), "aacgain": audio.aacgain_present(), "cookies": ytcookies.state(), "racine": DOSSIER_CLIPS, "racine_ok": Path(DOSSIER_CLIPS).is_dir(),
                    "emby": EMBY.describe()})


@app.route("/reglages")
def reglages():
    return render_template("reglages.html", version=APP_VERSION)


import emby_settings
emby_settings.init_app(app, BASE_DIR, lambda: EMBY.host, lambda key: setattr(EMBY, "api_key", key))
import fs_browser
fs_browser.init_app(app)


@app.route("/api/settings/emby-host", methods=["POST"])
def api_emby_host():
    host = str((request.get_json(silent=True) or {}).get("host", "")).strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+(:\d{1,5})?(/\S*)?$", host) or set('"$`\\\n\r') & set(host):
        return jsonify({"ok": False, "error": "Adresse invalide : elle doit ressembler à http://192.168.1.10:8096"}), 400
    emby_settings._write_secret(BASE_DIR / "data" / "secrets.env", "EMBY_URL", host)
    os.environ["EMBY_URL"] = host
    EMBY.host = host
    logger.info("Adresse d'Emby mise à jour : %s", host)
    return jsonify({"ok": True, "message": "Adresse enregistrée."})


@app.route("/api/settings/dossier", methods=["GET", "POST"])
def api_dossier():
    """Dossier des clips : enregistré dans config.json (local, jamais sur GitHub), pris en compte tout de suite."""
    global DOSSIER_CLIPS
    if request.method == "GET":
        return jsonify({"dossier": DOSSIER_CLIPS, "ok": Path(DOSSIER_CLIPS).is_dir()})
    d = str((request.get_json(silent=True) or {}).get("dossier", "")).strip().rstrip("/")
    if not d or not Path(d).is_dir():
        return jsonify({"ok": False, "error": f"Dossier introuvable sur le serveur : « {d} » (le partage est-il monté ?). Rien n'a été enregistré."}), 400
    import json
    chemin = BASE_DIR / "config.json"
    try:
        cfg = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
    except ValueError:
        return jsonify({"ok": False, "error": "config.json est illisible : rien n'a été modifié."}), 500
    cfg.setdefault("clips", {})["dossier"] = d
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chemin)
    DOSSIER_CLIPS = d
    _CACHE_GROUPES.clear()
    logger.info("Dossier des clips : %s", d)
    return jsonify({"ok": True, "message": "Enregistré."})


@app.route("/api/settings/youtube-cookies", methods=["GET", "POST"])
def api_cookies():
    if request.method == "GET":
        return jsonify(ytcookies.state())
    body = request.get_json(silent=True) or {}
    if body.get("clear"):
        ytcookies.clear()
        logger.info("Cookies YouTube supprimés")
        return jsonify({"ok": True, "message": "Cookies supprimés."})
    ok, msg, _n = ytcookies.validate(str(body.get("text", "")))
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    if body.get("test"):
        return jsonify({"ok": True, "message": msg})
    ytcookies.save(str(body.get("text", "")))
    logger.info("Cookies YouTube enregistrés (%s)", msg)
    return jsonify({"ok": True, "message": "Enregistré : " + msg})


import redemarrage
redemarrage.init_app(app, BASE_DIR)
import redemarrer
redemarrer.init_app(app, lambda: (["lecture des formats YouTube"] if any(not j["fini"] for j in FORMATS.values()) else []) + travaux.occupe())
travaux.emby_artiste_fn = lambda nom: emby_artistes.chercher(EMBY, nom)
travaux.configurer(BASE_DIR / "data", lambda: DOSSIER_CLIPS, lambda: AUDIO["cible_db"], lambda: AUDIO["debit"], EMBY, lambda: AUDIO["sans_perte"], lambda: REDUCTION, ecriture=False)
diag.init_app(app, APP_VERSION, _roots, EMBY.describe, lambda: "aucun traitement en lot (lecture seule)")

def _mise_a_jour_ytdlp(premiere_attente=120, toutes=24 * 3600):
    """yt-dlp suit les changements de YouTube : mise à jour au démarrage puis chaque jour, jamais pendant un clip."""
    time.sleep(premiere_attente)
    while True:
        while travaux.occupe():
            time.sleep(300)
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp", "yt-dlp-ejs"], capture_output=True, text=True, timeout=300)
            logger.info("yt-dlp : mise à jour %s", "faite" if r.returncode == 0 else "impossible : " + (r.stderr or r.stdout)[-200:])
        except Exception as e:
            logger.error("yt-dlp : mise à jour en erreur : %s", e)
        time.sleep(toutes)


if __name__ == "__main__":
    travaux.demarrer()
    threading.Thread(target=_mise_a_jour_ytdlp, daemon=True, name="maj-ytdlp").start()
    redemarrage.verifier(BASE_DIR, "MouFlip", BASE_DIR / "data" / "mouflip.log", lambda t: None)
    app.run(debug=False, host="0.0.0.0", port=8004)
