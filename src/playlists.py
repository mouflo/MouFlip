"""Playlists Deezer publiques : lecture des morceaux ajoutés depuis une date (API publique de Deezer, sans clé ni connexion),
et mise en correspondance avec les clips officiels trouvés par MouFlip. Rien n'est téléchargé ici."""
import re
import time
from datetime import datetime

import requests

from src.noms import normaliser

API = "https://api.deezer.com"
ALTERNATIVES = re.compile(r"remix|instrumental|inst\.|live|acoustic|sped|slowed|karaoke|mr removed|cover|demo|nightcore", re.I)


class DeezerIndisponible(Exception):
    pass


def id_playlist(texte: str) -> str:
    """Un identifiant, ou une adresse deezer.com/…/playlist/123 ou un lien court link.deezer.com/s/… (suivi une seule fois)."""
    t = (texte or "").strip()
    if t.isdigit():
        return t
    if re.match(r"https?://link\.deezer\.com/", t):
        try:
            r = requests.get(t, allow_redirects=True, timeout=15, headers={"User-Agent": "MouFlip/1.0"})
            t = r.url
        except requests.RequestException as e:
            raise DeezerIndisponible(f"Lien court illisible : {e}")
    m = re.search(r"deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?playlist/(\d+)", t)
    return m.group(1) if m else ""


def _get(chemin, params=None):
    for essai in range(3):
        try:
            r = requests.get(API + chemin, params=params, timeout=20, headers={"User-Agent": "MouFlip/1.0"})
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            if essai == 2:
                raise DeezerIndisponible(f"Deezer ne répond pas ({e}).")
            time.sleep(1.5 * (essai + 1))
            continue
        if isinstance(d, dict) and d.get("error"):
            err = d["error"]
            if err.get("code") == 4 and essai < 2:                       # quota : une courte pause
                time.sleep(3)
                continue
            raise DeezerIndisponible("Playlist introuvable ou privée (elle doit être publique)." if err.get("code") in (800, 801) or "no data" in str(err.get("message", "")).lower()
                                     else f"Deezer : {err.get('message', err)}")
        return d
    raise DeezerIndisponible("Deezer ne répond pas.")


def infos(pid: str) -> dict:
    d = _get(f"/playlist/{pid}")
    if d.get("public") is False:
        raise DeezerIndisponible("Cette playlist n'est pas publique : Deezer ne la montre pas sans connexion.")
    return {"id": str(pid), "nom": d.get("title", ""), "nb": d.get("nb_tracks", 0)}


def morceaux(pid: str, depuis_ts: int = 0) -> list[dict]:
    """Les morceaux de la playlist ajoutés à partir de cette date (secondes Unix), du plus récent au plus ancien.
    Toute la playlist est lue (par pages de 500, seulement des appels à Deezer) : l'ordre d'une playlist ne suit pas toujours la date d'ajout."""
    sortie, index = [], 0
    while True:
        d = _get(f"/playlist/{pid}/tracks", {"limit": 500, "index": index})
        page = d.get("data") or []
        for t in page:
            ts = int(t.get("time_add") or 0)
            if ts >= depuis_ts and t.get("title") and (t.get("artist") or {}).get("name"):
                sortie.append({"deezer_id": t.get("id"), "titre": t["title"], "artiste": t["artist"]["name"], "ajoute": ts})
        index += len(page)
        if not page or not d.get("next") or index > 20000:
            break
    sortie.sort(key=lambda x: -x["ajoute"])
    return sortie


def date_en_ts(texte: str) -> int:
    """« 2026-10-15 » ou « 15/10/2026 » → secondes Unix (minuit, heure de Paris approchée par l'heure du serveur) ; 0 si vide."""
    t = (texte or "").strip()
    if not t:
        return 0
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return int(datetime.strptime(t, fmt).timestamp())
        except ValueError:
            pass
    raise ValueError("Date illisible : écris-la 2026-10-15 ou 15/10/2026.")


def titre_cle(titre: str) -> str:
    """Le titre « nu » pour comparer : sans (feat. …), (Korean Ver.), [..], ni « - Remastered »."""
    t = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", titre or "")
    t = re.sub(r"\s[-–]\s.*$", "", t)
    t = re.sub(r"\b(feat|ft)\.?\s.*$", "", t, flags=re.I)
    return normaliser(t)


def version_alternative(titre: str) -> bool:
    """Remix, instrumental, live… : ce n'est pas le clip du morceau (MouFlip ne propose que le clip officiel)."""
    return bool(ALTERNATIVES.search(" ".join(re.findall(r"[\(\[]([^)\]]*)[\)\]]", titre or "")) + " " + (titre.split(" - ", 1)[1] if " - " in (titre or "") else "")))


def clip_du_morceau(titre: str, clips: list[dict]) -> dict | None:
    cle = titre_cle(titre)
    if len(cle) < 2:
        return None
    for c in clips:
        if titre_cle(c["titre"]) == cle:
            return c
    return None
