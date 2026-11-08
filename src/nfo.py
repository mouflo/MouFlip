"""Fichiers .nfo au format de tes clips actuels (<musicvideo> pour un clip, <artist> pour un groupe). Tout est échappé : un titre avec « & » ou « < » reste valide."""
import time
from xml.sax.saxutils import escape

_PAYS = {"KR": "South Korea", "JP": "Japan", "US": "United States", "GB": "United Kingdom", "CN": "China", "TH": "Thailand", "TW": "Taiwan",
         "FR": "France", "CA": "Canada", "AU": "Australia", "DE": "Germany", "SE": "Sweden", "PH": "Philippines", "ID": "Indonesia", "VN": "Vietnam"}


def nfo_clip(titre, annee, groupe, album, sonde: dict, mbid_enregistrement: str = "", youtube_id: str = "", date_ajout: str = "") -> str:
    """<musicvideo> comme « BLACKPINK - BORN PINK - Pink Venom.nfo » : titre, année, durée, groupe, album et <fileinfo><streamdetails>."""
    v, a = sonde.get("video") or {}, sonde.get("audio") or {}
    duree_s = int(round(float(sonde.get("duree") or 0)))
    ligne = lambda tag, val: f"  <{tag}>{escape(str(val))}</{tag}>" if val not in (None, "") else f"  <{tag} />"
    out = ['<?xml version="1.0" encoding="utf-8" standalone="yes"?>', "<musicvideo>", "  <plot />", "  <outline />", "  <lockdata>false</lockdata>",
           ligne("dateadded", date_ajout or time.strftime("%Y-%m-%d %H:%M:%S")), ligne("title", titre), ligne("year", annee), ligne("sorttitle", titre),
           ligne("runtime", max(1, round(duree_s / 60)) if duree_s else "")]
    if mbid_enregistrement:
        out.append(f'  <uniqueid type="musicbrainz">{escape(mbid_enregistrement)}</uniqueid>')
    if youtube_id:
        out.append(f'  <uniqueid type="youtube">{escape(youtube_id)}</uniqueid>')
    out += [ligne("artist", groupe), ligne("album", album), "  <fileinfo>", "    <streamdetails>", "      <video>"]
    for tag, val in (("codec", v.get("codec")), ("micodec", v.get("codec")), ("bitrate", v.get("debit")), ("width", v.get("largeur")), ("height", v.get("hauteur")),
                     ("aspect", v.get("aspect")), ("aspectratio", v.get("aspect")), ("framerate", v.get("fps")), ("scantype", "progressive"), ("default", "True"),
                     ("forced", "False"), ("duration", max(1, round(duree_s / 60)) if duree_s else ""), ("durationinseconds", duree_s or "")):
        out.append("        " + ligne(tag, val).strip())
    out += ["      </video>", "      <audio>"]
    for tag, val in (("codec", a.get("codec")), ("micodec", a.get("codec")), ("bitrate", a.get("debit")), ("scantype", "progressive"), ("channels", a.get("canaux")),
                     ("samplingrate", a.get("echantillonnage")), ("default", "True"), ("forced", "False")):
        out.append("        " + ligne(tag, val).strip())
    out += ["      </audio>", "    </streamdetails>", "  </fileinfo>", "</musicvideo>", ""]
    return "\n".join(out)


def nfo_artiste(nom, mbid, genre, pays_code, debut, biographie, vignette_url, chemin) -> str:
    """artist.nfo comme celui de BLACKPINK : name, musicBrainzArtistID, genre, formed « South Korea (2016) », biography, thumb, path."""
    pays = _PAYS.get((pays_code or "").upper(), pays_code or "")
    fonde = f"{pays} ({debut})" if pays and debut else (pays or str(debut or ""))
    return "\n".join(['<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>', "<artist>", f"    <name>{escape(nom)}</name>",
                      f"    <musicBrainzArtistID>{escape(mbid)}</musicBrainzArtistID>", f"    <genre>{escape(genre or 'K-Pop')}</genre>", "    <born></born>",
                      f"    <formed>{escape(fonde)}</formed>", f"    <biography>{escape(biographie or '')}</biography>", "    <died></died>", "    <disbanded></disbanded>",
                      f'    <thumb preview="{escape(vignette_url)}/preview">{escape(vignette_url)}</thumb>' if vignette_url else "    <thumb />",
                      f"    <path>{escape(chemin)}</path>", "    <fanart />", "</artist>", ""])


def lire_biographie(chemin) -> str:
    """Le texte « biography » d'un artist.nfo déjà dans la médiathèque (lecture seule, jamais modifié) ; « » s'il manque ou est illisible."""
    import re
    import xml.etree.ElementTree as ET
    try:
        brut = open(chemin, "rb").read().decode("utf-8-sig", errors="replace")
    except OSError:
        return ""
    try:
        t = (ET.fromstring(brut.encode("utf-8")).findtext("biography") or "").strip()
        if t:
            return t
    except ET.ParseError:
        pass
    m = re.search(r"<biography>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</biography>", brut, re.S)
    return (m.group(1).strip() if m else "")
