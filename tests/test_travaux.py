"""MouFlip : volume des clips, .nfo, plan et rangement, fanart.tv, file de travaux. Aucun réseau : vidéo de 2 s fabriquée par ffmpeg, YouTube et Emby remplacés."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import audio, nfo, rangement, sources, telechargement, travaux  # noqa: E402


def faire_video(chemin, db_volume=0, duree=3):
    """Petite vidéo mkv : image de test + sinus 440 Hz au volume voulu (dB)."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={duree}:size=320x180:rate=24",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={duree}:sample_rate=48000", "-af", f"volume={db_volume}dB", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(chemin)], check=True, capture_output=True)


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_niveau_ramene_a_la_cible_et_image_recopiee(self):
        src = self.tmp / "fort.mkv"
        faire_video(src, db_volume=-3)
        avant, _ = audio.mesurer(src)
        r = audio.normaliser(src, self.tmp / "sortie.mkv", 89.0)
        self.assertTrue(r["retouche"])
        self.assertAlmostEqual(r["apres"], 89.0, delta=0.7)
        self.assertNotAlmostEqual(avant, 89.0, delta=2.0)                       # le clip d'origine n'était pas à la cible
        s = telechargement.sonder(self.tmp / "sortie.mkv")
        self.assertEqual((s["video"]["codec"], s["audio"]["codec"]), ("h264", "aac"))
        self.assertEqual((s["video"]["largeur"], s["video"]["hauteur"]), (320, 180))
        self.assertTrue(src.exists())                                          # l'original n'est pas touché

    def test_video_recopiee_telle_quelle(self):
        src = self.tmp / "a.mkv"
        faire_video(src)
        audio.normaliser(src, self.tmp / "b.mkv", 89.0)
        def trames(p):
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets,codec_name",
                                  "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()
            return out
        self.assertEqual(trames(src), trames(self.tmp / "b.mkv"))              # mêmes paquets vidéo : aucun réencodage de l'image

    def test_deja_au_bon_niveau_rien_n_est_reencode(self):
        src = self.tmp / "c.mkv"
        faire_video(src)
        niveau, _ = audio.mesurer(src)
        r = audio.normaliser(src, self.tmp / "d.mkv", niveau)
        self.assertFalse(r["retouche"])
        self.assertEqual(r["gain"], 0.0)

    def test_fichier_sans_audio_est_refuse_clairement(self):
        src = self.tmp / "muet.mkv"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10", str(src)], check=True, capture_output=True)
        with self.assertRaises(audio.AudioError):
            audio.normaliser(src, self.tmp / "x.mkv", 89.0)


class NfoTest(unittest.TestCase):
    SONDE = {"duree": 193.4, "video": {"codec": "vp9", "debit": 17004666, "largeur": 3840, "hauteur": 2160, "fps": 24, "aspect": "16:9"},
             "audio": {"codec": "aac", "debit": 192000, "canaux": 2, "echantillonnage": "48000"}}

    def test_nfo_clip_comme_le_modele(self):
        x = nfo.nfo_clip("Pink Venom", "2022", "BLACKPINK", "BORN PINK", self.SONDE, "mb1", "gQlMMD8auMs", "2026-11-06 12:00:00")
        for attendu in ("<musicvideo>", "<title>Pink Venom</title>", "<year>2022</year>", "<sorttitle>Pink Venom</sorttitle>", "<runtime>3</runtime>",
                        "<artist>BLACKPINK</artist>", "<album>BORN PINK</album>", "<codec>vp9</codec>", "<width>3840</width>", "<height>2160</height>",
                        "<durationinseconds>193</durationinseconds>", '<uniqueid type="youtube">gQlMMD8auMs</uniqueid>', "<lockdata>false</lockdata>"):
            self.assertIn(attendu, x)
        import xml.dom.minidom
        xml.dom.minidom.parseString(x.encode("utf-8"))                         # XML valide

    def test_caracteres_speciaux_echappes(self):
        x = nfo.nfo_clip("Q&A <live>", "2022", "AC/DC & co", "Album", self.SONDE)
        import xml.dom.minidom
        xml.dom.minidom.parseString(x.encode("utf-8"))
        self.assertIn("Q&amp;A &lt;live&gt;", x)

    def test_nfo_artiste(self):
        x = nfo.nfo_artiste("BLACKPINK", "4864", "K-Pop", "KR", "2016", "Un groupe & ses fans.", "http://x/y.jpg", "/mnt/Video clip/BLACKPINK")
        self.assertIn("<formed>South Korea (2016)</formed>", x)
        self.assertIn("<musicBrainzArtistID>4864</musicBrainzArtistID>", x)
        self.assertIn("&amp;", x)


class RangementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.racine = self.tmp / "Video clip"
        self.racine.mkdir()
        self.video = self.tmp / "clip.mkv"
        self.video.write_bytes(b"V" * 1000)
        self.poster = self.tmp / "poster.jpg"
        self.poster.write_bytes(b"P")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plan(self, **kw):
        return rangement.plan(self.racine, kw.pop("dossier", ""), "aespa", "LEMONADE", "LEMONADE", self.video, self.poster, "<nfo/>", "<artist/>",
                              kw.pop("images", {"logo": "http://img/logo.png", "fanart": "http://img/f.jpg"}), kw.pop("album", None))

    def test_le_plan_n_ecrit_rien_et_liste_tout(self):
        p = self._plan()
        chemins = [Path(l["chemin"]).relative_to(self.racine).as_posix() for l in p]
        for attendu in ("aespa", "aespa/LEMONADE", "aespa/LEMONADE/aespa - LEMONADE - LEMONADE.mkv", "aespa/LEMONADE/aespa - LEMONADE - LEMONADE.nfo",
                        "aespa/LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg", "aespa/LEMONADE/folder.jpg", "aespa/artist.nfo", "aespa/logo.png", "aespa/fanart.jpg"):
            self.assertIn(attendu, chemins)
        self.assertEqual(list(self.racine.iterdir()), [])                       # rien d'écrit

    def test_dossier_existant_du_groupe_garde_son_ecriture(self):
        (self.racine / "Aespa").mkdir()
        p = self._plan(dossier=str(self.racine / "Aespa"))
        self.assertTrue(any("/Aespa/LEMONADE/" in l["chemin"] for l in p))
        self.assertFalse(any(l["chemin"].endswith("/aespa/artist.nfo") for l in p))

    def test_executer_ecrit_et_ne_remplace_jamais(self):
        r = rangement.executer(self._plan(images={}))
        self.assertEqual(r["erreurs"], [])
        clip = self.racine / "aespa/LEMONADE/aespa - LEMONADE - LEMONADE.mkv"
        self.assertEqual(clip.read_bytes(), b"V" * 1000)
        self.assertEqual((self.racine / "aespa/artist.nfo").read_text(), "<artist/>")
        clip.write_bytes(b"ANCIEN")                                            # un fichier déjà là n'est jamais remplacé
        (self.racine / "aespa/artist.nfo").write_text("MODIFIE par moi")
        r2 = rangement.executer(self._plan(images={}))
        self.assertEqual(clip.read_bytes(), b"ANCIEN")
        self.assertEqual((self.racine / "aespa/artist.nfo").read_text(), "MODIFIE par moi")
        self.assertTrue(any("existe déjà" in m for m in r2["ignores"]))

    def test_images_telechargees_et_pochette_d_album(self):
        faux = mock.Mock(status_code=200, content=b"IMG")
        with mock.patch.object(rangement.requests, "get", return_value=faux):
            r = rangement.executer(self._plan(album={"couverture": "http://img/cover.jpg", "cdart": "http://img/cd.png"}))
        self.assertEqual(r["erreurs"], [])
        self.assertEqual((self.racine / "aespa/logo.png").read_bytes(), b"IMG")
        self.assertEqual((self.racine / "aespa/LEMONADE/cdart.png").read_bytes(), b"IMG")
        self.assertEqual((self.racine / "aespa/LEMONADE/folder.jpg").read_bytes(), b"IMG")        # la vraie pochette, pas le poster du clip

    def test_image_indisponible_n_empeche_pas_le_rangement(self):
        with mock.patch.object(rangement.requests, "get", return_value=mock.Mock(status_code=404, content=b"")):
            r = rangement.executer(self._plan())
        self.assertEqual(r["erreurs"], [])
        self.assertTrue((self.racine / "aespa/LEMONADE/aespa - LEMONADE - LEMONADE.mkv").exists())
        self.assertTrue(any("indisponible" in m for m in r["ignores"]))

    def test_prevenir_emby(self):
        emby = mock.Mock(configured=True)
        self.assertIn("prévenu", rangement.prevenir_emby(emby, "/x/aespa"))
        emby._request.assert_called_once()
        self.assertEqual(emby._request.call_args.kwargs["json"], {"Updates": [{"Path": "/x/aespa", "UpdateType": "Created"}]})
        self.assertIn("pas configuré", rangement.prevenir_emby(mock.Mock(configured=False), "/x"))
        emby2 = mock.Mock(configured=True)
        emby2._request.side_effect = RuntimeError("Emby injoignable")
        self.assertIn("pas pu être prévenu", rangement.prevenir_emby(emby2, "/x"))


class FanartTest(unittest.TestCase):
    def test_sans_cle(self):
        with mock.patch.dict(os.environ, {"FANART_API_KEY": ""}):
            self.assertEqual(sources.images_fanart("x"), {"cle": False, "images": {}, "albums": {}})

    def test_choix_de_la_plus_aimee(self):
        d = {"artistthumb": [{"url": "t1", "likes": "2"}, {"url": "t2", "likes": "9"}], "hdmusiclogo": [{"url": "logo", "likes": "1"}],
             "musiclogo": [{"url": "vieux", "likes": "50"}], "artistbackground": [{"url": "f1", "likes": "1"}, {"url": "f2", "likes": "5"}, {"url": "f3", "likes": "3"}],
             "albums": {"rg1": {"cdart": [{"url": "cd"}], "albumcover": [{"url": "cov"}]}, "rg2": {}}}
        with mock.patch.dict(os.environ, {"FANART_API_KEY": "k" * 32}), mock.patch.object(sources, "_get", return_value=d):
            r = sources.images_fanart("x")
        self.assertEqual(r["images"], {"vignette": "t2", "logo": "logo", "fanart": "f2", "fanart2": "f3", "fanart3": "f1"})
        self.assertEqual(r["albums"], {"rg1": {"cdart": "cd", "couverture": "cov"}})

    def test_artiste_inconnu(self):
        with mock.patch.dict(os.environ, {"FANART_API_KEY": "k" * 32}), mock.patch.object(sources, "_get", return_value=None):
            self.assertEqual(sources.images_fanart("x"), {"cle": True, "images": {}, "albums": {}})


class FileDeTravauxTest(unittest.TestCase):
    """Un clip complet de bout en bout avec un faux YouTube : préparé en dossier de travail, rangé seulement sur ordre."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.racine = self.tmp / "Video clip"
        self.racine.mkdir()
        self.emby = mock.Mock(configured=True)
        travaux.TRAVAUX.clear()
        travaux.configurer(self.tmp / "data", lambda: str(self.racine), lambda: 89.0, lambda: "192k", self.emby)
        self.ctx = {"nom": "aespa", "mbid": "b51c672b-0000-0000-0000-000000000000", "fiche": {"nom": "aespa", "mbid": "b51c672b", "pays": "KR", "debut": "2020"},
                    "dossier_groupe": "", "images": {}, "biographie": "Bio", "genre": "K-Pop", "images_album": {}}

        def faux_telechargement(video_id, dossier, progression=None, ydl_cls=None):
            dossier = Path(dossier)
            dossier.mkdir(parents=True, exist_ok=True)
            faire_video(dossier / "source.mkv", db_volume=-4)
            return {"fichier": str(dossier / "source.mkv"), "vignette": "", "titre": "aespa 'LEMONADE' MV", "chaine": "SMTOWN", "duree": 3}
        self.p1 = mock.patch.object(travaux.telechargement, "telecharger", side_effect=faux_telechargement)
        self.p1.start()

    def tearDown(self):
        self.p1.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _preparer(self, video_id="abcdefghijk"):
        c = travaux.ajouter(self.ctx, {"titre": "LEMONADE", "album": "LEMONADE", "annee": "2026", "mbid": "m1", "video_id": video_id})
        travaux._preparer(travaux.TRAVAUX[c["id"]])
        return travaux.TRAVAUX[c["id"]]

    def test_preparation_ne_touche_pas_video_clip_puis_rangement_sur_ordre(self):
        c = self._preparer()
        self.assertEqual(c["etat"], "pret", c["message"])
        self.assertEqual(list(self.racine.iterdir()), [])                       # rien dans « Video clip » avant l'accord
        self.assertAlmostEqual(c["audio"]["apres"], 89.0, delta=0.7)
        self.assertTrue(any(l["chemin"].endswith("aespa - LEMONADE - LEMONADE.mkv") for l in c["plan"]))
        r = travaux.ranger(c["id"])
        self.assertTrue(r["ok"], r)
        self.assertTrue((self.racine / "aespa/LEMONADE/aespa - LEMONADE - LEMONADE.mkv").exists())
        nfo_texte = (self.racine / "aespa/LEMONADE/aespa - LEMONADE - LEMONADE.nfo").read_text()
        self.assertIn("<height>180</height>", nfo_texte)
        self.assertTrue((self.racine / "aespa/artist.nfo").exists())
        self.assertEqual(travaux.TRAVAUX[c["id"]]["etat"], "range")
        self.emby._request.assert_called_once()
        self.assertFalse((self.tmp / "data/travail" / c["id"]).exists())         # dossier de travail nettoyé

    def test_ranger_un_clip_qui_n_est_pas_pret_est_refuse(self):
        c = travaux.ajouter(self.ctx, {"titre": "X", "album": "X", "video_id": "abcdefghijk"})
        self.assertFalse(travaux.ranger(c["id"])["ok"])
        self.assertEqual(list(self.racine.iterdir()), [])

    def test_partage_non_monte_rien_n_est_ecrit(self):
        c = self._preparer()
        shutil.rmtree(self.racine)
        r = travaux.ranger(c["id"])
        self.assertFalse(r["ok"])
        self.assertIn("introuvable", r["error"])
        self.assertFalse(self.racine.exists())                                 # le dossier absent n'est pas recréé
        self.assertEqual(travaux.TRAVAUX[c["id"]]["etat"], "pret")             # le clip reste prêt, rien n'est perdu

    def test_sans_video_officielle_sure_le_clip_demande_un_choix(self):
        with mock.patch.object(travaux.formats, "candidats", return_value=[{"id": "zzzzzzzzzzz", "officielle": False}]):
            c = travaux.ajouter(self.ctx, {"titre": "Obscur", "album": "Obscur"})
            travaux._preparer(travaux.TRAVAUX[c["id"]])
        self.assertEqual(travaux.TRAVAUX[c["id"]]["etat"], "erreur")
        self.assertIn("Voir le clip", travaux.TRAVAUX[c["id"]]["message"])

    def test_meme_clip_pas_deux_fois_et_etat_survit_au_redemarrage(self):
        a = travaux.ajouter(self.ctx, {"titre": "LEMONADE", "album": "L", "video_id": "abcdefghijk"})
        b = travaux.ajouter(self.ctx, {"titre": "LEMONADE", "album": "L", "video_id": "abcdefghijk"})
        self.assertEqual(a["id"], b["id"])
        travaux.TRAVAUX[a["id"]]["etat"] = "telechargement"
        travaux._sauver()
        travaux._charger()
        self.assertEqual(travaux.TRAVAUX[a["id"]]["etat"], "erreur")           # interrompu : signalé, pas perdu en silence
        self.assertIn("redémarrage", travaux.TRAVAUX[a["id"]]["message"])


if __name__ == "__main__":
    unittest.main()
