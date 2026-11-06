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

from src import existants, formats, noms, sources, ytcookies
from src.emby_client import EmbyClient
from src.netfix import repair_urllib3

BASE_VERSION = "0.1"
CONFIG = load_config()
EMBY = EmbyClient(CONFIG.get("emby", {}))
DOSSIER_CLIPS = (CONFIG.get("clips") or {}).get("dossier") or "/mnt/mouflosyno/Emby-Media/Video clip"


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
_CACHE_GROUPES = {}                # mbid -> (heure, données) : 10 min, pour ne pas redemander à MusicBrainz à chaque clic
OUTILS_ETAT = {}


def _roots():
    return [DOSSIER_CLIPS]


def _groupe_complet(mbid: str) -> dict:
    """Fiche + clips d'un groupe, avec l'état « déjà dans la médiathèque » et le chemin de rangement prévu. Rien n'est écrit."""
    en_cache = _CACHE_GROUPES.get(mbid)
    if en_cache and time.time() - en_cache[0] < 600:
        base = en_cache[1]
    else:
        erreurs = []
        fiche = sources.fiche_mb(mbid)
        clips = sources.clips_mb(mbid, fiche.get("nom", ""))
        audiodb, wiki = {}, ""
        try:
            audiodb = sources.images_audiodb(mbid)
        except sources.SourceIndisponible as e:
            erreurs.append(str(e))
        if not (audiodb.get("biographie")):
            try:
                wiki = sources.resume_wikipedia(fiche.get("nom", ""), "fr") or sources.resume_wikipedia(fiche.get("nom", ""), "en")
            except sources.SourceIndisponible as e:
                erreurs.append(str(e))
        base = {"fiche": fiche, "clips": clips, "audiodb": audiodb, "wikipedia": wiki, "erreurs": erreurs}
        _CACHE_GROUPES[mbid] = (time.time(), base)
    fiche = base["fiche"]
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
            "images": base["audiodb"].get("images", {}), "clips": clips, "erreurs": base["erreurs"], "racine": DOSSIER_CLIPS,
            "racine_ok": Path(DOSSIER_CLIPS).is_dir()}


@app.route("/")
def index():
    return render_template("index.html", version=APP_VERSION)


@app.route("/api/groupes/chercher")
def api_chercher():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"groupes": []})
    try:
        return jsonify({"groupes": sources.chercher_groupes(q)})
    except sources.SourceIndisponible as e:
        return jsonify({"error": f"MusicBrainz est injoignable : {e}. Réessaie dans un instant."}), 502


@app.route("/api/groupe/<mbid>")
def api_groupe(mbid):
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid):
        return jsonify({"error": "Identifiant de groupe invalide."}), 400
    try:
        return jsonify(_groupe_complet(mbid))
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


_APERCUS = {}                      # (groupe, titre) -> (heure, candidats) : 30 min


@app.route("/api/apercu", methods=["POST"])
def api_apercu():
    """Les vidéos YouTube possibles pour un clip (la plus probable d'abord), pour les REGARDER et choisir. Lecture seule."""
    b = request.get_json(silent=True) or {}
    groupe, titre = str(b.get("groupe", "")).strip(), str(b.get("titre", "")).strip()
    if not groupe or not titre:
        return jsonify({"error": "Clip inconnu."}), 400
    cle = (groupe, titre)
    ancien = _APERCUS.get(cle)
    if ancien and time.time() - ancien[0] < 1800:
        return jsonify({"candidats": ancien[1]})
    try:
        c = formats.candidats(groupe, titre)
    except Exception as e:
        logger.warning("Aperçu impossible pour « %s - %s » : %s", groupe, titre, e)
        return jsonify({"error": f"YouTube ne répond pas : {str(e)[:160]}"}), 502
    _APERCUS[cle] = (time.time(), c)
    return jsonify({"candidats": c})


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
                    "ffmpeg": bool(shutil.which("ffmpeg")), "cookies": ytcookies.state(), "racine": DOSSIER_CLIPS, "racine_ok": Path(DOSSIER_CLIPS).is_dir(),
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
redemarrer.init_app(app, lambda: ["lecture des formats YouTube"] if any(not j["fini"] for j in FORMATS.values()) else [])
diag.init_app(app, APP_VERSION, _roots, EMBY.describe, lambda: "aucun traitement en lot (lecture seule)")

if __name__ == "__main__":
    redemarrage.verifier(BASE_DIR, "MouFlip", BASE_DIR / "data" / "mouflip.log", lambda t: None)
    app.run(debug=False, host="0.0.0.0", port=8004)
