"""Quelle vidéo YouTube pour ce clip, et quel est son MEILLEUR format ? Lecture seule : rien n'est téléchargé.
Recherche (yt-dlp, avec les cookies réglés) → choix de la vidéo officielle → liste des formats → meilleur = résolution la plus haute,
puis débit le plus élevé (même tri que « -S res,tbr » ; le flux Premium « amélioré » est pris s'il existe)."""
import logging
import re

from src import ytcookies
from src.noms import normaliser

logger = logging.getLogger(__name__)

# Ce qui n'est JAMAIS le clip officiel : répétitions de danse, extraits, paroles, concerts, lives, émissions de télévision, coulisses, réactions…
# (un seul de ces mots dans le titre suffit à écarter la vidéo)
_PAS_LE_CLIP = re.compile(r"\b(dance\s*practice|choreograph\w*|teaser|trailer|lyrics?|reaction|live|stage|stages|performance|behind|making|"
                          r"cover|remix|instrumental|audio|karaoke|fancam|shorts?|highlight|medley|preview|snippet|concert|tour|festival|awards?|"
                          r"countdown|inkigayo|music\s*bank|show\s*champion|the\s*show|first\s*take|kcon|interview|special|episode|ep\.?\s*\d+|"
                          r"jacket|photoshoot|camerawork|guide|sped\s*up|slowed|mashup|version|ver\.|recap|vlog|fan\s*made|mirrored|dance\s*break)\b|"
                          r"[#＃]|\u2022", re.I)
_BON_SIGNE = re.compile(r"\b(m\s*/\s*v|mv|music\s*video|official\s*video|official\s*mv)\b", re.I)
# Chaînes de télévision, d'émissions et de fans : jamais le clip officiel
_CHAINES_PAS_OFFICIELLES = re.compile(r"\b(mnet|kbs|sbs|mbc|m2|studio\s*choom|dingo|the\s*first\s*take|kcon|arirang|jtbc|tving|vevo\s*live|fan|lyrics?|"
                                      r"reaction|react|dance|cover)\b", re.I)
_LABELS = ("smtown", "hybe", "yg entertainment", "blackpink", "jyp", "1thek", "starship", "cube", "kq", "fnc", "pledis", "ador", "kakao",
           "dreamcatcher", "wm entertainment", "woollim", "stone music", "genie", "ist entertainment", "source music", "belift")


SEUIL_OFFICIEL = 90                                        # titre + « MV » + chaîne du label ou du groupe


def _options(plat: bool = False) -> dict:
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 25}
    if plat:
        o["extract_flat"] = True
    o.update(ytcookies.opts())
    return o


def _note(c: dict, groupe: str, titre: str) -> int:
    """Points d'une vidéo candidate : plus c'est haut, plus c'est probablement le clip officiel."""
    t = c.get("title") or ""
    nt, ng = normaliser(t), normaliser(groupe)
    note = 0
    if normaliser(titre) and normaliser(titre) in nt:
        note += 40
    else:
        return -100                                          # le titre du clip n'apparaît pas : autre chose
    # le titre cherché peut lui-même contenir un de ces mots (« Live Wire ») : on l'ôte avant de juger la vidéo
    reste = re.sub(re.escape(titre), " ", t, flags=re.I)
    if _PAS_LE_CLIP.search(reste):
        return -100                                          # live, concert, émission, coulisses… : jamais proposé
    chaine_brute = (c.get("channel") or c.get("uploader") or "")
    if _CHAINES_PAS_OFFICIELLES.search(chaine_brute) and not any(l in chaine_brute.lower() for l in _LABELS):
        return -100
    if ng and ng.split()[0] in nt:
        note += 8
    if _BON_SIGNE.search(t):
        note += 25
    chaine = (c.get("channel") or c.get("uploader") or "").lower()
    if any(l in chaine for l in _LABELS) or (ng and ng in normaliser(chaine)):
        note += 25
    if "official" in chaine or "vevo" in chaine:
        note += 8
    d = c.get("duration") or 0
    if d and not 110 <= d <= 480:
        note -= 30
    return note


def choisir_video(groupe: str, titre: str, extraire=None) -> tuple[dict | None, list[dict]]:
    """(meilleur candidat ou None, tous les candidats notés). `extraire(requete, options)` est remplaçable par les tests."""
    from yt_dlp import YoutubeDL
    extraire = extraire or (lambda q, o: YoutubeDL(o).extract_info(q, download=False))
    r = extraire(f"ytsearch8:{groupe} {titre} MV official", _options(plat=True)) or {}
    cands = []
    for e in r.get("entries") or []:
        if not e:
            continue
        cands.append({"id": e.get("id"), "title": e.get("title"), "channel": e.get("channel") or e.get("uploader"), "duration": e.get("duration"),
                      "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}", "note": _note(e, groupe, titre)})
    cands.sort(key=lambda c: -c["note"])
    return (cands[0] if cands and cands[0]["note"] >= 40 else None), cands


def meilleur_format(infos: dict) -> dict | None:
    """Parmi les formats vidéo d'une vidéo : hauteur la plus grande, puis débit le plus élevé. Un format « Premium » est signalé."""
    videos = [f for f in infos.get("formats") or [] if f.get("vcodec") not in (None, "none") and f.get("height")]
    if not videos:
        return None
    f = max(videos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    codec = (f.get("vcodec") or "").split(".")[0]
    premium = "premium" in (f.get("format_note") or "").lower() or "premium" in (f.get("format") or "").lower()
    audios = [a for a in infos.get("formats") or [] if a.get("acodec") not in (None, "none") and a.get("vcodec") in (None, "none")]
    a = max(audios, key=lambda a: a.get("abr") or a.get("tbr") or 0) if audios else None
    return {"format_id": f.get("format_id"), "largeur": f.get("width"), "hauteur": f.get("height"), "fps": f.get("fps"),
            "codec": {"avc1": "h264", "vp09": "vp9", "av01": "av1"}.get(codec, codec), "debit_kbps": round(f.get("tbr") or 0),
            "premium": premium, "resolution": f"{f.get('height')}p",
            "audio": {"codec": (a.get("acodec") or "").split(".")[0], "debit_kbps": round(a.get("abr") or a.get("tbr") or 0)} if a else None}


def candidats(groupe: str, titre: str, extraire=None, combien: int = 5) -> list[dict]:
    """Les vidéos YouTube qui pourraient être ce clip, la plus probable d'abord (pour les regarder et choisir). Lève en cas de panne de YouTube."""
    _choisi, cands = choisir_video(groupe, titre, extraire)
    bons = [c for c in cands if c["note"] > -100]
    officielles = [c for c in bons if c["note"] >= SEUIL_OFFICIEL]
    if officielles:                                          # une chaîne officielle existe : les re-publications de fans ne sont même pas montrées
        bons = officielles
    return [{k: c.get(k) for k in ("id", "title", "channel", "duration", "note")} | {"url": f"https://www.youtube.com/watch?v={c['id']}",
                                                                                      "officielle": c["note"] >= SEUIL_OFFICIEL}
            for c in bons][:combien]


def analyser(groupe: str, titre: str, extraire=None, video_id: str = "") -> dict:
    """Résultat complet pour un clip. Ne lève jamais : une panne de YouTube est dite (« erreur »), pas confondue avec « introuvable ».
    `video_id` : la vidéo que tu as choisie en la regardant (sinon la plus probable)."""
    from yt_dlp import YoutubeDL
    extraire = extraire or (lambda q, o: YoutubeDL(o).extract_info(q, download=False))
    try:
        if video_id and re.fullmatch(r"[\w-]{11}", video_id):
            choisi, cands = {"id": video_id, "title": "", "channel": "", "duration": 0, "url": f"https://www.youtube.com/watch?v={video_id}", "note": 100}, []
        else:
            choisi, cands = choisir_video(groupe, titre, extraire)
    except Exception as e:
        logger.warning("Recherche YouTube impossible pour « %s - %s » : %s", groupe, titre, e)
        return {"etat": "erreur", "erreur": f"YouTube ne répond pas : {str(e)[:160]}"}
    if not choisi:
        return {"etat": "introuvable", "candidats": [{k: c[k] for k in ("title", "channel", "note")} for c in cands[:3]]}
    try:
        infos = extraire(choisi["url"], _options()) or {}
    except Exception as e:
        logger.warning("Formats illisibles pour %s : %s", choisi["url"], e)
        return {"etat": "erreur", "erreur": f"Formats illisibles : {str(e)[:160]}", "video": {k: choisi[k] for k in ("id", "title", "channel")}}
    return {"etat": "ok", "video": {"id": choisi["id"], "titre": infos.get("title") or choisi["title"], "chaine": infos.get("channel") or choisi["channel"],
                                    "duree": infos.get("duration") or choisi["duration"], "url": f"https://www.youtube.com/watch?v={choisi['id']}"},
            "format": meilleur_format(infos)}
