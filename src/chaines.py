"""Chaînes YouTube officielles d'un groupe (la sienne, celle de son label…) : une vidéo qui en vient est reconnue « officielle », et la liste de leurs vidéos
sert à trouver des clips que les bases ne connaissent pas. MusicBrainz les connaît souvent (relation « youtube ») ; l'utilisatrice peut en ajouter."""
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
RE_ID = re.compile(r"youtube\.com/channel/(UC[\w-]{22})")
ID_VALIDE = re.compile(r"UC[\w-]{22}")
HOTES = {"youtube.com", "www.youtube.com", "m.youtube.com"}


class ChaineError(Exception):
    pass


def id_depuis_url(url: str) -> str:
    """L'identifiant « UC… » quand l'adresse le contient (https://www.youtube.com/channel/UC…), sinon ''."""
    m = RE_ID.search(url or "")
    return m.group(1) if m else ""


def resoudre(url: str, extraire=None) -> dict:
    """{id, nom, url} d'après une adresse de chaîne : /channel/UC…, /@nom, /c/nom ou /user/nom. Seules les adresses youtube.com sont lues.
    Lève ChaineError (en français) si l'adresse n'est pas une chaîne YouTube lisible."""
    url = (url or "").strip()
    hote = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower()
    if hote not in HOTES:
        raise ChaineError("Colle l'adresse d'une chaîne YouTube (https://www.youtube.com/channel/… ou https://www.youtube.com/@nom).")
    if "//" not in url:
        url = "https://" + url
    if "/watch" in url or "/playlist" in url or "youtu.be" in url:
        raise ChaineError("C'est l'adresse d'une vidéo, pas d'une chaîne : ouvre la chaîne (clique sur son nom) et colle son adresse.")
    from yt_dlp import YoutubeDL
    from src import ytcookies
    extraire = extraire or (lambda u, o: YoutubeDL(o).extract_info(u, download=False))
    o = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "playlistend": 1, "socket_timeout": 25}
    o.update(ytcookies.opts())
    try:
        i = extraire(url, o) or {}
    except Exception as e:
        raise ChaineError(f"YouTube ne donne pas cette chaîne : {str(e)[:140]}")
    cid = i.get("channel_id") or id_depuis_url(url)
    if not cid or not ID_VALIDE.fullmatch(cid):
        raise ChaineError("Cette adresse n'est pas celle d'une chaîne YouTube.")
    return {"id": cid, "nom": i.get("channel") or i.get("uploader") or i.get("title") or cid, "url": f"https://www.youtube.com/channel/{cid}"}


RE_PLAYLIST = re.compile(r"[?&]list=((?:PL|UU|OLAK5uy_)[\w-]{10,64})")
ID_PLAYLIST = re.compile(r"(?:PL|UU|OLAK5uy_)[\w-]{10,64}")


def resoudre_source(url: str, extraire=None) -> dict:
    """Une chaîne OU une playlist YouTube : {type: « chaine » | « playlist », id, nom, url, channel_id}. Une playlist (par exemple « Music Videos » d'une
    chaîne officielle) est la liste exacte des clips : bien plus fiable qu'une recherche. Les « mix » automatiques (list=RD…) ne sont pas des playlists."""
    url = (url or "").strip()
    m = RE_PLAYLIST.search(url)
    if m and "/playlist" in url:
        from yt_dlp import YoutubeDL
        from src import ytcookies
        extraire = extraire or (lambda u, o: YoutubeDL(o).extract_info(u, download=False))
        o = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "playlistend": 1, "socket_timeout": 25}
        o.update(ytcookies.opts())
        try:
            i = extraire(f"https://www.youtube.com/playlist?list={m.group(1)}", o) or {}
        except Exception as e:
            raise ChaineError(f"YouTube ne donne pas cette playlist : {str(e)[:140]}")
        return {"type": "playlist", "id": m.group(1), "nom": i.get("title") or m.group(1), "url": f"https://www.youtube.com/playlist?list={m.group(1)}",
                "channel_id": i.get("channel_id") or "", "chaine": i.get("channel") or i.get("uploader") or ""}
    ch = resoudre(url, extraire)
    return {"type": "chaine", **ch}


def videos_playlist(playlist_id: str, extraire=None, max_videos: int = 300) -> list[dict]:
    """Les vidéos d'une playlist (liste légère). [] si YouTube ne répond pas. Chaque vidéo garde la chaîne qui l'a publiée."""
    from yt_dlp import YoutubeDL
    from src import ytcookies
    extraire = extraire or (lambda u, o: YoutubeDL(o).extract_info(u, download=False))
    o = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "playlistend": max_videos, "socket_timeout": 25}
    o.update(ytcookies.opts())
    try:
        i = extraire(f"https://www.youtube.com/playlist?list={playlist_id}", o) or {}
    except Exception as e:
        logger.warning("Playlist %s illisible : %s", playlist_id, e)
        return []
    return [e for e in (i.get("entries") or []) if e and e.get("id")]


def contient_nom(titre: str, noms) -> bool:
    """Le titre de la vidéo porte-t-il le nom de l'artiste (ou un de ses alias) ? Tolère les tirets et parenthèses différents (« i‐dle » / « (G)I-DLE »).
    Un nom court (moins de 4 lettres ou chiffres) doit être un mot entier."""
    def plat(x):
        return re.sub(r"[^\w]", "", re.sub(r"[‐‑‒–—−]", "-", x or "").lower(), flags=re.UNICODE)
    t_plat, t = plat(titre), re.sub(r"[‐‑‒–—−]", "-", (titre or "").lower())
    for n in noms or []:
        p = plat(n)
        if not p:
            continue
        if len(p) >= 4:
            if p in t_plat:
                return True
        elif re.search(r"(?<!\w)" + re.escape(re.sub(r"[‐‑‒–—−]", "-", n.lower())) + r"(?!\w)", t):
            return True
    return False


def videos(chaine_id: str, extraire=None, max_videos: int = 200) -> list[dict]:
    """Les vidéos récentes de la chaîne (liste légère : titre, id, durée). [] si YouTube ne répond pas."""
    from yt_dlp import YoutubeDL
    from src import ytcookies
    extraire = extraire or (lambda u, o: YoutubeDL(o).extract_info(u, download=False))
    o = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True, "playlistend": max_videos, "socket_timeout": 25}
    o.update(ytcookies.opts())
    try:
        i = extraire(f"https://www.youtube.com/channel/{chaine_id}/videos", o) or {}
    except Exception as e:
        logger.warning("Chaîne %s illisible : %s", chaine_id, e)
        return []
    nom = i.get("channel") or i.get("uploader") or ""
    return [{**e, "channel_id": e.get("channel_id") or chaine_id, "channel": e.get("channel") or nom} for e in (i.get("entries") or []) if e and e.get("id")]
