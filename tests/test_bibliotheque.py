"""📚 Ma bibliothèque : réunir les dossiers d'un même groupe (clips / musique), la fiche d'images, le remplacement avec sauvegarde.
Aucun réseau, aucun vrai dossier : tout est fabriqué dans un dossier temporaire."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from src import bibliotheque, images_perso

KR = "64af8439-3319-4ad5-847c-79f828603427"
UK = "e3cb4543-210f-499a-b0d1-3882c312dfb9"


def png(couleur=(200, 30, 30), taille=(8, 8)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", taille, couleur).save(b, "PNG")
    return b.getvalue()


def nfo(dossier: Path, mbid: str, nom="x"):
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "artist.nfo").write_text(f'<?xml version="1.0"?>\n<artist><name>{nom}</name><musicBrainzArtistID>{mbid}</musicBrainzArtistID></artist>', encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.clips, self.musique, self.data = self.tmp / "clips", self.tmp / "musique", self.tmp / "data" / "bibliotheque"
        for d in (self.clips, self.musique, self.data):
            d.mkdir(parents=True)
        self.emby = mock.Mock(configured=True)
        self.ctx = {"fiche": {"nom": "Rainbow"}, "candidats": {"vignette": [{"url": "http://img/v1.jpg", "source": "fanart.tv"}, {"url": "http://img/v2.jpg", "source": "TheAudioDB"}],
                                                              "banniere": [{"url": "http://img/b1.jpg", "source": "fanart.tv"}], "logo": [{"url": "http://img/l1.png", "source": "fanart.tv"}],
                                                              "fanart": [{"url": f"http://img/f{i}.jpg", "source": "fanart.tv"} for i in range(1, 6)]},
                    "images_album": {"rg1": {"couverture_liste": ["http://img/c1.jpg"], "cdart_liste": ["http://img/d1.png"]}},
                    "clips": [{"titre": "Black Swan", "album": "INNOCENT", "rg": "rg1"}]}
        self.telecharges = []
        images_perso.configurer(self.data)
        bibliotheque.configurer(self.data, lambda: str(self.clips), lambda: str(self.musique), self.emby, lambda mbid: self.ctx, None, self._telecharger)

    def _telecharger(self, url):
        self.telecharges.append(url)
        return b"pas une image" if "casse" in url else png((len(url) * 7 % 255, 50, 90))

    def groupe(self, nom="Rainbow (레인보우)"):
        return next(g for g in bibliotheque.lister() if g["nom"] == nom)


class ListeTest(Base):
    def test_les_deux_dossiers_d_un_meme_groupe_sont_reunis_par_leur_identifiant(self):
        nfo(self.clips / "Rainbow", KR)
        nfo(self.musique / "Rainbow (레인보우)", KR)
        gs = bibliotheque.lister()
        self.assertEqual(len(gs), 1)
        g = gs[0]
        self.assertEqual((g["nom"], g["mbid"]), ("Rainbow (레인보우)", KR))              # le nom de la musique (celui d'Emby)
        self.assertEqual((g["clips"].name, g["musique"].name), ("Rainbow", "Rainbow (레인보우)"))

    def test_deux_groupes_de_meme_nom_mais_d_identifiants_differents_restent_separes(self):
        nfo(self.clips / "Rainbow", KR)
        nfo(self.musique / "Rainbow", UK)
        self.assertEqual(len(bibliotheque.lister()), 2)

    def test_un_dossier_sans_nfo_rejoint_le_groupe_identifie_de_meme_nom(self):
        nfo(self.musique / "Rainbow (레인보우)", KR)
        (self.clips / "Rainbow").mkdir()
        gs = bibliotheque.lister()
        self.assertEqual(len(gs), 1)
        self.assertTrue(gs[0]["clips"] and gs[0]["musique"])

    def test_plusieurs_groupes_de_meme_nom_le_dossier_au_nom_exact_choisit(self):
        nfo(self.musique / "LiSA", UK, "LiSA")
        nfo(self.musique / "LISA (Blackpink)", KR, "LISA")
        (self.clips / "LISA (Blackpink)").mkdir()                          # sans artist.nfo : rejoint celui de même nom EXACT
        gs = {g["mbid"]: g for g in bibliotheque.lister()}
        self.assertEqual(len(gs), 2)
        self.assertTrue(gs[KR]["clips"] and gs[KR]["musique"])
        self.assertIsNone(gs[UK]["clips"])

    def test_plusieurs_groupes_de_meme_nom_sans_dossier_exact_ne_devine_pas(self):
        nfo(self.musique / "LiSA", UK, "LiSA")
        nfo(self.musique / "LISA (Blackpink)", KR, "LISA")
        (self.clips / "Lisa (autre)").mkdir()                              # nom différent de tous : on ne devine pas
        self.assertEqual(len(bibliotheque.lister()), 3)

    def test_sans_identifiant_le_nom_sans_parentheses_reunit(self):
        (self.clips / "aespa").mkdir()
        (self.musique / "aespa (에스파)").mkdir()
        gs = bibliotheque.lister()
        self.assertEqual(len(gs), 1)
        self.assertEqual(gs[0]["mbid"], "")

    def test_les_dossiers_techniques_sont_ignores(self):
        (self.clips / "@eaDir").mkdir()
        (self.clips / "#recycle").mkdir()
        (self.clips / "aespa").mkdir()
        self.assertEqual([g["nom"] for g in bibliotheque.lister()], ["aespa"])

    def test_identifier_a_la_main_est_retenu(self):
        (self.clips / "Rainbow").mkdir()
        bibliotheque.identifier("rainbow", KR)
        self.assertEqual(bibliotheque.lister()[0]["mbid"], KR)
        with self.assertRaises(ValueError):
            bibliotheque.identifier("rainbow", "pas-un-identifiant")

    def test_le_resume_montre_si_le_poster_existe(self):
        nfo(self.clips / "Rainbow", KR)
        (self.clips / "Rainbow" / "folder.jpg").write_bytes(png())
        r = bibliotheque.resume(bibliotheque.lister()[0])
        self.assertEqual(r["poster"], {"clips": True, "musique": False})


class FicheTest(Base):
    def _groupe(self):
        nfo(self.clips / "Rainbow", KR)
        nfo(self.musique / "Rainbow (레인보우)", KR)
        (self.musique / "Rainbow (레인보우)" / "folder.jpg").write_bytes(png())
        (self.clips / "Rainbow" / "INNOCENT").mkdir()
        (self.clips / "Rainbow" / "INNOCENT" / "Rainbow - INNOCENT - Black Swan.mkv").write_bytes(b"v")
        (self.musique / "Rainbow (레인보우)" / "INNOCENT").mkdir()
        (self.musique / "Rainbow (레인보우)" / "INNOCENT" / "Rainbow - INNOCENT - 02 - Black Swan.m4a").write_bytes(b"a")
        (self.musique / "Rainbow (레인보우)" / "INNOCENT" / "folder.jpg").write_bytes(png())
        return bibliotheque.lister()[0]

    def test_la_fiche_reunit_images_albums_clips_et_morceaux(self):
        f = bibliotheque.fiche(self._groupe())
        self.assertEqual(f["images"]["vignette"]["existant"], {"clips": False, "musique": True})
        self.assertEqual([c["url"] for c in f["images"]["vignette"]["candidats"]], ["http://img/v1.jpg", "http://img/v2.jpg"])
        self.assertEqual(len(f["fonds"]["candidats"]), 5)
        self.assertEqual(f["fonds"]["fichiers"], ["fanart.jpg", "extrafanart/fanart1.jpg", "extrafanart/fanart2.jpg", "extrafanart/fanart3.jpg"])
        (a,) = f["albums"]
        self.assertEqual(a["nom"], "INNOCENT")
        self.assertEqual((a["clips"], a["morceaux"]), (["Rainbow - INNOCENT - Black Swan.mkv"], ["Rainbow - INNOCENT - 02 - Black Swan.m4a"]))
        self.assertEqual(a["couverture"], {"clips": False, "musique": True})
        self.assertEqual([c["url"] for c in a["candidats"]["couverture"]], ["http://img/c1.jpg"])
        self.assertEqual([c["url"] for c in a["candidats"]["cdart"]], ["http://img/d1.png"])

    def test_les_images_ajoutees_a_la_main_sont_proposees(self):
        g = self._groupe()
        e = images_perso.ajouter(KR, "vignette", png((1, 2, 3), (120, 120)), "moi.png")
        urls = [c["url"] for c in bibliotheque.fiche(g)["images"]["vignette"]["candidats"]]
        self.assertIn(f"perso:{e['id']}", urls)

    def test_sans_identifiant_la_fiche_le_dit_et_ne_propose_rien(self):
        (self.clips / "Inconnu").mkdir()
        f = bibliotheque.fiche(bibliotheque.lister()[0])
        self.assertIn("pas encore identifié", f["avertissement"])
        self.assertEqual(f["images"]["vignette"]["candidats"], [])

    def test_panne_des_sources_la_fiche_s_ouvre_quand_meme(self):
        g = self._groupe()
        bibliotheque._CFG["candidats"] = mock.Mock(side_effect=RuntimeError("MusicBrainz injoignable"))
        f = bibliotheque.fiche(g)
        self.assertIn("indisponibles", f["avertissement"])
        self.assertEqual(f["images"]["vignette"]["existant"]["musique"], True)

    def test_servir_une_image_seulement_parmi_les_noms_connus(self):
        g = self._groupe()
        self.assertTrue(bibliotheque.chemin_image(g, "musique", "folder.jpg"))
        self.assertTrue(bibliotheque.chemin_image(g, "musique", "INNOCENT/folder.jpg"))
        for mauvais in ("artist.nfo", "../folder.jpg", "INNOCENT/../../x.jpg", "../../etc/passwd", "INNOCENT/Rainbow - INNOCENT - 02 - Black Swan.m4a", "@eaDir/folder.jpg", "ABSENT/folder.jpg"):
            self.assertIsNone(bibliotheque.chemin_image(g, "musique", mauvais), mauvais)
        self.assertIsNone(bibliotheque.chemin_image(g, "clips", "folder.jpg"))             # n'existe pas dans les clips
        self.assertIsNone(bibliotheque.chemin_image(g, "autre", "folder.jpg"))


class AppliquerTest(Base):
    def setUp(self):
        super().setUp()
        nfo(self.clips / "Rainbow", KR)
        nfo(self.musique / "Rainbow (레인보우)", KR)
        self.ancienne = png((9, 9, 9))
        (self.clips / "Rainbow" / "folder.jpg").write_bytes(self.ancienne)
        (self.musique / "Rainbow (레인보우)" / "folder.jpg").write_bytes(self.ancienne)
        for racine, nom in ((self.clips, "Rainbow"), (self.musique, "Rainbow (레인보우)")):
            (racine / nom / "INNOCENT").mkdir()
        (self.musique / "Rainbow (레인보우)" / "INNOCENT" / "folder.jpg").write_bytes(self.ancienne)
        self.g = self.groupe()

    def sauvegardes(self):
        return sorted(p.name for p in (self.tmp / "data" / "sauvegardes").rglob("*") if p.is_file())

    def test_remplace_dans_les_deux_dossiers_avec_sauvegarde_et_previent_emby(self):
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips", "musique"], "images": {"vignette": "http://img/v2.jpg"}})
        self.assertTrue(r["ok"], r)
        for d in (self.clips / "Rainbow", self.musique / "Rainbow (레인보우)"):
            self.assertNotEqual((d / "folder.jpg").read_bytes(), self.ancienne)
        self.assertEqual(len(self.sauvegardes()), 2)                                        # une sauvegarde par dossier, sans écrasement
        self.assertEqual(self.emby._request.call_count, 2)

    def test_deux_sauvegardes_du_meme_nom_ne_s_ecrasent_pas(self):
        nfo(self.clips / "aespa", "b51c672b-85e0-48fe-8648-470a2422229f")
        nfo(self.musique / "aespa", "b51c672b-85e0-48fe-8648-470a2422229f")
        (self.clips / "aespa" / "folder.jpg").write_bytes(png((1, 1, 1)))
        (self.musique / "aespa" / "folder.jpg").write_bytes(png((2, 2, 2)))
        g = self.groupe("aespa")
        bibliotheque.appliquer(g, {"cibles": ["clips", "musique"], "images": {"vignette": "http://img/v1.jpg"}})
        contenus = sorted(p.read_bytes() for p in (self.tmp / "data" / "sauvegardes").rglob("*.jpg"))
        self.assertEqual(contenus, sorted([png((1, 1, 1)), png((2, 2, 2))]))

    def test_une_seule_cible(self):
        bibliotheque.appliquer(self.g, {"cibles": ["musique"], "images": {"vignette": "http://img/v1.jpg"}})
        self.assertEqual((self.clips / "Rainbow" / "folder.jpg").read_bytes(), self.ancienne)
        self.assertNotEqual((self.musique / "Rainbow (레인보우)" / "folder.jpg").read_bytes(), self.ancienne)

    def test_une_adresse_hors_propositions_est_refusee_sans_rien_toucher(self):
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips"], "images": {"vignette": "http://pirate/x.jpg"}})
        self.assertFalse(r["ok"])
        self.assertIn("propositions", r["erreurs"][0])
        self.assertEqual((self.clips / "Rainbow" / "folder.jpg").read_bytes(), self.ancienne)
        self.assertEqual(self.telecharges, [])                                              # rien n'a même été téléchargé

    def test_une_image_cassee_ne_remplace_jamais_la_bonne(self):
        self.ctx["candidats"]["vignette"].append({"url": "http://img/casse.jpg", "source": "fanart.tv"})
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips"], "images": {"vignette": "http://img/casse.jpg"}})
        self.assertFalse(r["ok"])
        self.assertEqual((self.clips / "Rainbow" / "folder.jpg").read_bytes(), self.ancienne)
        self.assertEqual(self.sauvegardes(), [])

    def test_le_lot_complet_fonds_compris(self):
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips"], "images": {"banniere": "http://img/b1.jpg", "logo": "http://img/l1.png"},
                                            "fonds": ["http://img/f1.jpg", None, "http://img/f3.jpg", "http://img/f4.jpg", "http://img/f5.jpg"]})
        self.assertTrue(r["ok"], r)
        d = self.clips / "Rainbow"
        for rel in ("banner.jpg", "logo.png", "fanart.jpg", "extrafanart/fanart2.jpg", "extrafanart/fanart3.jpg"):
            self.assertTrue((d / rel).is_file(), rel)
        self.assertFalse((d / "extrafanart" / "fanart1.jpg").exists())                      # le « rien » du 2e fond : non touché
        self.assertEqual(self.sauvegardes(), [])                                            # rien de remplacé : rien à sauvegarder

    def test_pochette_et_cdart_d_album_seulement_si_le_dossier_d_album_existe(self):
        shutil_dossier = self.clips / "Rainbow" / "INNOCENT"
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips", "musique"], "albums": {"INNOCENT": {"couverture": "http://img/c1.jpg", "cdart": "http://img/d1.png"}}})
        self.assertTrue(r["ok"], r)
        self.assertTrue((shutil_dossier / "folder.jpg").is_file() and (shutil_dossier / "cdart.png").is_file())
        self.assertNotEqual((self.musique / "Rainbow (레인보우)" / "INNOCENT" / "folder.jpg").read_bytes(), self.ancienne)
        self.assertEqual(len(self.sauvegardes()), 1)                                        # seule la pochette de la musique existait

    def test_aucun_dossier_n_est_cree(self):
        (self.clips / "Rainbow" / "INNOCENT").rmdir()
        avant = sorted(p for p in self.clips.rglob("*"))
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips"], "albums": {"INNOCENT": {"couverture": "http://img/c1.jpg"}}})
        self.assertEqual(sorted(p for p in self.clips.rglob("*")), avant)
        self.assertFalse(r["ok"] and r["faits"])

    def test_copier_une_image_de_l_autre_dossier(self):
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips"], "images": {"vignette": "copie:musique:folder.jpg"}})
        self.assertTrue(r["ok"], r)
        self.assertEqual((self.clips / "Rainbow" / "folder.jpg").read_bytes(), self.ancienne)   # même contenu que l'autre dossier
        r = bibliotheque.appliquer(self.g, {"cibles": ["clips"], "images": {"vignette": "copie:musique:../../secret.jpg"}})
        self.assertFalse(r["ok"])

    def test_une_image_ajoutee_a_la_main_peut_etre_posee(self):
        e = images_perso.ajouter(KR, "vignette", png((7, 70, 200), (120, 120)), "moi.png")
        r = bibliotheque.appliquer(self.g, {"cibles": ["musique"], "images": {"vignette": f"perso:{e['id']}"}})
        self.assertTrue(r["ok"], r)
        self.assertNotEqual((self.musique / "Rainbow (레인보우)" / "folder.jpg").read_bytes(), self.ancienne)

    def test_sans_dossier_choisi_ou_sans_image_rien_n_est_fait(self):
        self.assertFalse(bibliotheque.appliquer(self.g, {"cibles": [], "images": {"vignette": "http://img/v1.jpg"}})["ok"])
        self.assertFalse(bibliotheque.appliquer(self.g, {"cibles": ["clips"]})["ok"])
        self.assertFalse(bibliotheque.appliquer(self.g, {"cibles": ["clips", "inconnu"], "images": {"vignette": None}})["ok"])


if __name__ == "__main__":
    unittest.main()
