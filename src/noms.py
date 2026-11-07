"""Noms de fichiers et de dossiers des clips : comparaison tolérante (accents, majuscules, ponctuation, mentions « Official MV »)
et chemin de rangement « Groupe/Album/Groupe - Album - Titre.mkv » (même modèle que les clips déjà présents)."""
import re
import unicodedata

# mentions ajoutées par YouTube ou les chaînes : elles ne font pas partie du titre de la chanson
_MENTIONS = re.compile(r"\b(official|officiel|mv|m\s*/\s*v|music\s*video|video|clip|4k|8k|hd|uhd|remastered|performance)\b", re.I)


def sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte or "") if unicodedata.category(c) != "Mn")


def normaliser(texte: str) -> str:
    """« 'KILL THIS LOVE' (4K M V) (1) » → « kill this love » : de quoi comparer deux écritures du même titre."""
    t = sans_accents(str(texte or "")).lower()
    t = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", t)          # tout ce qui est entre parenthèses ou crochets
    t = _MENTIONS.sub(" ", t)
    t = re.sub(r"[^\w\s]|_", " ", t, flags=re.UNICODE)
    return " ".join(t.split())


def plat(texte: str) -> str:
    """Comme `normaliser` mais SANS retirer ce qui est entre parenthèses : « Let the wind blow(바람아 불어라) » garde les deux titres. Sert à retrouver un titre
    dans celui d'une vidéo, qui met souvent le titre coréen ou anglais entre parenthèses."""
    t = sans_accents(str(texte or "")).lower()
    return " ".join(re.sub(r"[^\w\s]|_", " ", t, flags=re.UNICODE).split())


def propre(nom: str) -> str:
    """Un nom utilisable comme fichier ou dossier sur le NAS (pas de / : * ? « < > |)."""
    nom = str(nom or "").replace(":", " -").replace("/", "-").replace("\\", "-")
    nom = re.sub(r'[*?"<>|]', "", nom)
    return " ".join(nom.split()).strip(" .") or "Sans titre"


def nom_de_clip(groupe: str, album: str, titre: str, ext: str = "mkv") -> str:
    return f"{propre(groupe)} - {propre(album)} - {propre(titre)}.{ext}"


def chemin_prevu(racine: str, dossier_groupe: str, groupe: str, album: str, titre: str, ext: str = "mkv") -> str:
    """Chemin complet où le clip serait rangé (rien n'est créé)."""
    return f"{racine.rstrip('/')}/{propre(dossier_groupe)}/{propre(album)}/{nom_de_clip(groupe, album, titre, ext)}"
