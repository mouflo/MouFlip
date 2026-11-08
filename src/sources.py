"""Fiche d'un groupe et liste de ses clips : MusicBrainz (identité, albums, vidéos), TheAudioDB (images, biographie), Wikipédia (secours).
Lecture seule, aucun téléchargement de vidéo. MusicBrainz : 1 requête par seconde au plus, nom d'application honnête (jamais d'adresse perso).
Une panne d'une source n'est jamais « rien trouvé » : elle est dite (champ « erreurs »)."""
import os
import re
import threading
import time
from urllib.parse import quote, unquote

import requests

from src.noms import normaliser

UA = "MouFlip/0.1 (appli personnelle de rangement de clips)"
MB = "https://musicbrainz.org/ws/2"
_verrou, _dernier = threading.Lock(), [0.0]

# Vidéos qui ne sont pas des clips : coulisses, concerts, bandes-annonces, intermèdes
# Variantes sans intérêt : remix, versions, chorégraphies, « guide de caméra »…
_VARIANTE = re.compile(r"\b(remix(es)?|rmx|ver\.?|version|remake|track\s*video|choreograph\w*|camerawork|guide|instrumental|acoustic|"
                       r"sped\s*up|slowed|mashup|edit|up\s*next|performance\s*video|dance\s*break)\b", re.I)
_PAS_CLIP = re.compile(r"\b(behind|making|trailer|teaser|interview|multi\s*angles?|bridge\s*vcr|vcr|mc|ment|movie|jacket|shooting|"
                       r"live\s*(clip|performance|at|from)|concert|dance\s*practice|lyric|reaction|special\s*video|film|showcase|a-nation|festival|tour|awards?|premium)\b", re.I)


class SourceIndisponible(Exception):
    pass


def _get(url, params=None, max_essais=5):
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
            derniere = f"serveur occupé (code {r.status_code})"
            try:
                attente = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                attente = 0
            time.sleep(min(30, max(attente, 3 * (2 ** essai))))             # 3, 6, 12, 24 s : MusicBrainz est parfois surchargé quelques minutes
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


RE_MBID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def mbid_dans(texte: str) -> str:
    """L'identifiant MusicBrainz contenu dans un texte (adresse de la fiche https://musicbrainz.org/artist/… ou identifiant seul), sinon ''."""
    m = RE_MBID.search(texte or "")
    return m.group(1).lower() if m else ""


def _lucene(texte: str) -> str:
    """Le texte cherché, sans que « (G)I-DLE » ou « AC/DC » soient pris pour des opérateurs de recherche."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", texte)


def _resume_artiste(a: dict) -> dict:
    return {"mbid": a["id"], "nom": a.get("name", ""), "type": a.get("type", ""), "pays": a.get("country", "") or (a.get("area") or {}).get("name", ""),
            "precision": a.get("disambiguation", ""), "score": a.get("score", 100), "debut": ((a.get("life-span") or {}).get("begin") or "")[:4]}


def artiste_par_mbid(mbid: str) -> dict | None:
    """Un artiste d'après l'identifiant de sa fiche MusicBrainz (adresse collée dans la recherche). None si cette fiche n'existe pas."""
    d = _get(f"{MB}/artist/{mbid}", {"fmt": "json"})
    return _resume_artiste(d) if d and d.get("id") else None


def chercher_groupes(nom: str, limite: int = 30) -> list[dict]:
    """Les artistes dont le nom est celui qu'on cherche d'abord (même écriture ou même alias, puis nom qui commence ainsi), puis les autres par pertinence.
    Avant, la recherche renvoyait « tous les artistes dont le nom contient ALI » (Nusrat Fateh Ali Khan, Brother Ali…) et le groupe ALI n'apparaissait pas."""
    d = _get(f"{MB}/artist", {"query": _lucene(nom), "fmt": "json", "limit": 40}) or {}
    voulu = normaliser(nom) or nom.lower()
    resultats = []
    for a in d.get("artists", []):
        noms = [a.get("name", "")] + [x.get("name", "") for x in a.get("aliases", []) or []]
        normes = [normaliser(x) or x.lower() for x in noms if x]
        rang = 0 if voulu in normes else 1 if any(n.startswith(voulu + " ") or n.startswith(voulu) for n in normes[:1]) else 2
        resultats.append((rang, -(a.get("score") or 0), _resume_artiste(a)))
    resultats.sort(key=lambda x: (x[0], x[1]))
    return [r[2] for r in resultats[:limite]]


def fiche_mb(mbid: str) -> dict:
    d = _get(f"{MB}/artist/{mbid}", {"inc": "aliases+tags+url-rels+artist-rels", "fmt": "json"}) or {}
    liens, youtube, parents = {}, [], []
    for r in d.get("relations", []):
        u = (r.get("url") or {}).get("resource", "")
        if r.get("type") == "member of band" and r.get("direction") == "forward" and not r.get("ended") and (r.get("artist") or {}).get("id"):
            parents.append({"id": r["artist"]["id"], "nom": r["artist"].get("name", "")})      # le groupe dont cet artiste est membre (YUQI → i-dle)
        if r.get("type") == "youtube" and u:
            youtube.append(u)                                        # chaîne(s) YouTube officielle(s) connue(s) de MusicBrainz
        if r.get("type") == "wikidata":
            liens["wikidata"] = u
        elif r.get("type") == "official homepage":
            liens["site"] = u
        elif r.get("type") == "social network" and "wikipedia" in u:
            liens["wikipedia"] = u
    return {"mbid": d.get("id", mbid), "nom": d.get("name", ""), "type": d.get("type", ""), "pays": d.get("country", ""),
            "debut": ((d.get("life-span") or {}).get("begin") or "")[:4], "alias": [a["name"] for a in d.get("aliases", []) if a.get("name")],
            "genres": [t["name"] for t in sorted(d.get("tags", []), key=lambda t: -t.get("count", 0))][:5], "liens": liens, "youtube": youtube, "parents": parents[:3]}


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


def images_fanart(mbid: str) -> dict:
    """Images soignées de fanart.tv (API officielle, clé personnelle gratuite FANART_API_KEY ; sans clé : {} et cle=False).
    Retourne {"cle": bool, "images": {vignette, banniere, logo, fanart, fanart2.. : la préférée}, "listes": {type: [toutes les images, la plus aimée d'abord]},
    "albums": {id du groupe de sorties: {cdart, couverture, cdart_liste, couverture_liste}}}."""
    cle = os.getenv("FANART_API_KEY", "").strip()
    vide = {"cle": bool(cle), "images": {}, "listes": {}, "albums": {}}
    if not cle:
        return vide
    d = _get(f"https://webservice.fanart.tv/v3/music/{mbid}", {"api_key": cle})
    if not d:
        return vide

    def tri(lst):
        return [x["url"] for x in sorted(lst or [], key=lambda x: -int(x.get("likes") or 0)) if x.get("url")]

    listes = {"vignette": tri(d.get("artistthumb")), "banniere": tri(d.get("musicbanner")),
              "logo": tri(d.get("hdmusiclogo")) + [u for u in tri(d.get("musiclogo")) if u not in tri(d.get("hdmusiclogo"))],
              "fanart": tri(d.get("artistbackground"))[:12]}
    images = {}
    for nom in ("vignette", "banniere", "logo"):
        if listes[nom]:
            images[nom] = listes[nom][0]
    for i, u in enumerate(listes["fanart"][:4]):
        images["fanart" if i == 0 else f"fanart{i + 1}"] = u
    albums = {}
    for rg, a in (d.get("albums") or {}).items():
        cd, cv = tri(a.get("cdart")), tri(a.get("albumcover"))
        if cd or cv:
            albums[rg] = {**{k: v[0] for k, v in (("cdart", cd), ("couverture", cv)) if v}, "cdart_liste": cd, "couverture_liste": cv}
    return {"cle": True, "images": images, "listes": listes, "albums": albums}


def candidats_images(audiodb: dict, fanart: dict) -> dict:
    """Toutes les images possibles par type, avec leur source : [{url, source}] — fanart.tv d'abord, puis TheAudioDB, sans doublon."""
    sortie = {}
    a = (audiodb or {}).get("images", {})
    cles_audiodb = {"vignette": ("vignette",), "banniere": ("banniere",), "logo": ("logo",), "fanart": ("fanart", "fanart2", "fanart3", "fanart4")}
    for t in ("vignette", "banniere", "logo", "fanart"):
        vus, lst = set(), []
        for u in (fanart or {}).get("listes", {}).get(t, []):
            if u not in vus:
                vus.add(u)
                lst.append({"url": u, "source": "fanart.tv"})
        for k in cles_audiodb[t]:
            u = a.get(k)
            if u and u not in vus:
                vus.add(u)
                lst.append({"url": u, "source": "TheAudioDB"})
        sortie[t] = lst
    return sortie


def couvertures_externes(rg: str, groupe: str, album: str) -> list[dict]:
    """Pochettes de l'album trouvées ailleurs que chez fanart.tv : [{url, source}].
    - Cover Art Archive (la banque d'images de MusicBrainz, par groupe de sorties) : images « Front » en grand format ;
    - iTunes (recherche par artiste et album, l'album doit porter le même nom) : image en 1200 px.
    Une panne d'une source est ignorée : ce sont des propositions en plus, jamais une obligation."""
    sortie = []
    if rg:
        try:
            d = _get(f"https://coverartarchive.org/release-group/{rg}") or {}
            for im in d.get("images", []):
                if im.get("front") or "Front" in (im.get("types") or []):
                    url = (im.get("thumbnails") or {}).get("1200") or im.get("image")
                    if url:
                        sortie.append({"url": url.replace("http://", "https://", 1), "source": "Cover Art Archive"})
        except SourceIndisponible:
            pass
    try:
        d = _get("https://itunes.apple.com/search", {"term": f"{groupe} {album}", "entity": "album", "limit": 10}) or {}
        voulu, artiste = normaliser(album), normaliser(groupe)
        for r in d.get("results", []):
            nom_album = normaliser(r.get("collectionName", ""))
            if not (nom_album == voulu or nom_album.startswith(voulu + " ")):
                continue
            if artiste and artiste not in normaliser(r.get("artistName", "")) and normaliser(r.get("artistName", "")) not in artiste:
                continue
            url = (r.get("artworkUrl100") or "").replace("100x100bb", "1200x1200bb")
            if url and url not in [x["url"] for x in sortie]:
                sortie.append({"url": url, "source": "iTunes"})
    except SourceIndisponible:
        pass
    return sortie


def resume_wikipedia(titre: str, langue: str = "fr") -> str:
    d = _get(f"https://{langue}.wikipedia.org/api/rest_v1/page/summary/{quote(titre.replace(' ', '_'))}")
    return ((d or {}).get("extract") or "") if (d or {}).get("type") == "standard" else ""


def _titre_wikipedia(url: str) -> tuple[str, str]:
    """« https://fr.wikipedia.org/wiki/Aespa_(groupe) » → (« fr », « Aespa_(groupe) »)."""
    m = re.match(r"https?://([a-z\-]+)\.wikipedia\.org/wiki/([^?#]+)", url or "")
    return (m.group(1), unquote(m.group(2))) if m else ("", "")


def pages_wikipedia(fiche: dict) -> dict:
    """{langue: titre de page} pour le groupe, d'après le lien exact de MusicBrainz (Wikidata donne les pages fr et en d'un même sujet) ;
    jamais d'après le nom seul si un lien exact existe."""
    pages = {}
    liens = fiche.get("liens") or {}
    langue, titre = _titre_wikipedia(liens.get("wikipedia", ""))
    if langue and titre:
        pages[langue] = titre
    q = re.search(r"/(Q\d+)", liens.get("wikidata", ""))
    if q:
        d = _get(f"https://www.wikidata.org/wiki/Special:EntityData/{q.group(1)}.json")
        liens_wiki = (((d or {}).get("entities") or {}).get(q.group(1)) or {}).get("sitelinks") or {}
        for lg in ("fr", "en"):
            t = (liens_wiki.get(f"{lg}wiki") or {}).get("title")
            if t:
                pages[lg] = t.replace(" ", "_")
    return pages


def biographie_wikipedia(fiche: dict) -> dict:
    """{fr: texte, en: texte} : le résumé de la page Wikipédia du groupe (interface officielle de Wikipédia), dans chaque langue trouvée.
    Sans lien exact connu, repli sur le nom du groupe (page « standard » seulement : une page d'homonymie est refusée)."""
    pages, sortie = pages_wikipedia(fiche), {}
    for lg in ("fr", "en"):
        titre = pages.get(lg) or ("" if pages else fiche.get("nom", ""))
        if titre:
            t = resume_wikipedia(titre, lg)
            if t:
                sortie[lg] = t
    return sortie


def choisir_biographie(audiodb: dict, wiki: dict) -> tuple[str, str, str]:
    """(texte, langue, source) : français d'abord (TheAudioDB puis Wikipédia), l'anglais seulement s'il n'y a pas de français."""
    ad = (audiodb or {}).get("biographie") or ""
    lad = (audiodb or {}).get("biographie_langue") or ""
    wiki = wiki or {}
    if ad and lad == "fr":
        return ad, "fr", "TheAudioDB"
    if wiki.get("fr"):
        return wiki["fr"], "fr", "Wikipédia"
    if ad:
        return ad, lad or "en", "TheAudioDB"
    if wiki.get("en"):
        return wiki["en"], "en", "Wikipédia"
    return "", "", ""


def _album_choisi(releases: list[dict], titre: str) -> tuple[str, str, str]:
    """(album, date) : le groupe de sorties « officiel » le plus ancien (Album, EP ou Single sans Live/Compilation/Remix…) ;
    à défaut le titre du clip lui-même (un single range son clip dans un dossier à son nom, comme tes clips actuels)."""
    bons = []
    for r in releases:
        rg = r.get("release-group") or {}
        if rg.get("primary-type") in ("Album", "EP", "Single") and not rg.get("secondary-types"):
            bons.append((r.get("date") or "9999", rg.get("title") or r.get("title") or "", r.get("date") or "", rg.get("id") or ""))
    if bons:
        bons.sort()
        return bons[0][1], bons[0][2], bons[0][3]
    return titre, "", ""


def _sorties(mbid: str) -> list[dict]:
    """Les groupes de sorties du groupe (Single / EP / Album) avec leur date, sans les remix, lives, compilations et bandes originales."""
    sortie, offset = [], 0
    while offset < 300:
        d = _get(f"{MB}/release-group", {"artist": mbid, "type": "single|ep|album", "fmt": "json", "limit": 100, "offset": offset}) or {}
        lot = d.get("release-groups", [])
        for g in lot:
            if g.get("secondary-types"):
                continue
            sortie.append({"id": g["id"], "titre": g.get("title", ""), "type": g.get("primary-type", ""), "date": g.get("first-release-date") or ""})
        offset += 100
        if offset >= d.get("count", 0) or not lot:
            break
    return sortie


def _pistes_des_albums(mbid: str) -> dict:
    """{titre normalisé: (album, date)} : dans quel album ou EP (le plus ancien) se trouve chaque morceau. Une seule série de requêtes."""
    out, offset = {}, 0
    while offset < 400:
        d = _get(f"{MB}/release", {"artist": mbid, "type": "album|ep", "status": "official", "inc": "recordings+release-groups", "fmt": "json",
                                   "limit": 100, "offset": offset}) or {}
        lot = d.get("releases", [])
        for r in lot:
            rg = r.get("release-group") or {}
            if rg.get("secondary-types") or rg.get("primary-type") not in ("Album", "EP"):
                continue
            date = rg.get("first-release-date") or r.get("date") or ""
            for m in r.get("media", []):
                for t in m.get("tracks", []):
                    n = normaliser(t.get("title", ""))
                    if n and (n not in out or (date or "9999") < (out[n][1] or "9999")):
                        out[n] = (rg.get("title") or r.get("title") or "", date, rg.get("id") or "")
        offset += 100
        if offset >= d.get("release-count", 0) or not lot:
            break
    return out


def clips_mb(mbid: str, nom_groupe: str, decouverte=None) -> list[dict]:
    """Les clips candidats : (1) les vidéos que MusicBrainz connaît, (2) les singles et les titres-phares d'EP / d'album. Les remix, versions,
    chorégraphies, concerts et coulisses sont écartés. « source » dit d'où vient la ligne : « video » (MusicBrainz connaît cette vidéo)
    ou « sortie » (un single ou titre-phare : la vidéo officielle est à vérifier sur YouTube)."""
    erreurs_pistes = None
    try:
        pistes = _pistes_des_albums(mbid)
    except SourceIndisponible as e:
        pistes, erreurs_pistes = {}, str(e)
    sorties = _sorties(mbid)
    singles = {normaliser(g["titre"]): g["date"] for g in sorties if g["type"] == "Single" and g["date"]}
    candidats = {}

    def _jours(a: str, b: str):
        """Jours entre deux dates MusicBrainz (AAAA, AAAA-MM ou AAAA-MM-JJ), None si une date manque."""
        from datetime import date
        def d(x):
            try:
                p = (x + "-01-01").split("-")[:3] if len(x) == 4 else (x + "-01").split("-")[:3] if len(x) == 7 else x.split("-")[:3]
                return date(int(p[0]), int(p[1]), int(p[2]))
            except (ValueError, IndexError):
                return None
        x, y = d(a), d(b)
        return (y - x).days if x and y else None

    def ajouter(titre, album, date, source, mb_id="", duree=0, rg="", video_id=""):
        n = normaliser(titre) or titre
        if _VARIANTE.search(titre) or _PAS_CLIP.search(titre):
            return
        album_pistes = pistes.get(n)
        if album_pistes:
            # un single sorti bien avant l'album garde son propre dossier (« Black Mamba/Black Mamba ») ;
            # un single de pré-sortie (moins de 100 jours avant l'album) va dans l'album (« Armageddon/Supernova »)
            ecart = _jours(singles[n], album_pistes[1]) if n in singles else None
            if n not in singles or (ecart is not None and 0 <= ecart <= 100):
                album, date, rg = album_pistes[0], (date or album_pistes[1]), album_pistes[2]
        if not date and n in singles:
            date = singles[n]                                    # année du single quand le clip vient de YouTube sans date
        annee = (date or "")[:4]
        if n in candidats:
            c = candidats[n]
            if video_id and not c.get("video_id"):
                c["video_id"] = video_id
            if source == "video":
                c["source"] = "video"
            if annee and (not c["annee"] or annee < c["annee"]):
                c["annee"] = annee
            return
        candidats[n] = {"titre": titre.strip(), "album": album or titre.strip(), "annee": annee, "mbid": mb_id, "type": "clip", "source": source, "duree": duree, "rg": rg,
                         "video_id": video_id, "date": date or ""}

    # (1) vidéos répertoriées par MusicBrainz
    offset = 0
    while offset < 400:
        d = _get(f"{MB}/recording", {"query": f"arid:{mbid} AND video:true", "fmt": "json", "limit": 100, "offset": offset}) or {}
        lot = d.get("recordings", [])
        for r in lot:
            titre = (r.get("title") or "").strip()
            rels = r.get("releases", []) or []
            live_seulement = bool(rels) and all("Live" in ((x.get("release-group") or {}).get("secondary-types") or []) for x in rels)
            if not titre or live_seulement:
                continue
            album, date, rg = _album_choisi(rels, titre)
            ajouter(titre, album, r.get("first-release-date") or date, "video", r.get("id", ""), round((r.get("length") or 0) / 1000), rg)
        offset += 100
        if offset >= d.get("count", 0) or not lot:
            break
    # (2) singles et titres-phares
    for g in sorties:
        if g["type"] == "Single" or normaliser(g["titre"]) in pistes:
            ajouter(g["titre"], g["titre"] if g["type"] == "Single" else "", g["date"], "sortie", rg=g["id"])
    # (3) clips trouvés sur YouTube dont le titre est un morceau d'un album (MusicBrainz les ignore souvent : titres-phares d'EP)
    if decouverte:
        try:
            for d in decouverte(set(pistes)):
                ajouter(d["titre"], "", d.get("date", ""), "youtube", "", d.get("duree", 0), "", d["video_id"])
        except Exception:
            pass
    sortie = sorted(candidats.values(), key=lambda c: (c["annee"] or "9999", normaliser(c["titre"])))
    if erreurs_pistes:
        for c in sortie:
            c["avertissement"] = "albums non lus : " + erreurs_pistes
    return sortie


_PISTES = {}


def piste_dans_album(rg: str, titre: str) -> tuple[int, int] | None:
    """(numéro de la piste, nombre de pistes) du morceau dans la première sortie officielle de ce groupe de sorties MusicBrainz."""
    if rg not in _PISTES:
        d = _get(f"{MB}/release-group/{rg}", {"inc": "releases", "fmt": "json"}) or {}
        sorties = [r for r in d.get("releases", []) if r.get("status") == "Official"] or d.get("releases", [])
        pistes = []
        if sorties:
            sorties.sort(key=lambda r: r.get("date") or "9999")
            r = _get(f"{MB}/release/{sorties[0]['id']}", {"inc": "recordings", "fmt": "json"}) or {}
            for m in r.get("media", []):
                pistes += [t.get("title", "") for t in m.get("tracks", [])]
        _PISTES[rg] = pistes
    pistes = _PISTES[rg]
    cle = normaliser(titre)
    for i, t in enumerate(pistes, 1):
        if normaliser(t) == cle:
            return i, len(pistes)
    return None
