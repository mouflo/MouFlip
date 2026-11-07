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

    def test_fiche_d_un_clip_pas_pret(self):
        c = travaux.ajouter(self.ctx, {"titre": "Autre", "album": "Autre", "video_id": "abcdefghijk"})
        self.assertIsNone(travaux.fiche(c["id"]))


class QualitesTest(unittest.TestCase):
    def test_format_demande(self):
        self.assertEqual(telechargement.format_demande(""), "bv*+ba[acodec^=mp4a]/bv*+ba/b")                # sans choix : la meilleure
        self.assertEqual(telechargement.format_demande("271"), "271+ba[acodec^=mp4a]/271+ba")              # choix : cette image, jamais autre chose en silence
        self.assertEqual(telechargement.format_demande("271; rm -rf /"), "bv*+ba[acodec^=mp4a]/bv*+ba/b")  # identifiant douteux : ignoré

    def test_options_utilisent_le_format_choisi(self):
        self.assertIn("137+ba", telechargement.options(Path("/tmp/x"), None, "137")["format"])


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
        travaux._charger()
        self.assertEqual(travaux.TRAVAUX[a["id"]]["etat"], "erreur")           # interrompu : signalé, pas perdu en silence
        self.assertIn("redémarrage", travaux.TRAVAUX[a["id"]]["message"])


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
