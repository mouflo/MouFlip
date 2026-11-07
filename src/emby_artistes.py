"""« Artiste reconnu dans Emby » : Emby relie un clip à la page d'un artiste par le NOM (le même artiste sert la bibliothèque « Musique » et
« Clips Vidéos »). Si le nom écrit dans le .nfo n'est pas celui d'Emby, le clip apparaîtrait sous un autre artiste. On vérifie donc avant de ranger.
Lecture seule : aucune écriture dans Emby."""
import logging

from src.noms import normaliser

logger = logging.getLogger(__name__)


def chercher(emby, nom: str) -> dict:
    """{configure, etat, exact, proches, ...}.
    etat : « identique » (même écriture dans Emby), « autre_ecriture » (même nom écrit autrement : « Aespa » / « aespa »), « proche » (noms voisins),
    « absent » (aucun artiste de ce nom), « indisponible » (Emby injoignable ou clé absente). Ne lève jamais."""
    if not getattr(emby, "configured", False):
        return {"configure": False, "etat": "indisponible", "message": "Clé Emby absente : ⚙️ Réglages → Emby.", "exact": None, "proches": []}
    try:
        d = emby._request("GET", "/Artists", params={"SearchTerm": nom, "Limit": 12}).json()
    except Exception as e:
        logger.warning("Emby : recherche de l'artiste « %s » impossible : %s", nom, e)
        return {"configure": True, "etat": "indisponible", "message": f"Emby ne répond pas ({e}).", "exact": None, "proches": []}
    items = [{"nom": x.get("Name", ""), "id": x.get("Id")} for x in d.get("Items", []) if x.get("Name")]
    voulu = normaliser(nom) or nom.lower()
    identique = next((x for x in items if x["nom"] == nom), None)
    autre = next((x for x in items if x["nom"] != nom and (normaliser(x["nom"]) == voulu or x["nom"].casefold() == nom.casefold())), None)
    trouve = identique or autre
    if trouve:
        trouve = {**trouve, **_compter(emby, trouve["id"])}
    proches = [x for x in items if x is not trouve and (voulu in normaliser(x["nom"]) or normaliser(x["nom"]) in voulu)][:5]
    etat = "identique" if identique else "autre_ecriture" if autre else "proche" if proches else "absent"
    return {"configure": True, "etat": etat, "exact": trouve, "proches": proches, "message": ""}


def _compter(emby, artiste_id) -> dict:
    """Nombre de titres audio et de clips déjà reliés à cet artiste."""
    out = {"titres_audio": None, "clips": None}
    for cle, type_ in (("titres_audio", "Audio"), ("clips", "MusicVideo")):
        try:
            out[cle] = emby._request("GET", "/Items", params={"ArtistIds": artiste_id, "IncludeItemTypes": type_, "Recursive": "true", "Limit": 0}).json().get("TotalRecordCount")
        except Exception:
            pass
    return out
