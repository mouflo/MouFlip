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

from src import audio, convention, images_perso, nfo, rangement, sources, telechargement, travaux  # noqa: E402


def setUpModule():
    travaux.couvertures_fn = None            # jamais de vraie requête (Cover Art Archive, iTunes) dans ces tests, même si l'appli a été importée avant


def png_octets(largeur=600, hauteur=600, couleur=(200, 30, 30, 255), mode="RGBA", fmt="PNG"):
    import io
    from PIL import Image
    tampon = io.BytesIO()
    Image.new(mode, (largeur, hauteur), couleur if mode == "RGBA" else couleur[:3]).save(tampon, fmt)
    return tampon.getvalue()


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
        r = audio.normaliser(src, self.tmp / "sortie.mkv", 89.0, sans_perte=False)         # réencodage (méthode de secours)
        self.assertTrue(r["retouche"])
        self.assertAlmostEqual(r["apres"], 89.0, delta=0.8)
        self.assertNotAlmostEqual(avant, 89.0, delta=2.0)                       # le clip d'origine n'était pas à la cible
        s = telechargement.sonder(self.tmp / "sortie.mkv")
        self.assertEqual((s["video"]["codec"], s["audio"]["codec"]), ("h264", "aac"))
        self.assertEqual((s["video"]["largeur"], s["video"]["hauteur"]), (320, 180))
        self.assertTrue(src.exists())                                          # l'original n'est pas touché

    def test_debit_audio_reel_dans_le_nfo(self):
        src = self.tmp / "d.mkv"
        faire_video(src)                                                       # son AAC 128k
        s = telechargement.sonder(src)
        self.assertTrue(100_000 <= s["audio"]["debit"] <= 150_000, s["audio"])  # le vrai débit, pas celui du réglage d'encodage

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


@unittest.skipUnless(audio.aacgain_present(), "AACGain non installé (bash install-aacgain.sh)")
class SansPerteTest(unittest.TestCase):
    """Réglage du volume SANS réencodage (AACGain) : la compression du son n'est pas touchée, seul son niveau change (paliers de 1,5 dB)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _paquets_audio(p):
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=size", "-of", "csv=p=0", str(p)],
                             capture_output=True, text=True).stdout.split()
        return len(out), sum(int(x.rstrip(",")) for x in out if x.rstrip(",").isdigit())

    def test_niveau_a_moins_de_un_palier_de_la_cible_sans_toucher_au_son(self):
        src = self.tmp / "fort.mkv"
        faire_video(src, db_volume=-3)
        r = audio.normaliser(src, self.tmp / "sortie.mkv", 89.0)
        self.assertEqual(r["methode"], "sans perte (AACGain)")
        self.assertTrue(r["retouche"])
        self.assertLessEqual(abs(r["apres"] - 89.0), 0.8)                       # au pire un demi-palier de 1,5 dB
        n_avant, _ = self._paquets_audio(src)
        n_apres, _ = self._paquets_audio(self.tmp / "sortie.mkv")
        self.assertEqual(n_avant, n_apres)                                     # mêmes paquets audio : rien n'est réencodé
        s = telechargement.sonder(self.tmp / "sortie.mkv")
        self.assertEqual((s["video"]["codec"], s["audio"]["codec"]), ("h264", "aac"))

    def test_gain_arrondi_au_palier_et_original_intact(self):
        src = self.tmp / "a.mkv"
        faire_video(src, db_volume=-3)
        avant, _ = audio.mesurer(src)
        r = audio.normaliser(src, self.tmp / "b.mkv", avant + 4.4)               # 4,4 dB demandés -> 3 paliers = 4,5 dB
        self.assertEqual(r["gain"], 4.5)
        self.assertAlmostEqual(audio.mesurer(self.tmp / "b.mkv")[0], avant + 4.5, delta=0.15)
        self.assertAlmostEqual(audio.mesurer(src)[0], avant, delta=0.01)       # le fichier de départ n'est pas modifié

    def test_deja_a_un_demi_palier_rien_n_est_retouche(self):
        src = self.tmp / "c.mkv"
        faire_video(src)
        niveau, _ = audio.mesurer(src)
        r = audio.normaliser(src, self.tmp / "d.mkv", niveau + 0.6)
        self.assertFalse(r["retouche"])

    def test_pas_de_saturation_en_montant_le_volume(self):
        src = self.tmp / "e.mkv"
        faire_video(src, db_volume=-1)                                         # crête déjà haute
        r = audio.normaliser(src, self.tmp / "f.mkv", 99.0)                      # on demande bien plus fort que possible
        _, crete = audio.mesurer(self.tmp / "f.mkv")
        self.assertLess(crete, 1.02)

    def test_son_qui_n_est_pas_de_l_aac_se_rabat_sur_le_reencodage(self):
        src = self.tmp / "opus.mkv"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x90:rate=10", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=2:sample_rate=48000", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libopus", "-shortest", str(src)],
                       check=True, capture_output=True)
        r = audio.normaliser(src, self.tmp / "o.mkv", 89.0)
        self.assertTrue(r["methode"].startswith("réencodé"))
        self.assertAlmostEqual(r["apres"], 89.0, delta=0.8)

    def test_sans_aacgain_ou_option_coupee_le_son_est_refait(self):
        src = self.tmp / "g.mkv"
        faire_video(src, db_volume=-3)
        self.assertTrue(audio.normaliser(src, self.tmp / "h.mkv", 89.0, sans_perte=False)["methode"].startswith("réencodé"))
        with mock.patch.object(audio, "AACGAIN", self.tmp / "absent"):
            self.assertTrue(audio.normaliser(src, self.tmp / "i.mkv", 89.0)["methode"].startswith("réencodé"))


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


class FicheTest(unittest.TestCase):
    """La fiche : ce qui sera visible dans Emby, avec choix des images. Faux YouTube, faux dossier « Video clip »."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.racine = self.tmp / "Video clip"
        self.racine.mkdir()
        self.emby = mock.Mock(configured=True)
        travaux.TRAVAUX.clear()
        travaux.configurer(self.tmp / "data", lambda: str(self.racine), lambda: 89.0, lambda: "192k", self.emby)
        cands = {"vignette": [{"url": "http://i/t1.jpg", "source": "fanart.tv"}, {"url": "http://i/t2.jpg", "source": "TheAudioDB"}],
                 "banniere": [{"url": "http://i/b1.jpg", "source": "fanart.tv"}], "logo": [{"url": "http://i/l1.png", "source": "fanart.tv"}, {"url": "http://i/l2.png", "source": "fanart.tv"}],
                 "fanart": [{"url": f"http://i/f{i}.jpg", "source": "fanart.tv"} for i in range(1, 7)]}
        self.ctx = {"nom": "aespa", "mbid": "b51c672b-0000-0000-0000-000000000000", "fiche": {"nom": "aespa", "mbid": "b51c672b", "pays": "KR", "debut": "2020"},
                    "dossier_groupe": "", "images": {}, "biographie": "Bio d'origine", "biographie_langue": "fr", "genre": "K-Pop", "candidats": cands,
                    "images_album": {"rg1": {"couverture": "http://i/c1.jpg", "couverture_liste": ["http://i/c1.jpg", "http://i/c2.jpg"], "cdart": "http://i/cd1.png", "cdart_liste": ["http://i/cd1.png"]}}}

        def faux_telechargement(video_id, dossier, progression=None, ydl_cls=None, format_id=""):
            dossier = Path(dossier)
            dossier.mkdir(parents=True, exist_ok=True)
            faire_video(dossier / "source.mkv", db_volume=-4, duree=6)
            return {"fichier": str(dossier / "source.mkv"), "vignette": "", "titre": "aespa 'LEMONADE' MV", "chaine": "SMTOWN", "duree": 6}
        self.p = mock.patch.object(travaux.telechargement, "telecharger", side_effect=faux_telechargement)
        self.p.start()
        c = travaux.ajouter(self.ctx, {"titre": "LEMONADE", "album": "LEMONADE", "annee": "2026", "mbid": "m1", "rg": "rg1", "video_id": "abcdefghijk"})
        travaux._preparer(travaux.TRAVAUX[c["id"]])
        self.tid = c["id"]

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_la_fiche_montre_tout(self):
        f = travaux.fiche(self.tid)
        self.assertEqual(f["etat"], "pret")
        self.assertEqual((f["clip"]["titre"], f["clip"]["album"], f["clip"]["annee"], f["clip"]["video"]["resolution"]), ("LEMONADE", "LEMONADE", "2026", "180p"))
        self.assertIn("<musicvideo>", f["clip"]["nfo"])
        self.assertTrue(f["clip"]["destination"].endswith("aespa - LEMONADE - LEMONADE.mkv"))
        self.assertGreaterEqual(len(f["clip"]["posters"]), 3)                     # vignette (si dispo) + images prises dans la vidéo
        self.assertEqual(f["groupe"]["biographie"], "Bio d'origine")
        self.assertEqual([x["url"] for x in f["groupe"]["images"]["logo"]["candidats"]], ["http://i/l1.png", "http://i/l2.png"])
        self.assertEqual(len(f["groupe"]["fanart"]["candidats"]), 6)
        self.assertEqual(f["groupe"]["fanart"]["libres"], 4)
        self.assertEqual([x["url"] for x in f["album"]["couverture"]["candidats"]], ["http://i/c1.jpg", "http://i/c2.jpg"])

    def test_par_defaut_la_premiere_image_de_chaque_type_et_quatre_fonds(self):
        plan = {l.get("cle"): l for l in travaux.construire_plan(travaux.TRAVAUX[self.tid]) if l.get("cle")}
        self.assertEqual(plan["logo"]["url"], "http://i/l1.png")
        self.assertEqual(plan["vignette"]["url"], "http://i/t1.jpg")
        self.assertEqual([plan[k]["url"] for k in ("fanart", "fanart2", "fanart3", "fanart4")], [f"http://i/f{i}.jpg" for i in range(1, 5)])
        self.assertEqual(plan["album_couverture"]["url"], "http://i/c1.jpg")

    def test_choix_d_images_pris_en_compte(self):
        f, err = travaux.enregistrer_choix(self.tid, {"images": {"logo": "http://i/l2.png", "banniere": "", "fanart": ["http://i/f6.jpg", "http://i/f2.jpg"]},
                                                       "album": {"couverture": "http://i/c2.jpg"}, "poster": "f35", "biographie": "Ma bio"})
        self.assertEqual(err, "")
        plan = {l.get("cle"): l for l in travaux.construire_plan(travaux.TRAVAUX[self.tid]) if l.get("cle")}
        self.assertEqual(plan["logo"]["url"], "http://i/l2.png")
        self.assertNotIn("banniere", plan)                                        # « aucune » : rien n'est récupéré
        self.assertEqual((plan["fanart"]["url"], plan["fanart2"]["url"]), ("http://i/f6.jpg", "http://i/f2.jpg"))
        self.assertNotIn("fanart3", plan)
        self.assertEqual(plan["album_couverture"]["url"], "http://i/c2.jpg")
        self.assertIn("Ma bio", travaux._nfo_artiste(travaux.TRAVAUX[self.tid]))
        self.assertTrue(travaux._chemin_poster(travaux.TRAVAUX[self.tid]).endswith("image_35.jpg"))
        self.assertEqual(f["groupe"]["biographie"], "Ma bio")

    def test_image_non_proposee_refusee(self):
        f, err = travaux.enregistrer_choix(self.tid, {"images": {"logo": "http://pirate/x.png"}})
        self.assertIsNone(f)
        self.assertIn("non proposée", err)
        self.assertIsNone(travaux.enregistrer_choix(self.tid, {"images": {"fanart": ["http://i/f1.jpg"] * 5}})[0])      # 4 fonds au plus
        self.assertIsNone(travaux.enregistrer_choix(self.tid, {"poster": "inconnu"})[0])

    def test_image_deja_la_gardee_sauf_choix_explicite_et_sauvegardee(self):
        g = self.racine / "aespa"
        g.mkdir()
        (g / "logo.png").write_bytes(b"ANCIEN")
        (g / "fanart.jpg").write_bytes(b"FOND")
        self.ctx["dossier_groupe"] = str(g)
        travaux.TRAVAUX[self.tid]["contexte"]["dossier_groupe"] = str(g)
        plan = {l.get("cle"): l for l in travaux.construire_plan(travaux.TRAVAUX[self.tid]) if l.get("cle")}
        self.assertFalse(plan["logo"]["remplacer"])                               # par défaut : l'existant est gardé
        self.assertNotIn("fanart", plan)                                         # fond déjà là : jamais remplacé
        self.assertEqual(plan["fanart2"]["url"], "http://i/f1.jpg")               # les propositions remplissent seulement les places libres
        travaux.enregistrer_choix(self.tid, {"images": {"logo": "http://i/l2.png"}})
        plan = {l.get("cle"): l for l in travaux.construire_plan(travaux.TRAVAUX[self.tid]) if l.get("cle")}
        self.assertTrue(plan["logo"]["remplacer"])                                # choix explicite : remplacement
        faux = mock.Mock(status_code=200, content=b"NOUVEAU")
        with mock.patch.object(rangement.requests, "get", return_value=faux):
            r = travaux.ranger(self.tid)
        self.assertTrue(r["ok"], r)
        self.assertEqual((g / "logo.png").read_bytes(), b"NOUVEAU")
        self.assertEqual((g / "fanart.jpg").read_bytes(), b"FOND")
        sauv = list((self.tmp / "data" / "sauvegardes").rglob("*logo.png"))
        self.assertEqual(len(sauv), 1)
        self.assertEqual(sauv[0].read_bytes(), b"ANCIEN")                         # l'ancien logo est sauvegardé

    def test_remplacement_echoue_l_ancienne_image_reste(self):
        g = self.racine / "aespa"
        g.mkdir()
        (g / "logo.png").write_bytes(b"ANCIEN")
        travaux.TRAVAUX[self.tid]["contexte"]["dossier_groupe"] = str(g)
        travaux.enregistrer_choix(self.tid, {"images": {"logo": "http://i/l2.png"}})
        with mock.patch.object(rangement.requests, "get", return_value=mock.Mock(status_code=404, content=b"")):
            r = travaux.ranger(self.tid)
        self.assertTrue(r["ok"])
        self.assertEqual((g / "logo.png").read_bytes(), b"ANCIEN")                # nouvelle introuvable : l'ancienne n'est pas touchée
        self.assertTrue(any("gardée" in m for m in r["resultat"]["ignores"]))

    def test_les_choix_du_groupe_sont_reutilises_pour_le_clip_suivant(self):
        travaux.enregistrer_choix(self.tid, {"images": {"logo": "http://i/l2.png"}})
        c2 = travaux.ajouter(self.ctx, {"titre": "Supernova", "album": "Armageddon", "video_id": "abcdefghijk"})
        self.assertEqual(travaux.TRAVAUX[c2["id"]]["choix"]["images"]["logo"], "http://i/l2.png")
        self.assertIsNone(travaux.TRAVAUX[c2["id"]]["choix"]["biographie"])        # la biographie et le poster se choisissent clip par clip

    def test_clip_lance_sans_identifiant_d_album_retrouve_sa_pochette(self):
        """Bug vu : la page ne transmettait pas l'identifiant de l'album, donc aucune pochette n'était proposée."""
        c = travaux.TRAVAUX[self.tid]
        c["rg"] = ""
        travaux.rg_fn = lambda ctx, titre, album: "rg1" if titre == "LEMONADE" else ""
        try:
            f = travaux.fiche(self.tid)
        finally:
            travaux.rg_fn = None
        self.assertEqual(c["rg"], "rg1")                                         # retrouvé et gardé
        self.assertEqual([x["url"] for x in f["album"]["couverture"]["candidats"]], ["http://i/c1.jpg", "http://i/c2.jpg"])
        self.assertEqual({l.get("cle"): l for l in f["plan"]}["album_couverture"]["url"], "http://i/c1.jpg")

    def test_les_choix_d_album_ne_sont_pas_retenus_pour_tout_le_groupe(self):
        travaux.enregistrer_choix(self.tid, {"album": {"couverture": ""}})        # « Aucune pochette » pour CET album
        c2 = travaux.ajouter(self.ctx, {"titre": "Supernova", "album": "Armageddon", "video_id": "abcdefghijk"})
        self.assertEqual(travaux.TRAVAUX[c2["id"]]["choix"]["album"], {"couverture": None, "cdart": None})   # un autre album repart des propositions

    def test_ancien_choix_aucune_pochette_oublie_quand_l_album_est_retrouve(self):
        c = travaux.TRAVAUX[self.tid]
        c["rg"] = ""
        c["choix"]["album"] = {"couverture": "", "cdart": None}                  # choix fait quand aucune pochette n'était proposée
        travaux.rg_fn = lambda ctx, titre, album: "rg1"
        try:
            f = travaux.fiche(self.tid)
        finally:
            travaux.rg_fn = None
        self.assertIsNone(f["album"]["couverture"]["choisi"])
        self.assertEqual({l.get("cle"): l for l in f["plan"]}["album_couverture"]["url"], "http://i/c1.jpg")

    def test_album_sans_pochette_sous_le_single_prend_celle_de_l_album(self):
        c = travaux.TRAVAUX[self.tid]
        c["rg"] = "rg_single"                                                    # le single n'a pas d'image chez fanart.tv, l'album du même nom oui
        travaux.rg_fn = lambda ctx, titre, album: "rg1"
        try:
            f = travaux.fiche(self.tid)
        finally:
            travaux.rg_fn = None
        self.assertEqual(c["rg"], "rg1")
        self.assertEqual(len(f["album"]["couverture"]["candidats"]), 2)

    def test_poster_du_clip_par_defaut_c_est_la_pochette_de_l_album(self):
        """Comme dans la médiathèque de l'utilisatrice : « …-poster.jpg » est la pochette carrée de l'album, pas la vignette 16:9 du clip."""
        f = travaux.fiche(self.tid)
        self.assertEqual(f["clip"]["poster_choisi"], "pochette")
        self.assertEqual(f["clip"]["posters"][0]["id"], "pochette")
        self.assertEqual(f["clip"]["posters"][0]["apercu"], "http://i/c1.jpg")                       # la première pochette proposée, celle qui sera posée
        plan = {"/".join(Path(l["chemin"]).parts[-2:]): l for l in f["plan"] if l["type"] == "image"}      # « LEMONADE/folder.jpg » (album) ≠ « aespa/folder.jpg » (groupe)
        self.assertEqual(plan["LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg"]["url"], "http://i/c1.jpg")   # le poster du clip = la pochette
        self.assertEqual(plan["LEMONADE/folder.jpg"]["url"], "http://i/c1.jpg")                                # = l'image de l'album
        self.assertNotIn("source", plan["LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg"])                    # et pas une image du clip
        self.assertTrue(len(f["clip"]["posters"]) >= 4)                                              # les images du clip restent proposées, ensuite

    def test_la_vignette_du_clip_est_l_image_paysage_a_cote_de_la_pochette(self):
        """Convention vue dans la médiathèque (Tsubasa, Vibrato) : folder.jpg = pochette, « <clip>-thumb.jpg » = vignette que Emby affiche en « Thumb »."""
        f = travaux.fiche(self.tid)
        plan = {"/".join(Path(l["chemin"]).parts[-2:]): l for l in f["plan"] if l["type"] == "image"}
        self.assertEqual(plan["LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg"]["url"], "http://i/c1.jpg")          # poster = pochette
        self.assertTrue(plan["LEMONADE/aespa - LEMONADE - LEMONADE-thumb.jpg"]["source"].endswith(".jpg"))            # vignette = image du clip
        self.assertNotIn("url", plan["LEMONADE/aespa - LEMONADE - LEMONADE-thumb.jpg"])
        self.assertEqual(f["clip"]["vignette_choisie"], f["clip"]["vignettes"][0]["id"])
        self.assertEqual(f["clip"]["vignettes"][0]["id"], "yt")                                                      # par défaut : la vignette YouTube
        f, err = travaux.enregistrer_choix(self.tid, {"vignette_clip": "f58"})
        self.assertEqual(err, "")
        plan = {"/".join(Path(l["chemin"]).parts[-2:]): l for l in f["plan"] if l["type"] == "image"}
        self.assertTrue(plan["LEMONADE/aespa - LEMONADE - LEMONADE-thumb.jpg"]["source"].endswith("image_58.jpg"))
        self.assertIsNone(travaux.enregistrer_choix(self.tid, {"vignette_clip": "inconnue"})[0])

    def test_vignette_ajoutee_a_la_main_et_rangee(self):
        images_perso.configurer(self.tmp / "data")
        f, err = travaux.ajouter_image(self.tid, "thumb", png_octets(1280, 720, mode="RGB"), "vignette.jpg")
        self.assertEqual(err, "")
        self.assertEqual(f["clip"]["vignette_choisie"], "perso1")
        with mock.patch.object(rangement.requests, "get", return_value=mock.Mock(status_code=200, content=b"IMG")):
            r = travaux.ranger(self.tid)
        self.assertTrue(r["ok"], r)
        album = self.racine / "aespa/LEMONADE"
        from PIL import Image
        self.assertEqual(Image.open(album / "aespa - LEMONADE - LEMONADE-thumb.jpg").size, (1280, 720))               # la vignette ajoutée, en paysage
        self.assertEqual((album / "aespa - LEMONADE - LEMONADE-poster.jpg").read_bytes(), (album / "folder.jpg").read_bytes())   # le poster = la pochette

    def test_sans_pochette_le_poster_est_une_image_du_clip(self):
        travaux.TRAVAUX[self.tid]["rg"] = ""
        f = travaux.fiche(self.tid)
        self.assertNotEqual(f["clip"]["poster_choisi"], "pochette")
        plan = {"/".join(Path(l["chemin"]).parts[-2:]): l for l in f["plan"] if l["type"] == "image"}
        self.assertTrue(plan["LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg"]["source"].endswith(".jpg"))   # faute de pochette : image du clip
        self.assertEqual(plan["LEMONADE/folder.jpg"]["source"], plan["LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg"]["source"])

    def test_poster_du_clip_choisi_explicitement_est_respecte(self):
        f, err = travaux.enregistrer_choix(self.tid, {"poster": "f35"})
        self.assertEqual(err, "")
        plan = {"/".join(Path(l["chemin"]).parts[-2:]): l for l in f["plan"] if l["type"] == "image"}
        self.assertTrue(plan["LEMONADE/aespa - LEMONADE - LEMONADE-poster.jpg"]["source"].endswith("image_35.jpg"))
        self.assertEqual(plan["LEMONADE/folder.jpg"]["url"], "http://i/c1.jpg")                       # la pochette de l'album reste celle du dossier
        f, _ = travaux.enregistrer_choix(self.tid, {"poster": "pochette"})
        self.assertEqual(f["clip"]["poster_choisi"], "pochette")
        self.assertIsNone(travaux.enregistrer_choix(self.tid, {"poster": "inconnu"})[0])

    def test_poster_et_folder_identiques_apres_rangement(self):
        images_perso.configurer(self.tmp / "data")
        travaux.ajouter_image(self.tid, "couverture", png_octets(900, 900), "pochette.png")
        with mock.patch.object(rangement.requests, "get", return_value=mock.Mock(status_code=200, content=b"IMG")):
            r = travaux.ranger(self.tid)
        self.assertTrue(r["ok"], r)
        album = self.racine / "aespa/LEMONADE"
        self.assertEqual((album / "aespa - LEMONADE - LEMONADE-poster.jpg").read_bytes(), (album / "folder.jpg").read_bytes())          # la même image carrée
        from PIL import Image
        self.assertEqual(Image.open(album / "aespa - LEMONADE - LEMONADE-poster.jpg").size, (900, 900))

    def test_pochette_ajoutee_a_la_main_choisie_et_rangee(self):
        """Cas « Rich Man » : aucune pochette trouvée, l'utilisatrice envoie la sienne depuis son téléphone."""
        images_perso.configurer(self.tmp / "data")
        travaux.TRAVAUX[self.tid]["rg"] = ""
        f = travaux.fiche(self.tid)
        self.assertEqual(f["album"]["couverture"]["candidats"], [])                                # aucune pochette trouvée nulle part
        f, err = travaux.ajouter_image(self.tid, "couverture", png_octets(800, 800), "ma_pochette.png")
        self.assertEqual(err, "")
        cand = f["album"]["couverture"]["candidats"][-1]
        self.assertEqual((cand["source"], cand["url"].startswith("perso:")), ("toi", True))
        self.assertTrue(cand["apercu"].startswith(f"/api/perso/{self.ctx['mbid']}/"))
        self.assertEqual(f["album"]["couverture"]["choisi"], cand["url"])                         # elle est choisie tout de suite
        ligne = {l.get("cle"): l for l in f["plan"]}["album_couverture"]
        self.assertTrue(ligne["source"].endswith(".jpg"))                                         # copiée depuis le fichier local, rien à télécharger
        with mock.patch.object(rangement.requests, "get", return_value=mock.Mock(status_code=200, content=b"IMG")):          # les AUTRES images viennent d'Internet (simulé)
            r = travaux.ranger(self.tid)
        self.assertTrue(r["ok"], r)
        pochette = self.racine / "aespa/LEMONADE/folder.jpg"
        self.assertTrue(pochette.exists())
        from PIL import Image
        self.assertEqual(Image.open(pochette).size, (800, 800))

    def test_pochette_ajoutee_remplace_l_existante_avec_sauvegarde(self):
        images_perso.configurer(self.tmp / "data")
        dossier = self.racine / "aespa" / "LEMONADE"
        dossier.mkdir(parents=True)
        (dossier / "folder.jpg").write_bytes(b"ANCIENNE")
        travaux.ajouter_image(self.tid, "couverture", png_octets(700, 700), "neuve.png")
        with mock.patch.object(rangement.requests, "get", return_value=mock.Mock(status_code=200, content=b"IMG")):
            r = travaux.ranger(self.tid)
        self.assertTrue(r["ok"], r)
        self.assertNotEqual((dossier / "folder.jpg").read_bytes(), b"ANCIENNE")
        sauv = list((self.tmp / "data" / "sauvegardes").rglob("*folder.jpg"))
        self.assertEqual([x.read_bytes() for x in sauv], [b"ANCIENNE"])                          # l'ancienne est sauvegardée avant d'être remplacée

    def test_clip_deja_range_mis_a_la_convention_avec_sauvegarde(self):
        """Les clips rangés avant la convention (poster = image 16:9 du clip) : poster → pochette, ancien poster → vignette, ancien poster sauvegardé."""
        album = self.racine / "aespa" / "LEMONADE"
        album.mkdir(parents=True)
        from PIL import Image
        Image.new("RGB", (1280, 720), (10, 50, 90)).save(album / "aespa - LEMONADE - LEMONADE-poster.jpg")     # l'ancien poster, 16:9
        Image.new("RGB", (1000, 1000), (200, 20, 20)).save(album / "folder.jpg")                                # la pochette de l'album
        (album / "aespa - LEMONADE - LEMONADE.mkv").write_bytes(b"V")
        c = travaux.TRAVAUX[self.tid]
        c.update(etat="range", plan=[{"type": "video", "chemin": str(album / "aespa - LEMONADE - LEMONADE.mkv"), "existe": False}])
        ancien = (album / "aespa - LEMONADE - LEMONADE-poster.jpg").read_bytes()
        r = travaux.mettre_a_la_convention(self.tid)
        self.assertTrue(r["ok"], r)
        self.assertEqual(Image.open(album / "aespa - LEMONADE - LEMONADE-thumb.jpg").size, (1280, 720))                # l'ancienne image devient la vignette
        self.assertEqual((album / "aespa - LEMONADE - LEMONADE-thumb.jpg").read_bytes(), ancien)
        self.assertEqual((album / "aespa - LEMONADE - LEMONADE-poster.jpg").read_bytes(), (album / "folder.jpg").read_bytes())   # poster = pochette
        sauv = list((self.tmp / "data" / "sauvegardes").rglob("*poster.jpg"))
        self.assertEqual([x.read_bytes() for x in sauv], [ancien])                                                    # l'ancien poster est sauvegardé
        self.emby._request.assert_called()                                                                            # Emby est prévenu
        self.assertIn("Déjà à la convention", travaux.mettre_a_la_convention(self.tid)["message"])                  # sans effet la 2e fois

    def test_mise_a_la_convention_de_tous_les_clips_ranges(self):
        from PIL import Image
        res = {}
        for nom, carre in (("A", True), ("B", False)):
            album = self.racine / "aespa" / nom
            album.mkdir(parents=True)
            Image.new("RGB", (1280, 720), (10, 50, 90)).save(album / f"aespa - {nom} - T-poster.jpg")
            Image.new("RGB", (1000, 1000) if carre else (1280, 720), (200, 20, 20)).save(album / "folder.jpg")
            (album / f"aespa - {nom} - T.mkv").write_bytes(b"V")
            c = travaux.ajouter(self.ctx, {"titre": f"T{nom}", "album": nom, "video_id": "abcdefghijk"})
            travaux.TRAVAUX[c["id"]].update(etat="range", plan=[{"type": "video", "chemin": str(album / f"aespa - {nom} - T.mkv"), "existe": False}])
        travaux.TRAVAUX[self.tid]["etat"] = "pret"
        r = travaux.mettre_tous_a_la_convention()
        self.assertEqual((r["total"], r["faits"], r["deja"], [x[0] for x in r["refuses"]]), (2, 1, 0, ["TB"]))      # le clip dont la « pochette » n'est pas carrée n'est pas touché

    def test_mise_a_la_convention_refusee_sans_pochette_carree_ou_clip_non_range(self):
        album = self.racine / "aespa" / "LEMONADE"
        album.mkdir(parents=True)
        from PIL import Image
        Image.new("RGB", (1280, 720), (10, 50, 90)).save(album / "aespa - LEMONADE - LEMONADE-poster.jpg")
        Image.new("RGB", (1280, 720), (1, 2, 3)).save(album / "folder.jpg")                                         # pas carrée : on ne la prend pas pour une pochette
        (album / "aespa - LEMONADE - LEMONADE.mkv").write_bytes(b"V")
        c = travaux.TRAVAUX[self.tid]
        self.assertFalse(travaux.mettre_a_la_convention(self.tid)["ok"])                                             # pas rangé
        c.update(etat="range", plan=[{"type": "video", "chemin": str(album / "aespa - LEMONADE - LEMONADE.mkv"), "existe": False}])
        r = travaux.mettre_a_la_convention(self.tid)
        self.assertFalse(r["ok"])
        self.assertIn("carrée", r["error"])
        self.assertFalse((album / "aespa - LEMONADE - LEMONADE-thumb.jpg").exists())                                 # rien n'a été modifié

    def test_les_images_ajoutees_resservent_aux_autres_clips_de_l_artiste(self):
        images_perso.configurer(self.tmp / "data")
        travaux.ajouter_image(self.tid, "logo", png_octets(600, 200), "logo.png")
        c2 = travaux.ajouter(self.ctx, {"titre": "Supernova", "album": "Armageddon", "video_id": "abcdefghijk"})
        self.assertTrue(travaux.TRAVAUX[c2["id"]]["choix"]["images"]["logo"].startswith("perso:"))        # choix du groupe réutilisé
        self.assertEqual(len([x for x in travaux._candidats(travaux.TRAVAUX[c2["id"]], "logo") if x["source"] == "toi"]), 1)
        self.assertEqual([x for x in travaux._candidats(travaux.TRAVAUX[c2["id"]], "couverture") if x["source"] == "toi"], [])      # pas de pochette d'un autre album

    def test_fond_d_ecran_ajoute_prend_la_premiere_place_libre(self):
        images_perso.configurer(self.tmp / "data")
        f, err = travaux.ajouter_image(self.tid, "fanart", png_octets(1280, 720, mode="RGB"), "fond.jpg")
        self.assertEqual(err, "")
        choisis = f["groupe"]["fanart"]["choisis"]
        self.assertTrue(choisis[0].startswith("perso:"))
        self.assertEqual(len(choisis), 4)                                                         # 4 places libres : la nouvelle + 3 propositions
        plan = {l.get("cle"): l for l in f["plan"]}
        self.assertTrue(plan["fanart"]["source"].endswith(".jpg"))

    def test_fond_d_ecran_refuse_quand_les_places_sont_prises(self):
        images_perso.configurer(self.tmp / "data")
        g = self.racine / "aespa"
        (g / "extrafanart").mkdir(parents=True)
        for nom in ("fanart.jpg", "extrafanart/fanart1.jpg", "extrafanart/fanart2.jpg", "extrafanart/fanart3.jpg"):
            (g / nom).write_bytes(b"x")
        travaux.TRAVAUX[self.tid]["contexte"]["dossier_groupe"] = str(g)
        f, err = travaux.ajouter_image(self.tid, "fanart", png_octets(1280, 720, mode="RGB"), "fond.jpg")
        self.assertIsNone(f)
        self.assertIn("jamais remplacés", err)

    def test_poster_ajoute_est_propre_a_ce_clip(self):
        images_perso.configurer(self.tmp / "data")
        f, err = travaux.ajouter_image(self.tid, "poster", png_octets(1280, 720, mode="RGB"), "poster.jpg")
        self.assertEqual(err, "")
        self.assertEqual(f["clip"]["poster_choisi"], "perso1")
        self.assertIn("perso1", [p["id"] for p in f["clip"]["posters"]])
        self.assertTrue(travaux.fichier_poster(self.tid, "perso1"))
        c2 = travaux.ajouter(self.ctx, {"titre": "Autre", "album": "A", "video_id": "abcdefghijk"})
        self.assertEqual(travaux.TRAVAUX[c2["id"]].get("posters"), None)                          # un autre clip ne voit pas ce poster

    def test_image_invalide_refusee_sans_rien_changer(self):
        images_perso.configurer(self.tmp / "data")
        avant = json.dumps(travaux.TRAVAUX[self.tid]["choix"], sort_keys=True)
        f, err = travaux.ajouter_image(self.tid, "logo", b"pas une image", "x.png")
        self.assertIsNone(f)
        self.assertIn("pas une image", err)
        self.assertEqual(json.dumps(travaux.TRAVAUX[self.tid]["choix"], sort_keys=True), avant)

    def test_pochettes_d_autres_banques_proposees_et_permises(self):
        travaux.couvertures_fn = lambda rg, groupe, album: [{"url": "https://coverartarchive.org/x-1200.jpg", "source": "Cover Art Archive"},
                                                           {"url": "https://is1.mzstatic.com/y/1200x1200bb.jpg", "source": "iTunes"}]
        try:
            f = travaux.fiche(self.tid)
            sources_c = [x["source"] for x in f["album"]["couverture"]["candidats"]]
            self.assertEqual(sources_c, ["fanart.tv", "fanart.tv", "Cover Art Archive", "iTunes"])
            f2, err = travaux.enregistrer_choix(self.tid, {"album": {"couverture": "https://is1.mzstatic.com/y/1200x1200bb.jpg"}})
            self.assertEqual(err, "")                                                              # choisissable : elle a été proposée
            self.assertIsNone(travaux.enregistrer_choix(self.tid, {"album": {"couverture": "https://pirate.example/x.jpg"}})[0])
        finally:
            travaux.couvertures_fn = None

    def test_titre_album_annee_modifiables_avant_le_rangement(self):
        """Cas Awich : l'album était mal identifié (« Golden Kamuy » au lieu de « Golden Horizon »)."""
        travaux.rg_fn = lambda ctx, titre, album: "rg_horizon" if album == "Golden Horizon" else ""
        try:
            f, err = travaux.enregistrer_choix(self.tid, {"edition": {"titre": "Golden Horizon", "album": "Golden Horizon", "annee": "2024"}})
        finally:
            travaux.rg_fn = None
        self.assertEqual(err, "")
        c = travaux.TRAVAUX[self.tid]
        self.assertEqual((c["titre"], c["album"], c["annee"], c["rg"]), ("Golden Horizon", "Golden Horizon", "2024", "rg_horizon"))
        chemins = [l["chemin"] for l in f["plan"]]
        self.assertTrue(any(x.endswith("/aespa/Golden Horizon/aespa - Golden Horizon - Golden Horizon.mkv") for x in chemins))      # le dossier et le nom suivent
        self.assertFalse(any("/LEMONADE/" in x for x in chemins))
        self.assertIn("<album>Golden Horizon</album>", f["clip"]["nfo"])
        self.assertIn("<year>2024</year>", f["clip"]["nfo"])
        self.assertEqual(f["clip"]["album"], "Golden Horizon")

    def test_edition_vide_ou_invalide_refusee_sans_rien_changer(self):
        avant = (travaux.TRAVAUX[self.tid]["titre"], travaux.TRAVAUX[self.tid]["album"], travaux.TRAVAUX[self.tid]["annee"])
        for ed in ({"titre": "  "}, {"album": ""}, {"annee": "20x4"}, {"titre": "a\nb"}, {"album": "x" * 200}):
            self.assertIsNone(travaux.enregistrer_choix(self.tid, {"edition": ed})[0], ed)
        self.assertEqual((travaux.TRAVAUX[self.tid]["titre"], travaux.TRAVAUX[self.tid]["album"], travaux.TRAVAUX[self.tid]["annee"]), avant)
        f, err = travaux.enregistrer_choix(self.tid, {"edition": {"annee": ""}})                     # une année vide est permise
        self.assertEqual(err, "")

    def test_changer_d_album_repart_des_propositions_de_pochette(self):
        travaux.enregistrer_choix(self.tid, {"album": {"couverture": ""}})                          # « aucune pochette » choisi pour l'ancien album
        f, err = travaux.enregistrer_choix(self.tid, {"edition": {"album": "Autre album"}})
        self.assertEqual(err, "")
        self.assertEqual(travaux.TRAVAUX[self.tid]["choix"]["album"], {"couverture": None, "cdart": None})

    def test_fiche_d_un_clip_pas_pret(self):
        c = travaux.ajouter(self.ctx, {"titre": "Autre", "album": "Autre", "video_id": "abcdefghijk"})
        self.assertIsNone(travaux.fiche(c["id"]))


class ImagesPersoTest(unittest.TestCase):
    MB = "e7385b1f-6356-45e0-ae5e-f690f19ec508"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        images_perso.configurer(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_png_devient_jpeg_et_le_logo_reste_png_transparent(self):
        from PIL import Image
        e = images_perso.ajouter(self.MB, "couverture", png_octets(700, 700), "pochette.png", "rich man")
        self.assertTrue(e["fichier"].endswith(".jpg"))
        self.assertEqual(Image.open(images_perso.chemin(self.MB, e["id"])).format, "JPEG")
        l = images_perso.ajouter(self.MB, "logo", png_octets(600, 200, (255, 255, 255, 0)), "logo.png")
        im = Image.open(images_perso.chemin(self.MB, l["id"]))
        self.assertEqual((im.format, im.mode), ("PNG", "RGBA"))                      # le logo garde sa transparence
        self.assertEqual(im.getpixel((10, 10))[3], 0)

    def test_refus_en_clair(self):
        for octets, mot in ((b"pas une image du tout", "pas une image"), (png_octets(50, 50), "trop petite"), (b"", "vide")):
            with self.assertRaises(images_perso.ImageError) as e:
                images_perso.ajouter(self.MB, "couverture", octets)
            self.assertIn(mot, str(e.exception))
        with self.assertRaises(images_perso.ImageError):
            images_perso.ajouter(self.MB, "inconnu", png_octets())
        with self.assertRaises(images_perso.ImageError) as e:
            images_perso.ajouter(self.MB, "couverture", b"x" * (images_perso.MAX_OCTETS + 1))
        self.assertIn("trop grosse", str(e.exception))

    def test_trop_grande_image_reduite(self):
        from PIL import Image
        e = images_perso.ajouter(self.MB, "fanart", png_octets(5200, 2900, mode="RGB"))
        self.assertLessEqual(max(Image.open(images_perso.chemin(self.MB, e["id"])).size), images_perso.MAX_COTE)

    def test_pochette_propre_a_son_album_les_autres_types_a_l_artiste(self):
        a = images_perso.ajouter(self.MB, "couverture", png_octets(), "a", "rich man")
        images_perso.ajouter(self.MB, "logo", png_octets(600, 200), "l")
        self.assertEqual([e["id"] for e in images_perso.lister(self.MB, "couverture", "rich man")], [a["id"]])
        self.assertEqual(images_perso.lister(self.MB, "couverture", "autre album"), [])          # la pochette d'un album n'est pas proposée à un autre
        self.assertEqual(len(images_perso.lister(self.MB, "logo")), 1)

    def test_adresses_du_reseau_local_refusees(self):
        for url in ("http://127.0.0.1/x.png", "http://192.168.1.134:8096/emby.png", "http://10.0.0.5/a.jpg", "ftp://exemple.fr/x.png", "file:///etc/passwd", "pas une adresse"):
            with self.assertRaises(images_perso.ImageError):
                images_perso.telecharger(url)

    def test_redirection_vers_le_reseau_local_refusee(self):
        faux = mock.MagicMock(status_code=302, headers={"Location": "http://192.168.1.134:8096/secret.png"})
        faux.__enter__.return_value = faux
        with mock.patch.object(images_perso, "_hote_public", side_effect=[None, images_perso.ImageError("réseau local")]), \
                mock.patch.object(images_perso.requests, "get", return_value=faux):
            with self.assertRaises(images_perso.ImageError):
                images_perso.telecharger("https://exemple.fr/redirige.png")

    def test_telechargement_d_une_adresse_publique(self):
        faux = mock.MagicMock(status_code=200)
        faux.__enter__.return_value = faux
        faux.iter_content.return_value = [png_octets(300, 300)]
        with mock.patch.object(images_perso, "_hote_public"), mock.patch.object(images_perso.requests, "get", return_value=faux):
            octets, nom = images_perso.telecharger("https://exemple.fr/dossier/pochette.png")
        self.assertEqual(nom, "pochette.png")
        self.assertTrue(octets.startswith(b"\x89PNG"))


class ConventionTest(unittest.TestCase):
    """Clips déjà rangés : poster = pochette carrée de l'album, ancienne image 16:9 = vignette « -thumb.jpg », directement sur les dossiers."""

    def setUp(self):
        from PIL import Image
        self.Image = Image
        self.tmp = Path(tempfile.mkdtemp())
        self.groupe = self.tmp / "aespa"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _album(self, nom, titre="T", poster=(1280, 720), folder=(1000, 1000), couleur_poster=(10, 50, 90)):
        a = self.groupe / nom
        a.mkdir(parents=True, exist_ok=True)
        (a / f"aespa - {nom} - {titre}.mkv").write_bytes(b"V")
        if poster:
            self.Image.new("RGB", poster, couleur_poster).save(a / f"aespa - {nom} - {titre}-poster.jpg")
        if folder:
            self.Image.new("RGB", folder, (200, 20, 20)).save(a / "folder.jpg")
        return a

    def test_analyse_en_lecture_seule_et_selection_des_clips(self):
        self._album("A")                                                           # 16:9 + pochette carrée : à faire
        self._album("B", folder=(1280, 720))                                       # pas de pochette carrée : jamais touché
        self._album("C", folder=None)                                              # pas de pochette : rien à faire
        a = self._album("D", poster=(1000, 1000))                                  # poster carré différent de la pochette : à remplacer, pas de vignette à créer
        (self.groupe / "@eaDir" / "x").mkdir(parents=True)
        avant = sorted(str(p) for p in self.groupe.rglob("*"))
        lot = convention.analyser(self.groupe)
        self.assertEqual(sorted((e["album"], e["vignette"], e["remplacer"]) for e in lot), [("A", True, True), ("D", False, True)])
        self.assertEqual(sorted(str(p) for p in self.groupe.rglob("*")), avant)    # l'analyse ne modifie rien
        self.assertEqual(convention.analyser(self.tmp / "inexistant"), [])

    def test_application_avec_sauvegarde_et_idempotence(self):
        a = self._album("A")
        ancien = (a / "aespa - A - T-poster.jpg").read_bytes()
        r = convention.appliquer(self.groupe, self.tmp / "sauv")
        self.assertEqual((r["total"], len(r["faits"]), r["erreurs"]), (1, 1, []))
        self.assertEqual((a / "aespa - A - T-thumb.jpg").read_bytes(), ancien)                               # l'ancienne image devient la vignette
        self.assertEqual((a / "aespa - A - T-poster.jpg").read_bytes(), (a / "folder.jpg").read_bytes())      # poster = pochette
        self.assertEqual([x.read_bytes() for x in (self.tmp / "sauv").rglob("*poster.jpg")], [ancien])        # ancien poster sauvegardé
        self.assertEqual(self.Image.open(a / "aespa - A - T-thumb.jpg").size, (1280, 720))
        self.assertEqual(convention.appliquer(self.groupe, self.tmp / "sauv")["total"], 0)                    # 2e passage : plus rien à faire

    def test_une_vignette_deja_la_n_est_jamais_ecrasee(self):
        a = self._album("A")
        (a / "aespa - A - T-thumb.jpg").write_bytes(b"MA VIGNETTE")
        convention.appliquer(self.groupe, self.tmp / "sauv")
        self.assertEqual((a / "aespa - A - T-thumb.jpg").read_bytes(), b"MA VIGNETTE")                       # la vignette existante est respectée
        self.assertEqual((a / "aespa - A - T-poster.jpg").read_bytes(), (a / "folder.jpg").read_bytes())


class QualitesTest(unittest.TestCase):
    def test_format_demande(self):
        self.assertEqual(telechargement.format_demande(""), "bv*+ba[acodec^=mp4a]/bv*+ba/b")                # sans choix : la meilleure
        self.assertEqual(telechargement.format_demande("271"), "271+ba[acodec^=mp4a]/271+ba")              # choix : cette image, jamais autre chose en silence
        self.assertEqual(telechargement.format_demande("271; rm -rf /"), "bv*+ba[acodec^=mp4a]/bv*+ba/b")  # identifiant douteux : ignoré

    def test_options_utilisent_le_format_choisi(self):
        self.assertIn("137+ba", telechargement.options(Path("/tmp/x"), None, "137")["format"])


class CouverturesExternesTest(unittest.TestCase):
    def test_cover_art_archive_et_itunes(self):
        def faux(url, params=None, max_essais=5):
            if "coverartarchive" in url:
                return {"images": [{"front": False, "types": ["Back"], "image": "http://x/back.jpg"},
                                   {"front": True, "types": ["Front"], "image": "http://x/front.jpg", "thumbnails": {"1200": "http://x/front-1200.jpg"}}]}
            return {"results": [{"collectionName": "Rich Man - The 6th Mini Album - EP", "artistName": "aespa", "artworkUrl100": "https://m/a/100x100bb.jpg"},
                                {"collectionName": "Next Level - Single", "artistName": "aespa", "artworkUrl100": "https://m/b/100x100bb.jpg"},
                                {"collectionName": "Rich Man - Tribute", "artistName": "Un autre", "artworkUrl100": "https://m/c/100x100bb.jpg"}]}
        with mock.patch.object(sources, "_get", side_effect=faux):
            r = sources.couvertures_externes("rg1", "aespa", "Rich Man")
        self.assertEqual([(x["source"], x["url"]) for x in r], [("Cover Art Archive", "https://x/front-1200.jpg"), ("iTunes", "https://m/a/1200x1200bb.jpg")])

    def test_une_panne_n_empeche_rien(self):
        with mock.patch.object(sources, "_get", side_effect=sources.SourceIndisponible("down")):
            self.assertEqual(sources.couvertures_externes("rg1", "aespa", "Rich Man"), [])


class FanartTest(unittest.TestCase):
    def test_sans_cle(self):
        with mock.patch.dict(os.environ, {"FANART_API_KEY": ""}):
            self.assertEqual(sources.images_fanart("x"), {"cle": False, "images": {}, "listes": {}, "albums": {}})

    def test_choix_de_la_plus_aimee(self):
        d = {"artistthumb": [{"url": "t1", "likes": "2"}, {"url": "t2", "likes": "9"}], "hdmusiclogo": [{"url": "logo", "likes": "1"}],
             "musiclogo": [{"url": "vieux", "likes": "50"}], "artistbackground": [{"url": "f1", "likes": "1"}, {"url": "f2", "likes": "5"}, {"url": "f3", "likes": "3"}],
             "albums": {"rg1": {"cdart": [{"url": "cd"}], "albumcover": [{"url": "cov"}]}, "rg2": {}}}
        with mock.patch.dict(os.environ, {"FANART_API_KEY": "k" * 32}), mock.patch.object(sources, "_get", return_value=d):
            r = sources.images_fanart("x")
        self.assertEqual(r["images"], {"vignette": "t2", "logo": "logo", "fanart": "f2", "fanart2": "f3", "fanart3": "f1"})
        self.assertEqual(r["albums"], {"rg1": {"cdart": "cd", "couverture": "cov", "cdart_liste": ["cd"], "couverture_liste": ["cov"]}})
        self.assertEqual(r["listes"]["vignette"], ["t2", "t1"])                  # toutes les images, la plus aimée d'abord
        self.assertEqual(r["listes"]["logo"], ["logo", "vieux"])                  # logo HD avant le logo ordinaire
        self.assertEqual(r["listes"]["fanart"], ["f2", "f3", "f1"])

    def test_candidats_fusionnes_sans_doublon(self):
        audiodb = {"images": {"vignette": "t2", "logo": "autre", "fanart": "f9", "fanart2": "f2"}}
        fanart = {"listes": {"vignette": ["t2", "t1"], "logo": [], "banniere": [], "fanart": ["f2", "f3"]}}
        c = sources.candidats_images(audiodb, fanart)
        self.assertEqual([(x["url"], x["source"]) for x in c["vignette"]], [("t2", "fanart.tv"), ("t1", "fanart.tv")])
        self.assertEqual([(x["url"], x["source"]) for x in c["logo"]], [("autre", "TheAudioDB")])
        self.assertEqual([x["url"] for x in c["fanart"]], ["f2", "f3", "f9"])      # f2 vu chez fanart.tv : pas répété

    def test_artiste_inconnu(self):
        with mock.patch.dict(os.environ, {"FANART_API_KEY": "k" * 32}), mock.patch.object(sources, "_get", return_value=None):
            self.assertEqual(sources.images_fanart("x"), {"cle": True, "images": {}, "listes": {}, "albums": {}})


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

        def faux_telechargement(video_id, dossier, progression=None, ydl_cls=None, format_id=""):
            self.demande = (video_id, format_id)
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

    def test_la_qualite_choisie_est_celle_qui_est_demandee(self):
        c = travaux.ajouter(self.ctx, {"titre": "LEMONADE", "album": "L", "video_id": "abcdefghijk", "format_id": "271", "qualite": "1440p vp9"})
        travaux._preparer(travaux.TRAVAUX[c["id"]])
        self.assertEqual(self.demande, ("abcdefghijk", "271"))
        self.assertEqual(travaux.TRAVAUX[c["id"]]["qualite"], "1440p vp9")

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
        travaux._charger()                                                       # simple lecture (import de l'appli, tests…) : rien n'est modifié
        self.assertEqual(travaux.TRAVAUX[a["id"]]["etat"], "telechargement")
        travaux._charger(reprise=True)                                           # vrai démarrage du service : le clip REPREND tout seul
        self.assertEqual(travaux.TRAVAUX[a["id"]]["etat"], "attente")
        self.assertIn("Reprise", travaux.TRAVAUX[a["id"]]["message"])
        for _ in range(2):                                                       # …mais pas en boucle si l'appli retombe à chaque fois
            travaux.TRAVAUX[a["id"]]["etat"] = "reduction"
            travaux._sauver()
            travaux._charger(reprise=True)
        self.assertEqual(travaux.TRAVAUX[a["id"]]["etat"], "erreur")
        self.assertIn("plusieurs fois", travaux.TRAVAUX[a["id"]]["message"])

    def test_lire_l_etat_sans_ecriture_ne_touche_pas_au_fichier_du_service(self):
        """Bug vu : importer l'appli (les tests) réécrivait le vrai fichier d'état et marquait comme interrompus les clips que le service traitait encore."""
        fichier = self.tmp / "data" / "travail" / "etat.json"
        c = travaux.ajouter(self.ctx, {"titre": "En cours", "album": "A", "video_id": "abcdefghijk"})
        travaux.TRAVAUX[c["id"]]["etat"] = "reduction"
        travaux._sauver()
        avant = fichier.read_text(encoding="utf-8")
        travaux.configurer(self.tmp / "data", lambda: str(self.racine), lambda: 89.0, lambda: "192k", self.emby, ecriture=False)     # ce que fait « import app »
        self.assertEqual(fichier.read_text(encoding="utf-8"), avant)             # fichier intact
        self.assertEqual(travaux.TRAVAUX[c["id"]]["etat"], "reduction")
        travaux._maj(travaux.TRAVAUX[c["id"]], progres=50)                       # même une mise à jour ne l'écrit pas tant que demarrer() n'a pas eu lieu
        self.assertEqual(fichier.read_text(encoding="utf-8"), avant)


class ArtisteEmbyTest(unittest.TestCase):
    @staticmethod
    def _emby(artistes, comptes=None):
        emby = mock.Mock(configured=True)
        def requete(methode, chemin, **kw):
            r = mock.Mock()
            if chemin == "/Artists":
                r.json.return_value = {"Items": [{"Name": n, "Id": str(i)} for i, n in enumerate(artistes, 1)]}
            else:
                r.json.return_value = {"TotalRecordCount": (comptes or {}).get(kw["params"]["IncludeItemTypes"], 0)}
            return r
        emby._request.side_effect = requete
        return emby

    def test_identique(self):
        from src import emby_artistes
        r = emby_artistes.chercher(self._emby(["aespa"], {"Audio": 12, "MusicVideo": 3}), "aespa")
        self.assertEqual((r["etat"], r["exact"]["nom"], r["exact"]["titres_audio"], r["exact"]["clips"]), ("identique", "aespa", 12, 3))

    def test_meme_nom_ecrit_autrement(self):
        from src import emby_artistes
        r = emby_artistes.chercher(self._emby(["Aespa"]), "aespa")
        self.assertEqual((r["etat"], r["exact"]["nom"]), ("autre_ecriture", "Aespa"))

    def test_noms_voisins_et_absent(self):
        from src import emby_artistes
        r = emby_artistes.chercher(self._emby(["(G)I-DLE (여자)아이들", "Autre"]), "(G)I-DLE")
        self.assertEqual(r["etat"], "proche")
        self.assertEqual([x["nom"] for x in r["proches"]], ["(G)I-DLE (여자)아이들"])
        self.assertEqual(emby_artistes.chercher(self._emby(["Truc"]), "aespa")["etat"], "absent")

    def test_cle_absente_ou_emby_en_panne(self):
        from src import emby_artistes
        self.assertEqual(emby_artistes.chercher(mock.Mock(configured=False), "aespa")["etat"], "indisponible")
        emby = mock.Mock(configured=True)
        emby._request.side_effect = RuntimeError("Emby injoignable")
        r = emby_artistes.chercher(emby, "aespa")
        self.assertEqual(r["etat"], "indisponible")
        self.assertIn("injoignable", r["message"])

    def test_nom_choisi_dans_la_fiche_ecrit_dans_les_nfo(self):
        t = FicheTest("test_la_fiche_montre_tout")
        t.setUp()
        try:
            f, err = travaux.enregistrer_choix(t.tid, {"artiste": "Aespa"})
            self.assertEqual(err, "")
            self.assertEqual(f["artiste"], "Aespa")
            c = travaux.TRAVAUX[t.tid]
            self.assertIn("<artist>Aespa</artist>", travaux._nfo_clip(c))
            self.assertIn("<name>Aespa</name>", travaux._nfo_artiste(c))
            self.assertEqual(travaux.enregistrer_choix(t.tid, {"artiste": None})[0]["artiste"], "aespa")           # retour au nom d'origine
            self.assertIsNone(travaux.enregistrer_choix(t.tid, {"artiste": "x\ny"})[0])
        finally:
            t.tearDown()


class ReductionTest(unittest.TestCase):
    """Réduire la taille : l'image est réencodée en VP9, le son est recopié ; jamais un fichier plus gros, jamais d'AV1 réencodé."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _source(self, nom="s.mkv", codec="libx264", args=()):
        f = self.tmp / nom
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=3:size=640x360:rate=24", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=3:sample_rate=48000", "-c:v", codec, *args, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(f)],
                       check=True, capture_output=True)
        return f

    def test_image_en_vp9_plus_petite_son_recopie(self):
        src = self._source(args=("-crf", "10"))                                  # source volontairement gourmande
        pourcents = []
        r = telechargement.reduire(src, self.tmp / "r.mkv", 30, pourcents.append)
        self.assertTrue(r["fait"], r)
        self.assertLess(r["apres"], r["avant"] * 0.9)
        s = telechargement.sonder(self.tmp / "r.mkv")
        self.assertEqual((s["video"]["codec"], s["audio"]["codec"]), ("vp9", "aac"))          # image refaite en VP9, son recopié
        self.assertEqual((s["video"]["largeur"], s["video"]["hauteur"]), (640, 360))
        self.assertTrue(pourcents and max(pourcents) <= 99)                      # l'avancement est signalé
        self.assertEqual(audio.codec_audio(src), audio.codec_audio(self.tmp / "r.mkv"))
        self.assertTrue(src.exists())                                            # l'original n'est pas touché

    def test_son_identique_paquet_par_paquet(self):
        src = self._source(args=("-crf", "10"))
        telechargement.reduire(src, self.tmp / "r.mkv", 30)
        def paquets(p):
            return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=size", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
        self.assertEqual(paquets(src), paquets(self.tmp / "r.mkv"))              # aucun réencodage du son

    def test_gain_trop_faible_le_fichier_d_origine_est_garde(self):
        src = self._source("p.mkv", "libx264", ("-crf", "45"))                  # déjà très compressé : le VP9 ne gagnera pas 10 %
        r = telechargement.reduire(src, self.tmp / "r.mkv", 20)
        if not r["fait"]:
            self.assertFalse((self.tmp / "r.mkv").exists())                      # pas de fichier plus gros laissé derrière
            self.assertIn("gain trop faible", r["raison"])

    def test_av1_n_est_pas_reencode(self):
        src = self._source("a.mkv", "libsvtav1", ("-crf", "40"))
        r = telechargement.reduire(src, self.tmp / "r.mkv", 30)
        self.assertEqual((r["fait"], r["raison"]), (False, "déjà en AV1 (déjà compact)"))
        self.assertFalse((self.tmp / "r.mkv").exists())

    def test_pas_assez_de_memoire_pas_de_reencodage(self):
        src = self._source(args=("-crf", "10"))
        with mock.patch.object(telechargement, "memoire_disponible_go", return_value=1.0):
            r = telechargement.reduire(src, self.tmp / "r.mkv", 30)
        self.assertFalse(r["fait"])
        self.assertIn("mémoire", r["raison"])

    def test_choix_par_clip_defaut_non_ou_un_niveau(self):
        travaux.configurer(self.tmp / "data", lambda: "/x", lambda: 89.0, lambda: "192k", mock.Mock(configured=True), lambda: True, lambda: {"actif": True, "crf": 30})
        self.assertEqual(travaux.reduction_voulue({}), (True, 30))                       # pas de choix : le réglage général
        self.assertEqual(travaux.reduction_voulue({"reduction_mode": "defaut"}), (True, 30))
        self.assertEqual(travaux.reduction_voulue({"reduction_mode": "non"}), (False, 0))  # ce clip-là jamais réduit
        self.assertEqual(travaux.reduction_voulue({"reduction_mode": "plus_petit"}), (True, 36))
        self.assertEqual(travaux.reduction_voulue({"reduction_mode": "tres_peu_de_perte"}), (True, 27))
        travaux.configurer(self.tmp / "data", lambda: "/x", lambda: 89.0, lambda: "192k", mock.Mock(configured=True), lambda: True, lambda: {"actif": False, "crf": 30})
        self.assertFalse(travaux.reduction_voulue({})[0])                                # réglage général coupé : pas de réduction
        self.assertEqual(travaux.reduction_voulue({"reduction_mode": "leger"}), (True, 30))   # …mais ce clip-là, oui

    def test_clip_a_ne_pas_reduire_malgre_le_reglage_general(self):
        travaux.TRAVAUX.clear()
        racine = self.tmp / "Video clip"
        racine.mkdir()
        travaux.configurer(self.tmp / "data", lambda: str(racine), lambda: 89.0, lambda: "192k", mock.Mock(configured=True), lambda: True, lambda: {"actif": True, "crf": 30})
        src_gros = self._source("gros.mkv", args=("-crf", "10"))
        def faux_telechargement(video_id, dossier, progression=None, ydl_cls=None, format_id=""):
            d = Path(dossier)
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_gros, d / "source.mkv")
            return {"fichier": str(d / "source.mkv"), "vignette": "", "titre": "t", "chaine": "c", "duree": 3}
        ctx = {"nom": "aespa", "mbid": "b51c672b-0000-0000-0000-000000000000", "fiche": {"nom": "aespa", "mbid": "b", "pays": "KR", "debut": "2020"}, "dossier_groupe": "",
               "images": {}, "biographie": "", "genre": "K-Pop", "candidats": {}, "images_album": {}}
        with mock.patch.object(travaux.telechargement, "telecharger", side_effect=faux_telechargement):
            c = travaux.ajouter(ctx, {"titre": "T", "album": "A", "video_id": "abcdefghijk", "reduction_mode": "non"})
            travaux._preparer(travaux.TRAVAUX[c["id"]])
        j = travaux.TRAVAUX[c["id"]]
        self.assertEqual(j["etat"], "pret", j["message"])
        self.assertEqual(j["reduction"], {})                                             # rien n'a été réduit
        self.assertEqual(j["sonde"]["video"]["codec"], "h264")                           # l'image est restée telle quelle

    def test_dans_la_file_de_travaux_reduction_puis_volume(self):
        travaux.TRAVAUX.clear()
        racine = self.tmp / "Video clip"
        racine.mkdir()
        travaux.configurer(self.tmp / "data", lambda: str(racine), lambda: 89.0, lambda: "192k", mock.Mock(configured=True), lambda: True, lambda: {"actif": True, "crf": 30})
        src_gros = self._source("gros.mkv", args=("-crf", "10"))
        def faux_telechargement(video_id, dossier, progression=None, ydl_cls=None, format_id=""):
            d = Path(dossier)
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_gros, d / "source.mkv")
            return {"fichier": str(d / "source.mkv"), "vignette": "", "titre": "t", "chaine": "c", "duree": 3}
        ctx = {"nom": "aespa", "mbid": "b51c672b-0000-0000-0000-000000000000", "fiche": {"nom": "aespa", "mbid": "b", "pays": "KR", "debut": "2020"}, "dossier_groupe": "",
               "images": {}, "biographie": "", "genre": "K-Pop", "candidats": {}, "images_album": {}}
        with mock.patch.object(travaux.telechargement, "telecharger", side_effect=faux_telechargement):
            c = travaux.ajouter(ctx, {"titre": "T", "album": "A", "video_id": "abcdefghijk"})
            travaux._preparer(travaux.TRAVAUX[c["id"]])
        j = travaux.TRAVAUX[c["id"]]
        self.assertEqual(j["etat"], "pret", j["message"])
        self.assertTrue(j["reduction"]["fait"])
        self.assertLess(j["reduction"]["apres"], j["reduction"]["avant"] * 0.9)
        self.assertEqual(j["sonde"]["video"]["codec"], "vp9")
        self.assertIn("taille", j["message"])
        self.assertEqual(travaux.fiche(c["id"])["clip"]["reduction"]["fait"], True)


if __name__ == "__main__":
    unittest.main()
