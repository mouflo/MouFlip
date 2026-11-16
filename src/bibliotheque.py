"""📚 Ma bibliothèque : les groupes DÉJÀ rangés (dossiers « Video clip » et « Musique »), leur fiche d'images, et le remplacement des images.

Un même groupe a souvent deux dossiers aux noms différents (« Rainbow » chez les clips, « Rainbow (레인보우) » dans la musique) : on les réunit par
leur identifiant MusicBrainz (lu dans leur artist.nfo, ou retenu à la main), à défaut par leur nom sans parenthèses.

Règles : jamais d'adresse d'image libre (seulement celles PROPOSÉES pour ce groupe, ou ajoutées à la main) ; toute image remplacée est d'abord
copiée dans les sauvegardes ; une image n'est téléchargée et vérifiée (vraie image, taille raisonnable) AVANT que l'ancienne soit touchée ;
aucun dossier d'album n'est créé ; rien n'est supprimé."""
import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

from src import images_perso, noms, rangement

logger = logging.getLogger(__name__)

IGNORES = {"@eaDir", "#recycle", "lost+found", ".trash", "extrafanart"}
EXT_VIDEO = (".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v")
EXT_AUDIO = (".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav")
TYPES_GROUPE = ("vignette", "banniere", "logo")
FICHIERS_GROUPE = dict(rangement.IMAGES_GROUPE)                  # vignette → folder.jpg, banniere → banner.jpg, logo → logo.png, fanart → fanart.jpg…
SLOTS_FONDS = ("fanart", "fanart2", "fanart3", "fanart4")        # fanart.jpg puis extrafanart/fanart1..3.jpg
FICHIERS_ALBUM = {"couverture": "folder.jpg", "cdart": "cdart.png"}
CIBLES = ("clips", "musique")
MAX_OCTETS = 15 * 1024 * 1024

_MBID = re.compile(r"<musicBrainzArtistID>\s*([0-9a-f-]{36})\s*</musicBrainzArtistID>", re.I)
_CFG = {"racine_clips": None, "racine_musique": None, "dossier": None, "emby": None, "candidats": None, "couvertures": None, "telecharger": None}
_ID2CLE = {}


def configurer(dossier_donnees, racine_clips_fn, racine_musique_fn, emby, candidats_fn, couvertures_fn=None, telecharger_fn=None):
    """candidats_fn(mbid) -> contexte du groupe (celui de /api/groupe/<mbid>) ; couvertures_fn(rg, groupe, album) -> [{url, source}] ;
    telecharger_fn(url) -> octets (essai : remplacé)."""
    _CFG.update(dossier=Path(dossier_donnees), racine_clips=racine_clips_fn, racine_musique=racine_musique_fn, emby=emby, candidats=candidats_fn,
                couvertures=couvertures_fn, telecharger=telecharger_fn or _telecharger)


# ---------------------------------------------------------------- mémoire des identifications faites à la main
def _fichier_memo() -> Path:
    return Path(_CFG["dossier"]) / "bibliotheque.json"


def _memo() -> dict:
    try:
        return json.loads(_fichier_memo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def identifier(cle_nom: str, mbid: str):
    """Retient « ce dossier, c'est ce groupe MusicBrainz » (pour un groupe sans artist.nfo). cle_nom : nom normalisé du dossier."""
    if not re.fullmatch(r"[0-9a-f-]{36}", mbid or ""):
        raise ValueError("Identifiant de groupe invalide.")
    d = _memo()
    d[cle_nom] = mbid
    tmp = _fichier_memo().with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_fichier_memo())


# ---------------------------------------------------------------- les groupes
_BLOC = re.compile(r"<artist>(.*?)</artist>", re.S | re.I)
_NOM = re.compile(r"<name>\s*([^<]*?)\s*</name>", re.I)


def _mbid_nfo(dossier: Path) -> str:
    """L'identifiant MusicBrainz du groupe d'après son artist.nfo. Un ancien outil a parfois collé UNE AUTRE fiche devant la vraie (Marina Kaye / « Marina del
    Ray », Rainbow / « One-T + Cool-T », Lucky J / « Lucenzo ») : avec plusieurs fiches, on prend celle dont le nom est celui du dossier, sinon la première."""
    try:
        texte = (dossier / "artist.nfo").read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return ""
    voulu = noms.normaliser(dossier.name)
    trouves = []
    for bloc in _BLOC.findall(texte):
        m = _MBID.search(bloc)
        if m:
            nom = _NOM.search(bloc)
            trouves.append((noms.normaliser(nom.group(1)) if nom else "", m.group(1).lower()))
    if not trouves:
        m = _MBID.search(texte)
        return m.group(1).lower() if m else ""
    return next((mbid for nom, mbid in trouves if nom and nom == voulu), trouves[0][1])


def _sous_dossiers(racine) -> list:
    racine = Path(racine or "")
    try:
        return sorted((d for d in racine.iterdir() if d.is_dir() and d.name not in IGNORES and not d.name.startswith(".")), key=lambda d: d.name.lower())
    except OSError:
        return []


def _id(cle: str) -> str:
    return hashlib.sha1(cle.encode("utf-8")).hexdigest()[:12]


def lister() -> list:
    """Tous les groupes des deux dossiers, réunis par identifiant MusicBrainz (sinon par nom). Chaque : {id, cle, nom, mbid, clips, musique} (chemins ou None)."""
    memo = _memo()
    groupes = {}
    for source, racine in (("musique", _CFG["racine_musique"]()), ("clips", _CFG["racine_clips"]())):
        for d in _sous_dossiers(racine):
            nom_norm = noms.normaliser(d.name) or d.name.lower()
            mbid = _mbid_nfo(d) or memo.get(nom_norm, "")
            cle = mbid or "n:" + nom_norm
            g = groupes.setdefault(cle, {"cle": cle, "nom": d.name, "mbid": mbid, "clips": None, "musique": None, "nom_norm": nom_norm})
            if g[source] is None:
                g[source] = d
    # un dossier sans identifiant rejoint le groupe identifié de même nom, s'il n'y en a qu'un
    for cle in [c for c in groupes if c.startswith("n:")]:
        g = groupes[cle]
        pareils = [x for x in groupes.values() if x["mbid"] and x["nom_norm"] == g["nom_norm"]]
        if len(pareils) > 1:                                               # plusieurs groupes de même nom (LiSA, LISA (Blackpink)) : celui dont un dossier porte EXACTEMENT ce nom
            nom_dossier = (g["clips"] or g["musique"]).name.casefold()
            pareils = [x for x in pareils if any(x[s] and x[s].name.casefold() == nom_dossier for s in CIBLES)]
        if len(pareils) == 1 and not any(g[s] and pareils[0][s] for s in CIBLES):
            for s in CIBLES:
                pareils[0][s] = pareils[0][s] or g[s]
            del groupes[cle]
    out = sorted(groupes.values(), key=lambda g: g["nom"].lower())
    _ID2CLE.clear()
    for g in out:
        g["id"] = _id(g["cle"])
        _ID2CLE[g["id"]] = g["cle"]
    return out


def groupe(id_: str):
    for g in lister():
        if g["id"] == id_:
            return g
    return None


def _present(dossier, rel) -> bool:
    return bool(dossier) and (Path(dossier) / rel).is_file()


def resume(g: dict) -> dict:
    """Une ligne de la liste : nom, où il est, et si son poster existe (la liste le montre, pour repérer les mauvais d'un coup d'œil)."""
    return {"id": g["id"], "nom": g["nom"], "mbid": g["mbid"], "clips": bool(g["clips"]), "musique": bool(g["musique"]),
            "poster": {s: _present(g[s], "folder.jpg") for s in CIBLES}}


# ---------------------------------------------------------------- la fiche
def _fichiers(dossier: Path, extensions) -> list:
    try:
        return sorted(f.name for f in Path(dossier).iterdir() if f.is_file() and f.suffix.lower() in extensions and not f.name.startswith("."))
    except OSError:
        return []


def _albums(g: dict) -> list:
    """Les albums des deux dossiers, réunis par nom : leurs clips, leurs morceaux, leur pochette et leur cdart."""
    albums = {}
    for s in CIBLES:
        for d in _sous_dossiers(g[s]):
            a = albums.setdefault(noms.normaliser(d.name) or d.name.lower(), {"nom": d.name, "dossiers": {}})
            a["dossiers"][s] = d
    out = []
    for a in sorted(albums.values(), key=lambda a: a["nom"].lower()):
        d = a["dossiers"]
        out.append({"nom": a["nom"], "dossiers": {s: p.name for s, p in d.items()},
                    "clips": sorted({f for p in [d.get("clips")] if p for f in _fichiers(p, EXT_VIDEO)}),
                    "morceaux": sorted({f for p in [d.get("musique")] if p for f in _fichiers(p, EXT_AUDIO)}),
                    "couverture": {s: _present(d.get(s), "folder.jpg") for s in CIBLES},
                    "cdart": {s: _present(d.get(s), "cdart.png") for s in CIBLES}})
    return out


def _candidats_groupe(mbid: str):
    """(contexte du groupe ou None, avertissement)."""
    if not mbid:
        return None, "Ce groupe n'est pas encore identifié : choisis-le pour voir les images proposées."
    try:
        return _CFG["candidats"](mbid), ""
    except Exception as e:
        logger.info("Bibliothèque : images proposées indisponibles pour %s : %s", mbid, e)
        return None, f"Les images proposées sont indisponibles pour l'instant ({str(e)[:120]}). Tu peux quand même ajouter les tiennes."


def _perso(mbid: str, type_: str, album_norm: str = "") -> list:
    if not mbid:
        return []
    return [{"url": f"perso:{e['id']}", "source": "toi", "apercu": f"/api/perso/{mbid}/{e['id']}"} for e in images_perso.lister(mbid, type_, album_norm)]


def _rg_de(ctx, album: str) -> str:
    n = noms.normaliser(album)
    for c in (ctx or {}).get("clips", []):
        if noms.normaliser(c.get("album", "")) == n and c.get("rg"):
            return c["rg"]
    return ""


def _candidats_album(ctx, mbid, album: str, type_: str) -> list:
    base = []
    rg = _rg_de(ctx, album)
    if ctx and rg:
        liste = ((ctx.get("images_album") or {}).get(rg) or {}).get(type_ + "_liste", [])
        base = [{"url": u, "source": "fanart.tv"} for u in liste]
        if type_ == "couverture" and _CFG["couvertures"]:
            try:
                for x in _CFG["couvertures"](rg, ctx["fiche"]["nom"], album):
                    if x["url"] not in [b["url"] for b in base]:
                        base.append(x)
            except Exception:
                pass
    return base + _perso(mbid, type_, noms.normaliser(album))


def fiche(g: dict) -> dict:
    ctx, avert = _candidats_groupe(g["mbid"])
    cands = (ctx or {}).get("candidats") or {}
    images = {}
    for t in TYPES_GROUPE:
        rel = FICHIERS_GROUPE[t]
        images[t] = {"fichier": rel, "existant": {s: _present(g[s], rel) for s in CIBLES},
                     "candidats": list(cands.get(t) or []) + _perso(g["mbid"], t)}
    fonds = {"fichiers": [FICHIERS_GROUPE[c] for c in SLOTS_FONDS],
             "existants": {s: [_present(g[s], FICHIERS_GROUPE[c]) for c in SLOTS_FONDS] for s in CIBLES},
             "candidats": list(cands.get("fanart") or []) + _perso(g["mbid"], "fanart")}
    albums = _albums(g)
    for a in albums:
        a["candidats"] = {t: _candidats_album(ctx, g["mbid"], a["nom"], t) for t in FICHIERS_ALBUM}
    return {"id": g["id"], "nom": g["nom"], "mbid": g["mbid"], "cle_nom": g["nom_norm"],
            "dossiers": {s: (str(g[s]) if g[s] else None) for s in CIBLES}, "images": images, "fonds": fonds, "albums": albums,
            "groupe_connu": (ctx or {}).get("fiche", {}).get("nom", ""), "avertissement": avert}


# ---------------------------------------------------------------- servir une image du dossier (liste blanche)
def chemin_image(g: dict, cible: str, rel: str):
    """Le fichier d'une image DÉJÀ rangée, seulement parmi les noms connus : jamais un chemin libre."""
    if cible not in CIBLES or not g[cible]:
        return None
    autorises = set(FICHIERS_GROUPE.values())
    parts = rel.split("/")
    if rel not in autorises:
        if len(parts) != 2 or parts[1] not in FICHIERS_ALBUM.values() or parts[0] in IGNORES or parts[0].startswith(".") or parts[0] in ("..", "."):
            return None
        if not (Path(g[cible]) / parts[0]).is_dir():
            return None
    p = Path(g[cible]) / rel
    return p if p.is_file() else None


# ---------------------------------------------------------------- appliquer
def _telecharger(url: str) -> bytes:
    contenu, err = rangement._telecharger_image(url)
    if contenu is None:
        raise ValueError(err)
    return contenu


def _verifier(octets: bytes):
    """Une vraie image, pas trop lourde : sinon on ne touche à rien."""
    if not octets or len(octets) > MAX_OCTETS:
        raise ValueError("image vide ou trop lourde")
    try:
        from PIL import Image
        Image.open(io.BytesIO(octets)).verify()
    except Exception:
        raise ValueError("ce n'est pas une image lisible")


def _permises(g: dict, ctx) -> set:
    ok = set()
    c = (ctx or {}).get("candidats") or {}
    for t in (*TYPES_GROUPE, "fanart"):
        ok |= {x["url"] for x in c.get(t) or []}
        ok |= {x["url"] for x in _perso(g["mbid"], t)}
    for a in _albums(g):
        for t in FICHIERS_ALBUM:
            ok |= {x["url"] for x in _candidats_album(ctx, g["mbid"], a["nom"], t)}
    return ok


def _source(g: dict, url: str, permises: set, tmp: Path):
    """(chemin d'un fichier local contenant l'image vérifiée) pour une adresse permise, « perso:<id> » ou « copie:<cible>:<fichier> »."""
    if url.startswith("copie:"):
        _, cible, rel = url.split(":", 2)
        p = chemin_image(g, cible, rel)
        if not p:
            raise ValueError("image à copier introuvable")
        octets = p.read_bytes()
    elif url.startswith("perso:"):
        p = images_perso.chemin(g["mbid"], url[len("perso:"):]) if g["mbid"] else None
        if not p:
            raise ValueError("image ajoutée introuvable")
        octets = Path(p).read_bytes()
    elif url in permises:
        octets = _CFG["telecharger"](url)
    else:
        raise ValueError("cette image ne fait pas partie des propositions")
    _verifier(octets)
    f = tmp / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".img")
    f.write_bytes(octets)
    return f


def appliquer(g: dict, corps: dict) -> dict:
    """corps : {cibles: ["clips", "musique"], images: {vignette|banniere|logo: adresse}, fonds: [adresse|null ×4], albums: {nom: {couverture|cdart: adresse}}}.
    Ce qui est absent n'est pas touché. Renvoie {ok, faits, ignores, erreurs, emby}."""
    cibles = [c for c in (corps.get("cibles") or []) if c in CIBLES and g[c]]
    if not cibles:
        return {"ok": False, "error": "Choisis au moins un dossier à modifier (clips ou musique)."}
    ctx, _ = _candidats_groupe(g["mbid"])
    permises = _permises(g, ctx)
    albums = {a["nom"]: a for a in _albums(g)}
    tmp = Path(tempfile.mkdtemp(prefix="biblio-", dir=str(_CFG["dossier"]))) if _CFG["dossier"] else Path(tempfile.mkdtemp(prefix="biblio-"))
    lignes, erreurs = [], []
    try:
        def ajouter(url, chemin, etiquette):
            try:
                f = _source(g, str(url), permises, tmp)
            except (ValueError, OSError) as e:
                erreurs.append(f"{etiquette} : {e}")
                return
            lignes.append({"type": "image", "chemin": str(chemin), "source": str(f), "remplacer": True})

        for t, url in (corps.get("images") or {}).items():
            if t in TYPES_GROUPE and url:
                for s in cibles:
                    ajouter(url, Path(g[s]) / FICHIERS_GROUPE[t], f"{t} ({s})")
        fonds = list(corps.get("fonds") or [])[:len(SLOTS_FONDS)]
        for cle, url in zip(SLOTS_FONDS, fonds):
            if url:
                for s in cibles:
                    ajouter(url, Path(g[s]) / FICHIERS_GROUPE[cle], f"fond {FICHIERS_GROUPE[cle]} ({s})")
        for nom_album, choix in (corps.get("albums") or {}).items():
            a = albums.get(nom_album)
            if not a:
                erreurs.append(f"album « {nom_album} » introuvable")
                continue
            for t, url in (choix or {}).items():
                if t in FICHIERS_ALBUM and url:
                    for s in cibles:
                        dossier_album = a["dossiers"].get(s)
                        if dossier_album:                                   # jamais de dossier d'album créé
                            ajouter(url, Path(g[s]) / dossier_album / FICHIERS_ALBUM[t], f"{t} de « {nom_album} » ({s})")
        if not lignes and not erreurs:
            return {"ok": False, "error": "Aucune image choisie : rien n'a été modifié."}
        res = rangement.executer(lignes, Path(_CFG["dossier"]).parent / "sauvegardes") if lignes else {"faits": [], "ignores": [], "erreurs": []}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    emby = []
    if res["faits"] and _CFG["emby"]:
        for s in cibles:
            emby.append(rangement.prevenir_emby(_CFG["emby"], str(g[s])))
    erreurs += res["erreurs"]
    logger.info("[Bibliothèque] %s : %d image(s) posée(s), %d erreur(s)", g["nom"], len(res["faits"]), len(erreurs))
    return {"ok": not erreurs or bool(res["faits"]), "faits": res["faits"], "ignores": res["ignores"], "erreurs": erreurs, "emby": emby[0] if emby else ""}
