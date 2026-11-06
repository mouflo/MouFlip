"""Fiche d'un groupe et liste de ses clips : MusicBrainz (identité, albums, vidéos), TheAudioDB (images, biographie), Wikipédia (secours).
Lecture seule, aucun téléchargement de vidéo. MusicBrainz : 1 requête par seconde au plus, nom d'application honnête (jamais d'adresse perso).
Une panne d'une source n'est jamais « rien trouvé » : elle est dite (champ « erreurs »)."""
import os
import re
import threading
import time
from urllib.parse import quote

import requests

from src.noms import normaliser

UA = "MouFlip/0.1 (appli personnelle de rangement de clips)"
MB = "https://musicbrainz.org/ws/2"
_verrou, _dernier = threading.Lock(), [0.0]

# Vidéos qui ne sont pas des clips : coulisses, concerts, bandes-annonces, intermèdes
_PAS_CLIP = re.compile(r"\b(behind|making|trailer|teaser|interview|multi\s*angles?|bridge\s*vcr|vcr|mc|ment|movie|jacket|shooting|"
                       r"live\s*(clip|performance|at|from)|concert|dance\s*practice|lyric|reaction|special\s*video|film|showcase|a-nation|festival|tour|awards?|premium)\b", re.I)


class SourceIndisponible(Exception):
    pass


def _get(url, params=None, max_essais=3):
    """GET JSON poli (MusicBrainz : une requête par seconde). Lève SourceIndisponible avec un message clair."""
    derniere = None
    for essai in range(max_essais):
        if "musicbrainz.org" in url:
            with _verrou:
                attente = 1.05 - (time.time() - _dernier[0])
                if attente > 0:
                    time.sleep(attente)
                _dernier[0] = time.time()
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
        except requests.RequestException as e:
            derniere = f"réseau : {e}"
            time.sleep(1.5)
            continue
        if r.status_code in (429, 503):
            derniere = f"trop de demandes (code {r.status_code})"
            time.sleep(2 + essai * 2)
            continue
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise SourceIndisponible(f"{url.split('/')[2]} a répondu {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise SourceIndisponible(f"{url.split('/')[2]} a renvoyé une réponse illisible")
    raise SourceIndisponible(f"{url.split('/')[2]} ne répond pas ({derniere})")


def chercher_groupes(nom: str, limite: int = 6) -> list[dict]:
    d = _get(f"{MB}/artist", {"query": f'artist:"{nom}" OR alias:"{nom}"', "fmt": "json", "limit": limite}) or {}
    sortie = []
    for a in d.get("artists", []):
        sortie.append({"mbid": a["id"], "nom": a.get("name", ""), "type": a.get("type", ""), "pays": a.get("country", "") or (a.get("area") or {}).get("name", ""),
                       "precision": a.get("disambiguation", ""), "score": a.get("score", 0),
                       "debut": ((a.get("life-span") or {}).get("begin") or "")[:4]})
    return sortie


def fiche_mb(mbid: str) -> dict:
    d = _get(f"{MB}/artist/{mbid}", {"inc": "aliases+tags+url-rels", "fmt": "json"}) or {}
    liens = {}
    for r in d.get("relations", []):
        u = (r.get("url") or {}).get("resource", "")
        if r.get("type") == "wikidata":
            liens["wikidata"] = u
        elif r.get("type") == "official homepage":
            liens["site"] = u
        elif r.get("type") == "social network" and "wikipedia" in u:
            liens["wikipedia"] = u
    return {"mbid": d.get("id", mbid), "nom": d.get("name", ""), "type": d.get("type", ""), "pays": d.get("country", ""),
            "debut": ((d.get("life-span") or {}).get("begin") or "")[:4], "alias": [a["name"] for a in d.get("aliases", []) if a.get("name")],
            "genres": [t["name"] for t in sorted(d.get("tags", []), key=lambda t: -t.get("count", 0))][:5], "liens": liens}


def images_audiodb(mbid: str) -> dict:
    """Images et biographie chez TheAudioDB (clé publique de test « 123 » si aucune clé n'est réglée)."""
    cle = os.getenv("THEAUDIODB_KEY", "").strip() or "123"
    d = _get(f"https://www.theaudiodb.com/api/v1/json/{cle}/artist-mb.php", {"i": mbid})
    a = ((d or {}).get("artists") or [None])[0]
    if not a:
        return {}
    bio = a.get("strBiographyFR") or a.get("strBiographyEN") or ""
    return {"biographie": bio, "biographie_langue": "fr" if a.get("strBiographyFR") else ("en" if bio else ""),
            "genre": a.get("strGenre") or "", "pays": a.get("strCountry") or "", "fonde": a.get("intFormedYear") or "",
            "images": {k: a.get(v) for k, v in (("vignette", "strArtistThumb"), ("logo", "strArtistLogo"), ("banniere", "strArtistBanner"),
                                                ("fanart", "strArtistFanart"), ("fanart2", "strArtistFanart2"), ("fanart3", "strArtistFanart3"),
                                                ("fanart4", "strArtistFanart4")) if a.get(v)}}


def resume_wikipedia(titre: str, langue: str = "fr") -> str:
    d = _get(f"https://{langue}.wikipedia.org/api/rest_v1/page/summary/{quote(titre.replace(' ', '_'))}")
    return ((d or {}).get("extract") or "") if (d or {}).get("type") == "standard" else ""


def _album_choisi(releases: list[dict], titre: str) -> tuple[str, str]:
    """(album, date) : le groupe de sorties « officiel » le plus ancien (Album, EP ou Single sans Live/Compilation/Remix…) ;
    à défaut le titre du clip lui-même (un single range son clip dans un dossier à son nom, comme tes clips actuels)."""
    bons = []
    for r in releases:
        rg = r.get("release-group") or {}
        if rg.get("primary-type") in ("Album", "EP", "Single") and not rg.get("secondary-types"):
            bons.append((r.get("date") or "9999", rg.get("title") or r.get("title") or "", r.get("date") or ""))
    if bons:
        bons.sort()
        return bons[0][1], bons[0][2]
    return titre, ""


def clips_mb(mbid: str, nom_groupe: str) -> list[dict]:
    """Les enregistrements marqués « vidéo » par MusicBrainz, dédoublonnés. « clip » = vrai clip probable ; « autre » = concert, coulisses, intermède…"""
    bruts, offset = [], 0
    while offset < 400:
        d = _get(f"{MB}/recording", {"query": f"arid:{mbid} AND video:true", "fmt": "json", "limit": 100, "offset": offset}) or {}
        lot = d.get("recordings", [])
        bruts += lot
        offset += 100
        if offset >= d.get("count", 0) or not lot:
            break
    vus, sortie = {}, []
    for r in bruts:
        titre = (r.get("title") or "").strip()
        if not titre:
            continue
        sorties = r.get("releases", []) or []
        live_seulement = bool(sorties) and all("Live" in ((x.get("release-group") or {}).get("secondary-types") or []) for x in sorties)
        album, date_album = _album_choisi(sorties, titre)
        clip = not (_PAS_CLIP.search(titre) or live_seulement)
        cle = normaliser(titre) or titre                 # un même titre dans plusieurs albums (rééditions, versions JP) = un seul clip, rangé dans sa plus ancienne sortie
        ligne = {"titre": titre, "album": album, "annee": ((r.get("first-release-date") or date_album or "")[:4]), "mbid": r.get("id", ""),
                 "type": "clip" if clip else "autre", "duree": round((r.get("length") or 0) / 1000)}
        if cle in vus:
            if ligne["annee"] and (not vus[cle]["annee"] or ligne["annee"] < vus[cle]["annee"]):
                vus[cle].update(annee=ligne["annee"])
            continue
        vus[cle] = ligne
        sortie.append(ligne)
    sortie.sort(key=lambda c: (c["type"] != "clip", c["annee"] or "9999", normaliser(c["titre"])))
    return sortie
