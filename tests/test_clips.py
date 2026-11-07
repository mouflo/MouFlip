"""MouFlip, premier morceau (lecture seule) : noms, clips déjà présents, choix de la vidéo YouTube et du meilleur format, routes.
Aucun réseau (MusicBrainz, TheAudioDB et YouTube sont remplacés), dossier « Video clip » fabriqué dans un dossier temporaire."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import existants, formats, noms, sources  # noqa: E402


class NomsTest(unittest.TestCase):
    def test_comparaison_tolerante(self):
        self.assertEqual(noms.normaliser("'KILL THIS LOVE' (4K M V) (1)"), "kill this love")
        self.assertEqual(noms.normaliser("Pink Venom [Official MV]"), "pink venom")
        self.assertEqual(noms.normaliser("AS IF IT’S YOUR LAST"), noms.normaliser("As If It's Your Last"))
        self.assertEqual(noms.normaliser("Ddu-Du Ddu-Du"), noms.normaliser("DDU-DU DDU-DU"))

    def test_chemin_prevu_suit_le_modele_actuel(self):
        self.assertEqual(noms.chemin_prevu("/mnt/Video clip", "BLACKPINK", "BLACKPINK", "BORN PINK", "Pink Venom"),
                         "/mnt/Video clip/BLACKPINK/BORN PINK/BLACKPINK - BORN PINK - Pink Venom.mkv")

    def test_caracteres_interdits(self):
        self.assertEqual(noms.propre('AC/DC: "Who?"'), "AC-DC - Who")
        self.assertEqual(noms.nom_de_clip("(G)I-DLE", "I feel", "Queencard"), "(G)I-DLE - I feel - Queencard.mkv")


class ExistantsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.racine = Path(self.tmp.name)
        g = self.racine / "BLACKPINK"
        (g / "BORN PINK").mkdir(parents=True)
        (g / "Kill This Love").mkdir()
        (g / "@eaDir").mkdir()
        (g / "extrafanart").mkdir()
        for f in ("BORN PINK/BLACKPINK - BORN PINK - Pink Venom.mkv", "BORN PINK/BLACKPINK - BORN PINK  - Shut Down.mkv",
                  "Kill This Love/BLACKPINK - 'KILL THIS LOVE' (4K M V) (1).mkv", "BORN PINK/BLACKPINK - BORN PINK - Pink Venom-320-10.bif",
                  "BORN PINK/BLACKPINK - BORN PINK - Pink Venom-poster.jpg", "@eaDir/truc.mkv", "extrafanart/fanart.jpg"):
            (g / f).write_bytes(b"x")
        (g / "BORN PINK/BLACKPINK - BORN PINK - Pink Venom.nfo").write_text("<musicvideo><title>Pink Venom</title></musicvideo>", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dossier_du_groupe_sans_accents_ni_majuscules(self):
        self.assertEqual(existants.dossier_du_groupe(self.racine, ["blackpink"]).name, "BLACKPINK")
        self.assertIsNone(existants.dossier_du_groupe(self.racine, ["aespa"]))

    def test_seules_les_videos_comptent(self):
        v = existants.lister(self.racine / "BLACKPINK")
        self.assertEqual(sorted(x["stem"] for x in v), ["BLACKPINK - 'KILL THIS LOVE' (4K M V) (1)", "BLACKPINK - BORN PINK  - Shut Down",
                                                         "BLACKPINK - BORN PINK - Pink Venom"])
        self.assertEqual({x["album"] for x in v}, {"BORN PINK", "Kill This Love"})

    def test_reconnaissance_des_clips_deja_la(self):
        v = existants.lister(self.racine / "BLACKPINK")
        self.assertTrue(existants.deja_la("Pink Venom", v))
        self.assertTrue(existants.deja_la("Shut Down", v))                  # double espace dans le nom du fichier
        self.assertTrue(existants.deja_la("Kill This Love", v))             # nom écrit autrement
        self.assertFalse(existants.deja_la("Lovesick Girls", v))
        self.assertFalse(existants.deja_la("Love", v))                      # un mot ne doit pas se retrouver dans « Kill This Love »

    def test_rien_n_est_ecrit(self):
        avant = sorted(str(p) for p in self.racine.rglob("*"))
        existants.lister(self.racine / "BLACKPINK")
        self.assertEqual(avant, sorted(str(p) for p in self.racine.rglob("*")))


class CliposTest(unittest.TestCase):
    def test_album_et_type(self):
        rel = [{"title": "THE SHOW", "date": "2021-06-18", "release-group": {"primary-type": "Album", "secondary-types": ["Live"]}},
               {"title": "Kill This Love", "date": "2019-04-09", "release-group": {"primary-type": "Single", "title": "Kill This Love"}}]
        self.assertEqual(sources._album_choisi(rel, "Kill This Love"), ("Kill This Love", "2019-04-09", ""))
        self.assertEqual(sources._album_choisi([], "Shut Down"), ("Shut Down", "", ""))

    def test_meme_titre_dans_plusieurs_albums_un_seul_clip(self):
        page = {"count": 2, "recordings": [
            {"id": "1", "title": "Kill This Love", "first-release-date": "2019-04-05", "releases": [{"date": "2019-04-05", "release-group": {"primary-type": "Single", "title": "Kill This Love"}}]},
            {"id": "2", "title": "Kill This Love", "first-release-date": "2021-08-03", "releases": [{"date": "2021-08-03", "release-group": {"primary-type": "Album", "title": "THE ALBUM -JP ver.-"}}]}]}
        with mock.patch.object(sources, "_get", return_value=page):
            r = sources.clips_mb("x", "BLACKPINK")
        self.assertEqual([(c["titre"], c["album"], c["annee"]) for c in r], [("Kill This Love", "Kill This Love", "2019")])

    def test_clips_mb_filtre_et_dedoublonne(self):
        page = {"count": 4, "recordings": [
            {"id": "1", "title": "Pink Venom", "first-release-date": "2022-08-19", "releases": [{"date": "2022-09-16", "release-group": {"primary-type": "Album", "title": "BORN PINK"}}]},
            {"id": "2", "title": "Pink Venom", "first-release-date": "2022-08-19", "releases": [{"date": "2022-09-16", "release-group": {"primary-type": "Album", "title": "BORN PINK"}}]},
            {"id": "3", "title": "[THE SHOW] BEHIND EP.1", "releases": []},
            {"id": "4", "title": "STAY", "releases": [{"release-group": {"primary-type": "Album", "secondary-types": ["Live"], "title": "Concert"}}]}]}
        with mock.patch.object(sources, "_get", return_value=page):
            r = sources.clips_mb("x", "BLACKPINK")
        self.assertEqual([(c["titre"], c["album"], c["source"]) for c in r], [("Pink Venom", "BORN PINK", "video")])   # coulisses et concert écartés, doublon fusionné

    def test_remix_versions_et_choregraphies_ecartes(self):
        page = {"count": 5, "recordings": [{"id": str(i), "title": t, "releases": []} for i, t in enumerate(
            ["Girls (BRLLNT remix)", "Whiplash (English version)", "Dirty Work (choreography video)", "Savage (camerawork guide version)", "Supernova"])]}
        with mock.patch.object(sources, "_get", return_value=page):
            r = sources.clips_mb("x", "aespa")
        self.assertEqual([c["titre"] for c in r], ["Supernova"])

    def test_single_de_pre_sortie_va_dans_l_album_les_autres_gardent_leur_dossier(self):
        def faux(url, params=None, max_essais=3):
            if url.endswith("/recording"):
                return {"count": 0, "recordings": []}
            if url.endswith("/release-group"):
                return {"count": 4, "release-groups": [
                    {"id": "d", "title": "Girls", "primary-type": "EP", "first-release-date": "2022-07-08"},
                    {"id": "a", "title": "Black Mamba", "primary-type": "Single", "first-release-date": "2020-11-17"},
                    {"id": "b", "title": "Supernova", "primary-type": "Single", "first-release-date": "2024-05-13"},
                    {"id": "c", "title": "Armageddon", "primary-type": "Album", "first-release-date": "2024-05-27"}]}
            return {"release-count": 2, "releases": [
                {"title": "Girls", "date": "2022-07-08", "release-group": {"title": "Girls", "primary-type": "EP", "first-release-date": "2022-07-08"},
                 "media": [{"tracks": [{"title": "Girls"}, {"title": "Black Mamba"}]}]},
                {"title": "Armageddon", "date": "2024-05-27", "release-group": {"title": "Armageddon", "primary-type": "Album", "first-release-date": "2024-05-27"},
                 "media": [{"tracks": [{"title": "Supernova"}, {"title": "Armageddon"}]}]}]}
        with mock.patch.object(sources, "_get", side_effect=faux):
            r = sources.clips_mb("x", "aespa")
        self.assertEqual({c["titre"]: c["album"] for c in r}, {"Black Mamba": "Black Mamba", "Supernova": "Armageddon", "Armageddon": "Armageddon", "Girls": "Girls"})


def _faux_youtube(entrees, formats_video):
    def extraire(requete, options):
        if str(requete).startswith("ytsearch"):
            return {"entries": entrees}
        return {"title": "BLACKPINK - Pink Venom (Official MV)", "channel": "BLACKPINK", "duration": 193, "formats": formats_video}
    return extraire


class FormatsTest(unittest.TestCase):
    ENTREES = [
        {"id": "aaa", "title": "BLACKPINK - 'Pink Venom' DANCE PRACTICE", "channel": "Fan", "duration": 200},
        {"id": "bbb", "title": "BLACKPINK - 'Pink Venom' M/V", "channel": "BLACKPINK", "duration": 193},
        {"id": "ccc", "title": "Pink Venom lyrics", "channel": "Lyrics", "duration": 190},
        {"id": "ddd", "title": "Autre chose", "channel": "Truc", "duration": 190}]
    FORMATS = [{"format_id": "137", "height": 1080, "width": 1920, "vcodec": "avc1.640028", "tbr": 4016, "fps": 24},
               {"format_id": "616", "height": 1080, "width": 1920, "vcodec": "vp09.00.40.08", "tbr": 4691, "fps": 24, "format_note": "Premium"},
               {"format_id": "313", "height": 2160, "width": 3840, "vcodec": "vp09.00.50.08", "tbr": 17045, "fps": 24},
               {"format_id": "251", "acodec": "opus", "vcodec": "none", "abr": 132}]

    def test_choisit_le_clip_officiel(self):
        c, tous = formats.choisir_video("BLACKPINK", "Pink Venom", _faux_youtube(self.ENTREES, []))
        self.assertEqual(c["id"], "bbb")
        self.assertEqual(tous[-1]["note"], -100)                                # sans le titre : écarté

    def test_meilleur_format_resolution_puis_debit(self):
        f = formats.meilleur_format({"formats": self.FORMATS})
        self.assertEqual((f["format_id"], f["resolution"], f["codec"], f["debit_kbps"]), ("313", "2160p", "vp9", 17045))
        self.assertEqual(f["audio"]["codec"], "opus")
        sans4k = formats.meilleur_format({"formats": self.FORMATS[:2]})
        self.assertEqual((sans4k["format_id"], sans4k["premium"]), ("616", True))   # à 1080p le plus gros débit (Premium) gagne

    def test_analyse_complete(self):
        r = formats.analyser("BLACKPINK", "Pink Venom", _faux_youtube(self.ENTREES, self.FORMATS))
        self.assertEqual((r["etat"], r["video"]["id"], r["format"]["resolution"]), ("ok", "bbb", "2160p"))

    def test_introuvable_n_est_pas_une_panne(self):
        r = formats.analyser("BLACKPINK", "Pink Venom", _faux_youtube([self.ENTREES[3]], []))
        self.assertEqual(r["etat"], "introuvable")

    def test_une_panne_est_dite(self):
        def casse(q, o):
            raise RuntimeError("HTTP 429")
        r = formats.analyser("BLACKPINK", "Pink Venom", casse)
        self.assertEqual(r["etat"], "erreur")
        self.assertIn("429", r["erreur"])


class RoutesTest(unittest.TestCase):
    def setUp(self):
        from werkzeug.security import generate_password_hash
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k) for k in ("APP_USER", "APP_PASSWORD_HASH")}
        import app as A                                   # l'import relit data/secrets.env : le compte d'essai se règle APRÈS
        os.environ.update(APP_USER="admin", APP_PASSWORD_HASH=generate_password_hash("mdp-de-test-123"))
        self.A = A
        self.racine = Path(self.tmp.name) / "Video clip"
        (self.racine / "BLACKPINK" / "BORN PINK").mkdir(parents=True)
        (self.racine / "BLACKPINK" / "BORN PINK" / "BLACKPINK - BORN PINK - Pink Venom.mkv").write_bytes(b"x")
        self._racine, A.DOSSIER_CLIPS = A.DOSSIER_CLIPS, str(self.racine)
        self._cache, A.CACHE_DIR = A.CACHE_DIR, Path(self.tmp.name) / "cache"          # jamais le vrai cache enregistré
        self._p_choix = mock.patch.object(A, "_choix_fichier", return_value=Path(self.tmp.name) / "choix.json")        # ni les vrais choix de l'utilisatrice
        self._p_qualite = mock.patch.object(A, "_qualite_fichier", return_value=Path(self.tmp.name) / "choix_qualite.json")
        self._p_choix.start()
        self._p_qualite.start()
        self._p_yt = mock.patch.object(formats, "decouvrir", return_value=[])                 # jamais de vrai réseau YouTube dans les tests
        self._p_rgs = mock.patch.object(sources, "_sorties", return_value=[])
        self._p_yt.start()
        self._p_rgs.start()
        A._CACHE_GROUPES.clear()
        self.c = A.app.test_client()
        self.c.environ_base["REMOTE_ADDR"] = "192.168.1.50"
        self.assertEqual(self.c.post("/login", data={"username": "admin", "password": "mdp-de-test-123"}).status_code, 302)

    def tearDown(self):
        self._p_yt.stop()
        self._p_rgs.stop()
        self._p_choix.stop()
        self._p_qualite.stop()
        self.A.DOSSIER_CLIPS = self._racine
        self.A.CACHE_DIR = self._cache
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_page_non_connecte_refusee(self):
        autre = self.A.app.test_client()
        autre.environ_base["REMOTE_ADDR"] = "192.168.1.50"
        self.assertIn(autre.get("/api/groupes/chercher?q=blackpink").status_code, (302, 401))

    def test_groupe_marque_les_clips_deja_la_sans_rien_ecrire(self):
        mb = "48646387-1664-4c9a-9139-9bfd091b823c"
        clips = [{"titre": "Pink Venom", "album": "BORN PINK", "annee": "2022", "mbid": "1", "type": "clip", "duree": 193},
                 {"titre": "Shut Down", "album": "BORN PINK", "annee": "2022", "mbid": "2", "type": "clip", "duree": 175}]
        avant = sorted(str(p) for p in self.racine.rglob("*"))
        with mock.patch.object(sources, "fiche_mb", return_value={"mbid": mb, "nom": "BLACKPINK", "alias": [], "genres": ["k-pop"]}), \
                mock.patch.object(sources, "clips_mb", return_value=clips), \
                mock.patch.object(sources, "images_audiodb", return_value={"biographie": "Un groupe.", "images": {}}), \
                mock.patch.object(sources, "images_fanart", return_value={"cle": False, "images": {}, "albums": {}}):
            d = self.c.get("/api/groupe/" + mb).get_json()
        self.assertEqual([(c["titre"], c["deja"]) for c in d["clips"]], [("Pink Venom", True), ("Shut Down", False)])
        self.assertTrue(d["clips"][1]["chemin_prevu"].endswith("/BLACKPINK/BORN PINK/BLACKPINK - BORN PINK - Shut Down.mkv"))
        self.assertEqual(d["nb_locaux"], 1)
        self.assertEqual(avant, sorted(str(p) for p in self.racine.rglob("*")))     # rien d'écrit dans « Video clip »

    MB = "48646387-1664-4c9a-9139-9bfd091b823c"

    def _faux_mb(self, clips=None):
        clips = clips or [{"titre": "Pink Venom", "album": "BORN PINK", "annee": "2022", "mbid": "1", "type": "clip", "duree": 193}]
        return (mock.patch.object(sources, "fiche_mb", return_value={"mbid": self.MB, "nom": "BLACKPINK", "alias": [], "genres": ["k-pop"]}),
                mock.patch.object(sources, "clips_mb", return_value=clips),
                mock.patch.object(sources, "images_audiodb", return_value={"biographie": "Un groupe.", "images": {}}),
                mock.patch.object(sources, "images_fanart", return_value={"cle": False, "images": {}, "albums": {}}))

    def test_liste_enregistree_pas_redemandee_puis_actualisee(self):
        p1, p2, p3, p4 = self._faux_mb()
        with p1, p2 as clips_mb, p3, p4:
            a = self.c.get("/api/groupe/" + self.MB).get_json()
            b = self.c.get("/api/groupe/" + self.MB).get_json()
            self.assertEqual(clips_mb.call_count, 1)                           # la 2e ouverture lit la liste enregistrée
            self.assertTrue(b["depuis_cache"])
            self.assertEqual(a["clips"], b["clips"])
            self.assertTrue((self.A.CACHE_DIR / "groupes" / f"{self.MB}.json").exists())
            self.c.get("/api/groupe/" + self.MB + "?actualiser=1")
            self.assertEqual(clips_mb.call_count, 2)                           # « Actualiser » redemande

    def test_musicbrainz_en_panne_la_liste_enregistree_sert_quand_meme(self):
        p1, p2, p3, p4 = self._faux_mb()
        with p1, p2, p3, p4:
            self.c.get("/api/groupe/" + self.MB)
        with mock.patch.object(sources, "fiche_mb", side_effect=sources.SourceIndisponible("musicbrainz.org ne répond pas")):
            d = self.c.get("/api/groupe/" + self.MB + "?actualiser=1").get_json()
        self.assertEqual([c["titre"] for c in d["clips"]], ["Pink Venom"])
        self.assertTrue(any("liste enregistrée" in e for e in d["erreurs"]))
        autre = "11111111-2222-3333-4444-555555555555"
        with mock.patch.object(sources, "fiche_mb", side_effect=sources.SourceIndisponible("musicbrainz.org ne répond pas")):
            r = self.c.get("/api/groupe/" + autre)
        self.assertEqual(r.status_code, 502)                                   # jamais vu et MusicBrainz en panne : erreur dite

    def test_liste_enregistree_par_une_ancienne_version_est_refaite_toute_seule(self):
        p1, p2, p3, p4 = self._faux_mb()
        with p1, p2 as clips_mb, p3, p4:
            self.c.get("/api/groupe/" + self.MB)
            fichier = self.A.CACHE_DIR / "groupes" / f"{self.MB}.json"
            base = json.loads(fichier.read_text())
            base["v"] = 1                                                        # enregistrée avant l'ajout de la découverte YouTube
            fichier.write_text(json.dumps(base))
            self.c.get("/api/groupe/" + self.MB)
            self.assertEqual(clips_mb.call_count, 2)                             # refaite
            self.c.get("/api/groupe/" + self.MB)
            self.assertEqual(clips_mb.call_count, 2)                             # puis de nouveau lue sur place
        with mock.patch.object(sources, "fiche_mb", side_effect=sources.SourceIndisponible("musicbrainz.org ne répond pas")):
            base = json.loads(fichier.read_text()); base["v"] = 1; fichier.write_text(json.dumps(base))
            d = self.c.get("/api/groupe/" + self.MB).get_json()                  # MusicBrainz en panne : l'ancienne liste sert quand même
        self.assertEqual(len(d["clips"]), 1)

    def test_la_deja_la_se_recalcule_meme_avec_la_liste_enregistree(self):
        p1, p2, p3, p4 = self._faux_mb()
        with p1, p2, p3, p4:
            a = self.c.get("/api/groupe/" + self.MB).get_json()
            self.assertTrue(a["clips"][0]["deja"])
            (self.racine / "BLACKPINK" / "BORN PINK" / "BLACKPINK - BORN PINK - Pink Venom.mkv").unlink()
            b = self.c.get("/api/groupe/" + self.MB).get_json()
        self.assertFalse(b["clips"][0]["deja"])                                # l'état local n'est jamais enregistré

    def test_recherche_enregistree_et_secours_en_panne(self):
        g = [{"mbid": self.MB, "nom": "BLACKPINK", "score": 100}]
        with mock.patch.object(sources, "chercher_groupes", return_value=g) as ch:
            self.c.get("/api/groupes/chercher?q=blackpink")
            self.c.get("/api/groupes/chercher?q=BlackPink")
            self.assertEqual(ch.call_count, 1)
        fichier = self.A.CACHE_DIR / "recherches.json"
        d = json.loads(fichier.read_text())
        d[list(d)[0]]["date"] = 0                                              # vieille recherche : redemandée, mais gardée si MusicBrainz tombe
        fichier.write_text(json.dumps(d))
        with mock.patch.object(sources, "chercher_groupes", side_effect=sources.SourceIndisponible("musicbrainz.org ne répond pas")):
            r = self.c.get("/api/groupes/chercher?q=blackpink").get_json()
        self.assertEqual(r["groupes"], g)
        self.assertIn("enregistrés", r["avertissement"])

    def test_identifiant_invalide(self):
        self.assertEqual(self.c.get("/api/groupe/pas-un-id").status_code, 400)

    def test_formats_un_seul_lot_a_la_fois_et_resultats(self):
        import time
        with mock.patch.object(formats, "analyser", return_value={"etat": "ok", "video": {"id": "x", "titre": "t", "chaine": "c"}, "format": {"resolution": "2160p"}}):
            d = self.c.post("/api/formats", json={"groupe": "BLACKPINK", "titres": ["Pink Venom", "Shut Down"]}).get_json()
            for _ in range(50):
                e = self.c.get("/api/formats/etat?job=" + d["job"]).get_json()
                if e["fini"]:
                    break
                time.sleep(0.05)
        self.assertEqual((e["fait"], e["total"], sorted(e["resultats"])), (2, 2, ["Pink Venom", "Shut Down"]))
        self.assertEqual(self.c.post("/api/formats", json={"groupe": "", "titres": []}).status_code, 400)

    def test_cles_des_reglages_jamais_renvoyees_en_entier(self):
        with mock.patch.object(self.A, "_tester_http", return_value=(200, "")), \
                mock.patch.object(self.A.emby_settings, "_write_secret") as ecrire, mock.patch.dict(os.environ, {"FANART_API_KEY": ""}):
            r = self.c.post("/api/settings/imvdb", json={"api_key": "abcdefghij1234567890abcdefghij1234567890", "test": True}).get_json()
            self.assertTrue(r["ok"])
            ecrire.assert_not_called()                                           # « Tester » n'enregistre rien
            r = self.c.post("/api/settings/imvdb", json={"api_key": "abcdefghij1234567890abcdefghij1234567890"}).get_json()
            self.assertTrue(r["ok"])
            self.assertEqual(ecrire.call_args.args[1], "IMVDB_API_KEY")
            etat = self.c.get("/api/settings/imvdb").get_json()                  # relu dans le même bloc : l'environnement d'essai est restauré à la sortie
            self.assertTrue(etat["configured"])
            self.assertEqual(etat["hint"], "…7890")
            self.assertNotIn("abcdefghij1234567890abcdefghij", json.dumps(etat))

    def test_cle_refusee_par_le_service_n_est_pas_enregistree(self):
        with mock.patch.object(self.A, "_tester_http", return_value=(403, "")), mock.patch.object(self.A.emby_settings, "_write_secret") as ecrire:
            r = self.c.post("/api/settings/imvdb", json={"api_key": "abcdefghij1234567890"})
        self.assertEqual(r.status_code, 400)
        ecrire.assert_not_called()
        self.assertEqual(self.c.post("/api/settings/imvdb", json={"api_key": "a b"}).status_code, 400)

    def test_choix_de_qualite_enregistre_et_transmis_aux_travaux(self):
        p1, p2, p3, p4 = self._faux_mb()
        with p1, p2, p3, p4:
            r = self.c.post("/api/choix_qualite", json={"mbid": self.MB, "titre": "Pink Venom", "video_id": "gQlMMD8auMs", "format_id": "271", "texte": "1440p vp9"})
            self.assertTrue(r.get_json()["ok"])
            self.assertEqual(self.c.get("/api/choix_qualite/" + self.MB).get_json()["choix"]["Pink Venom"]["format_id"], "271")
            with mock.patch.object(self.A.travaux, "ajouter", side_effect=lambda ctx, clip: clip) as aj:
                self.c.post("/api/travaux", json={"mbid": self.MB, "clips": [{"titre": "Pink Venom", "album": "BORN PINK"}]})
            clip = aj.call_args.args[1]
            self.assertEqual((clip["video_id"], clip["format_id"], clip["qualite"]), ("gQlMMD8auMs", "271", "1440p vp9"))
            # une autre vidéo choisie pour ce clip : la qualité choisie sur la première n'est PAS appliquée
            with mock.patch.object(self.A.travaux, "ajouter", side_effect=lambda ctx, clip: clip) as aj:
                self.c.post("/api/travaux", json={"mbid": self.MB, "clips": [{"titre": "Pink Venom", "album": "BORN PINK", "video_id": "zzzzzzzzzzz"}]})
            self.assertEqual((aj.call_args.args[1]["video_id"], aj.call_args.args[1]["format_id"]), ("zzzzzzzzzzz", ""))
            # format_id vide = retour à « la meilleure »
            self.c.post("/api/choix_qualite", json={"mbid": self.MB, "titre": "Pink Venom", "video_id": "", "format_id": ""})
            self.assertEqual(self.c.get("/api/choix_qualite/" + self.MB).get_json()["choix"], {})
        self.assertEqual(self.c.post("/api/choix_qualite", json={"mbid": self.MB, "titre": "X", "video_id": "bad", "format_id": "271"}).status_code, 400)
        self.assertEqual(self.c.post("/api/choix_qualite", json={"mbid": self.MB, "titre": "X", "video_id": "gQlMMD8auMs", "format_id": "1; rm"}).status_code, 400)

    def test_resolution_de_l_album_d_un_clip(self):
        """Le groupe de sorties est retrouvé d'après la liste enregistrée : même titre, puis même album ; celui qui a une image fanart.tv de préférence."""
        clips = [{"titre": "LEMONADE", "album": "LEMONADE", "rg": "rg_single", "mbid": "1", "annee": "2026"},
                 {"titre": "WDA", "album": "LEMONADE", "rg": "rg_album", "mbid": "2", "annee": "2026"}]
        p1, p2, p3, p4 = self._faux_mb(clips)
        with p1, p2, p3, p4:
            ctx = {"mbid": self.MB, "images_album": {"rg_album": {"couverture": "http://i/c.jpg"}}}
            self.assertEqual(self.A._resoudre_rg(ctx, "LEMONADE", "LEMONADE"), "rg_album")      # le single n'a pas d'image, l'album du même nom oui
            self.assertEqual(self.A._resoudre_rg({"mbid": self.MB, "images_album": {}}, "LEMONADE", "LEMONADE"), "rg_single")
            self.assertEqual(self.A._resoudre_rg(ctx, "Inconnu", "Autre album"), "")

    def test_ajout_a_la_main_analyse_confirme_et_survit_a_l_actualisation(self):
        p1, p2, p3, p4 = self._faux_mb()
        rgs = [{"id": "rgV", "titre": "VERSUS", "type": "EP", "date": "2023-11-02"}, {"id": "rgS", "titre": "MANIAC", "type": "Single", "date": "2023-11-02"}]
        with p1, p2, p3, p4:
            self.c.get("/api/groupe/" + self.MB)                                 # crée la liste enregistrée
            fichier = self.A.CACHE_DIR / "groupes" / f"{self.MB}.json"
            base = json.loads(fichier.read_text())
            base["rgs"] = rgs
            base["fiche"]["nom"] = "VIVIZ"
            fichier.write_text(json.dumps(base))
            infos = {"title": "VIVIZ (비비지) - 'MANIAC' MV", "channel": "BPM Entertainment", "duration": 196, "upload_date": "20231102"}
            with mock.patch("yt_dlp.YoutubeDL") as yd:
                yd.return_value.extract_info.return_value = infos
                r = self.c.post("/api/ajout/analyser", json={"mbid": self.MB, "lien": "https://www.youtube.com/watch?v=9JFi7MmjtGA&list=RD83C3TZ4Zm_o&index=23"}).get_json()
            self.assertEqual((r["video_id"], r["titre"], r["annee"], r["album_suggere"]), ("9JFi7MmjtGA", "MANIAC", "2023", "VERSUS"))     # l'EP sorti le jour du clip, pas le single
            ok = self.c.post("/api/ajout/confirmer", json={"mbid": self.MB, "titre": "MANIAC", "album": "VERSUS", "annee": "2023", "video_id": "9JFi7MmjtGA"}).get_json()
            self.assertTrue(ok["ok"])
            clips = self.c.get("/api/groupe/" + self.MB).get_json()["clips"]
            m = next(c for c in clips if c["titre"] == "MANIAC")
            self.assertEqual((m["album"], m["source"], m["video_id"], m["rg"]), ("VERSUS", "ajout", "9JFi7MmjtGA", "rgV"))
            self.assertEqual(self.c.get("/api/choix/" + self.MB).get_json()["choix"]["MANIAC"], "9JFi7MmjtGA")       # la vidéo collée sera téléchargée
            with mock.patch.object(sources, "clips_mb", return_value=[]):
                apres = self.c.get("/api/groupe/" + self.MB + "?actualiser=1").get_json()["clips"]
            self.assertEqual([c["titre"] for c in apres], ["MANIAC"])            # l'ajout reste après « Actualiser »
            self.c.post("/api/ajout/retirer", json={"mbid": self.MB, "titre": "MANIAC"})
            with mock.patch.object(sources, "clips_mb", return_value=[]):
                self.assertEqual(self.c.get("/api/groupe/" + self.MB + "?actualiser=1").get_json()["clips"], [])

    def test_ajout_lien_invalide_et_champs_invalides(self):
        self.assertEqual(self.c.post("/api/ajout/analyser", json={"mbid": self.MB, "lien": "pas un lien"}).status_code, 400)
        self.assertEqual(self.c.post("/api/ajout/confirmer", json={"mbid": self.MB, "titre": "", "album": "x", "video_id": "9JFi7MmjtGA"}).status_code, 400)
        self.assertEqual(self.c.post("/api/ajout/confirmer", json={"mbid": self.MB, "titre": "X", "album": "x", "video_id": "court"}).status_code, 400)
        self.assertEqual(self.c.post("/api/ajout/confirmer", json={"mbid": self.MB, "titre": "X", "album": "x", "annee": "20x", "video_id": "9JFi7MmjtGA"}).status_code, 400)

    def test_reglage_reduire_la_taille(self):
        with mock.patch.object(self.A.os, "replace"), mock.patch.object(Path, "write_text"):
            r = self.c.post("/api/settings/reduction", json={"actif": True, "crf": 30}).get_json()
        self.assertTrue(r["ok"])
        self.assertEqual((self.A.REDUCTION["actif"], self.A.REDUCTION["crf"]), (True, 30))
        self.assertEqual(self.c.post("/api/settings/reduction", json={"actif": True, "crf": 5}).status_code, 400)     # niveau absurde refusé
        self.assertEqual(self.c.post("/api/settings/reduction", json={"actif": True, "crf": "x"}).status_code, 400)
        g = self.c.get("/api/settings/reduction").get_json()
        self.assertEqual(g["choix"]["leger"], 30)
        self.A.REDUCTION.update(actif=False, crf=30)                                    # remis comme avant pour les autres tests

    def test_choix_de_reduction_par_clip_enregistre_et_transmis(self):
        with mock.patch.object(self.A, "_reduction_fichier", return_value=Path(self.tmp.name) / "choix_reduction.json"):
            p1, p2, p3, p4 = self._faux_mb()
            with p1, p2, p3, p4:
                ok = self.c.post("/api/choix_reduction", json={"mbid": self.MB, "titres": ["Pink Venom", "Autre"], "mode": "plus_petit"}).get_json()
                self.assertTrue(ok["ok"])
                self.assertEqual(self.c.get("/api/choix_reduction/" + self.MB).get_json()["choix"], {"Pink Venom": "plus_petit", "Autre": "plus_petit"})
                with mock.patch.object(self.A.travaux, "ajouter", side_effect=lambda ctx, clip: clip) as aj:
                    self.c.post("/api/travaux", json={"mbid": self.MB, "clips": [{"titre": "Pink Venom", "album": "BORN PINK"}]})
                self.assertEqual(aj.call_args.args[1]["reduction_mode"], "plus_petit")
                self.c.post("/api/choix_reduction", json={"mbid": self.MB, "titre": "Pink Venom", "mode": "defaut"})        # retour au réglage général
                self.assertEqual(self.c.get("/api/choix_reduction/" + self.MB).get_json()["choix"], {"Autre": "plus_petit"})
                with mock.patch.object(self.A.travaux, "ajouter", side_effect=lambda ctx, clip: clip) as aj:
                    self.c.post("/api/travaux", json={"mbid": self.MB, "clips": [{"titre": "Pink Venom", "album": "BORN PINK"}]})
                self.assertEqual(aj.call_args.args[1]["reduction_mode"], "defaut")
        for mauvais in ({"mode": "h265"}, {"mode": "non", "titres": []}, {"mode": "non", "mbid": "x", "titres": ["a"]}):
            corps = {"mbid": self.MB, "titres": ["a"], **mauvais}
            self.assertEqual(self.c.post("/api/choix_reduction", json=corps).status_code, 400)

    def test_chaines_officielles_ajouter_marquer_retirer(self):
        from src import chaines as _ch
        p1, p2, p3, p4 = self._faux_mb()
        cid, autre = "UCWFMjDuWKlct0jy89k2kkRA", "UC6YMr57knEIYXOOKMmYAFXQ"
        with p1, p2, p3, p4, mock.patch.object(self.A, "_fichier_chaines", return_value=Path(self.tmp.name) / "chaines.json"):
            self.c.get("/api/groupe/" + self.MB)
            fich = self.A.CACHE_DIR / "groupes" / f"{self.MB}.json"
            base = json.loads(fich.read_text()); base["chaines_mb"] = [{"id": cid, "nom": "VIVIZ", "url": "u"}]; fich.write_text(json.dumps(base))
            self.assertEqual([(x["id"], x["source"]) for x in self.c.get("/api/chaines/" + self.MB).get_json()["chaines"]], [(cid, "MusicBrainz")])
            with mock.patch.object(_ch, "resoudre", return_value={"id": autre, "nom": "BPM Entertainment", "url": f"https://www.youtube.com/channel/{autre}"}):
                r = self.c.post("/api/chaines/ajouter", json={"mbid": self.MB, "lien": "https://www.youtube.com/@BPM"}).get_json()
            self.assertEqual([(x["id"], x["source"]) for x in r["chaines"]], [(cid, "MusicBrainz"), (autre, "toi")])
            self.assertEqual(self.A._ids_chaines(self.MB), {cid, autre})
            # le bouton « cette chaîne est officielle » du lecteur
            r = self.c.post("/api/chaines/officielle", json={"mbid": self.MB, "channel_id": "UCqq-ovGE01ErlXakPihhKDA", "nom": "1theK"}).get_json()
            self.assertEqual(len(r["chaines"]), 3)
            # l'aperçu juge « officielle » d'après ces chaînes
            e = {"id": "aaaaaaaaaaa", "title": "VIVIZ - MANIAC", "channel": "BPM Entertainment", "channel_id": autre, "duration": 196}
            with mock.patch("yt_dlp.YoutubeDL") as yd:
                yd.return_value.extract_info.return_value = {"entries": [e]}
                c = self.c.post("/api/apercu", json={"groupe": "VIVIZ", "titre": "MANIAC", "mbid": self.MB}).get_json()["candidats"]
            self.assertTrue(c[0]["officielle"])
            # retirer : seulement celles de l'utilisatrice, jamais celle de MusicBrainz
            r = self.c.post("/api/chaines/retirer", json={"mbid": self.MB, "id": autre}).get_json()
            self.assertEqual({x["id"] for x in r["chaines"]}, {cid, "UCqq-ovGE01ErlXakPihhKDA"})
            r = self.c.post("/api/chaines/retirer", json={"mbid": self.MB, "id": cid}).get_json()
            self.assertIn(cid, {x["id"] for x in r["chaines"]})
            # entrées invalides
            self.assertEqual(self.c.post("/api/chaines/officielle", json={"mbid": self.MB, "channel_id": "pas valide"}).status_code, 400)
            with mock.patch.object(_ch, "resoudre", side_effect=_ch.ChaineError("Colle l'adresse d'une chaîne YouTube")):
                bad = self.c.post("/api/chaines/ajouter", json={"mbid": self.MB, "lien": "n'importe quoi"})
            self.assertEqual(bad.status_code, 400)
            self.assertIn("chaîne YouTube", bad.get_json()["error"])

    def test_artiste_solo_herite_des_sources_de_son_groupe(self):
        par = "0068ae6c-7156-40f9-a81f-39294af6a549"
        fich = self.A.CACHE_DIR / "groupes" / f"{self.MB}.json"
        fich.parent.mkdir(parents=True, exist_ok=True)
        fich.write_text(json.dumps({"fiche": {"nom": "YUQI", "parents": [{"id": par, "nom": "i‐dle"}]}, "clips": [], "chaines_mb": [],
                                    "chaines_parents": [{"id": "UCritGVo7pLJLUS8wEu32vow", "nom": "i-dle", "url": "u", "parent": "i‐dle", "parent_id": par}]}))
        parent_toi = [{"type": "playlist", "id": "PLG8DKAYW6M264immTa4KjaGkYBF1FHJ5X", "nom": "Music Videos", "url": "u", "channel_id": "UCritGVo7pLJLUS8wEu32vow"}]
        with mock.patch.object(self.A, "_fichier_chaines", side_effect=lambda m: Path(self.tmp.name) / f"{m}.json"):
            (Path(self.tmp.name) / f"{par}.json").write_text(json.dumps(parent_toi))              # la playlist a été ajoutée sur la page du GROUPE
            liste = self.A._chaines_officielles(self.MB)
        self.assertEqual([(x["type"], x["source"]) for x in liste], [("chaine", "MusicBrainz, groupe i‐dle"), ("playlist", "toi, groupe i‐dle")])
        with mock.patch.object(self.A, "_fichier_chaines", side_effect=lambda m: Path(self.tmp.name) / f"{m}.json"):
            self.assertEqual(self.A._ids_chaines(self.MB), {"UCritGVo7pLJLUS8wEu32vow"})
            self.assertEqual(self.A._ids_playlists(self.MB), ["PLG8DKAYW6M264immTa4KjaGkYBF1FHJ5X"])

    def test_recherche_par_adresse_de_fiche_et_anciennes_recherches_enregistrees_ignorees(self):
        fiche = {"mbid": "a0bb95b8-9fd9-47dd-af28-d4210f7f2a11", "nom": "ALI", "type": "Group", "pays": "JP", "precision": "", "score": 100, "debut": "2016"}
        with mock.patch.object(sources, "artiste_par_mbid", return_value=fiche):
            r = self.c.get("/api/groupes/chercher?q=https://musicbrainz.org/artist/a0bb95b8-9fd9-47dd-af28-d4210f7f2a11").get_json()
        self.assertEqual(([g["nom"] for g in r["groupes"]], r["direct"]), (["ALI"], True))
        with mock.patch.object(sources, "artiste_par_mbid", return_value=None):
            r = self.c.get("/api/groupes/chercher?q=a0bb95b8-9fd9-47dd-af28-d4210f7f2a11").get_json()
        self.assertEqual(r["groupes"], [])
        self.assertIn("Aucune fiche", r["avertissement"])
        # une recherche enregistrée par l'ancienne version (clé sans « v2: ») n'est jamais réutilisée
        self.A.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (self.A.CACHE_DIR / "recherches.json").write_text(json.dumps({"ali": {"date": time.time(), "groupes": [{"mbid": "x", "nom": "Mauvais résultat"}]}}))
        with mock.patch.object(sources, "chercher_groupes", return_value=[{"mbid": "y", "nom": "ALI"}]) as ch:
            r = self.c.get("/api/groupes/chercher?q=ali").get_json()
        self.assertEqual([g["nom"] for g in r["groupes"]], ["ALI"])
        ch.assert_called_once()

    def test_ajout_d_image_par_formulaire_et_apercu(self):
        import io
        from PIL import Image
        def png():
            t = io.BytesIO(); Image.new("RGB", (640, 640), (10, 120, 200)).save(t, "PNG"); return t.getvalue()
        faux = {"id": "t1", "etat": "pret"}
        with mock.patch.object(self.A.travaux, "ajouter_image", return_value=(faux, "")) as aj:
            r = self.c.post("/api/travaux/t1/image", data={"type": "couverture", "fichier": (io.BytesIO(png()), "ma_pochette.png")}, content_type="multipart/form-data")
        self.assertEqual((r.status_code, r.get_json()), (200, faux))
        self.assertEqual(aj.call_args.args[1], "couverture")
        self.assertTrue(aj.call_args.args[2].startswith(b"\x89PNG"))
        # rien envoyé
        self.assertEqual(self.c.post("/api/travaux/t1/image", data={"type": "logo"}, content_type="multipart/form-data").status_code, 400)
        # une adresse du réseau local est refusée avant toute lecture
        r = self.c.post("/api/travaux/t1/image", data={"type": "logo", "lien": "http://192.168.1.134:8096/x.png"}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Internet", r.get_json()["error"])
        # erreur renvoyée par le travail : transmise telle quelle
        with mock.patch.object(self.A.travaux, "ajouter_image", return_value=(None, "Ce fichier n'est pas une image lisible")):
            r = self.c.post("/api/travaux/t1/image", data={"type": "logo", "fichier": (io.BytesIO(b"abc"), "a.png")}, content_type="multipart/form-data")
        self.assertEqual((r.status_code, "image lisible" in r.get_json()["error"]), (400, True))

    def test_apercu_d_une_image_ajoutee(self):
        from src import images_perso
        images_perso.configurer(Path(self.tmp.name))
        import io
        from PIL import Image
        t = io.BytesIO(); Image.new("RGB", (400, 400), (1, 2, 3)).save(t, "PNG")
        e = images_perso.ajouter(self.MB, "couverture", t.getvalue(), "x", "album")
        r = self.c.get(f"/api/perso/{self.MB}/{e['id']}")
        self.assertEqual((r.status_code, r.data[:2]), (200, b"\xff\xd8"))                      # JPEG
        self.assertEqual(self.c.get(f"/api/perso/{self.MB}/{'0' * 12}").status_code, 404)
        self.assertEqual(self.c.get(f"/api/perso/{self.MB}/../../etc/passwd").status_code, 404)
        images_perso.configurer(self.A.BASE_DIR / "data")

    def test_convention_par_groupe_apercu_puis_application(self):
        from PIL import Image
        album = self.racine / "BLACKPINK" / "BORN PINK"
        album.mkdir(parents=True, exist_ok=True)
        (album / "BLACKPINK - BORN PINK - Shut Down.mkv").write_bytes(b"V")
        Image.new("RGB", (1280, 720), (1, 2, 3)).save(album / "BLACKPINK - BORN PINK - Shut Down-poster.jpg")
        Image.new("RGB", (1000, 1000), (9, 9, 9)).save(album / "folder.jpg")
        fich = self.A.CACHE_DIR / "groupes" / f"{self.MB}.json"
        fich.parent.mkdir(parents=True, exist_ok=True)
        fich.write_text(json.dumps({"fiche": {"nom": "BLACKPINK", "alias": []}, "clips": []}))
        with mock.patch.object(self.A.rangement, "prevenir_emby", return_value="Emby est prévenu."):
            g = self.c.get("/api/convention/" + self.MB).get_json()
            self.assertEqual([(c["titre"], c["album"], c["vignette"]) for c in g["clips"]], [("Shut Down", "BORN PINK", True)])
            self.assertFalse((album / "BLACKPINK - BORN PINK - Shut Down-thumb.jpg").exists())          # l'aperçu ne modifie rien
            with mock.patch.object(self.A, "BASE_DIR", Path(self.tmp.name)):
                r = self.c.post("/api/convention/" + self.MB).get_json()
        self.assertEqual((r["ok"], len(r["faits"]), r["emby"]), (True, 1, "Emby est prévenu."))
        self.assertTrue((album / "BLACKPINK - BORN PINK - Shut Down-thumb.jpg").exists())
        self.assertEqual(self.c.get("/api/convention/" + self.MB).get_json()["clips"], [])                # plus rien à faire ensuite
        self.assertEqual(self.c.get("/api/convention/11111111-2222-3333-4444-555555555555").status_code, 404)     # groupe inconnu ou sans dossier

    def test_pages_et_outils(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/reglages").status_code, 200)
        o = self.c.get("/api/outils").get_json()
        self.assertIn("yt_dlp", o)
        self.assertEqual(self.c.post("/api/settings/dossier", json={"dossier": "/nexiste/pas"}).status_code, 400)



class TitreEntreParenthesesTest(unittest.TestCase):
    """Cas LED Apple : « LEDApple(레드애플) _ Let the wind blow(바람아 불어라) MV » n'était pas reconnue comme « 바람아 불어라 » (le titre coréen est entre parenthèses)."""
    VIDEO = {"id": "fbkixXucdIw", "title": "LEDApple(레드애플) _ Let the wind blow(바람아 불어라) MV", "channel": "1theK (원더케이)", "channel_id": "UCweOkPb1wVVH0Q0Tlj4a5Pw", "duration": 220}

    def test_le_titre_cherche_est_retrouve_meme_entre_parentheses(self):
        for titre in ("바람아 불어라", "Let the wind blow"):
            c = formats.candidats("Led Apple", titre, lambda q, o: {"entries": [self.VIDEO]})
            self.assertEqual([x["id"] for x in c], ["fbkixXucdIw"], titre)
            self.assertTrue(c[0]["officielle"])                                        # 1theK est un label reconnu
        v2 = {**self.VIDEO, "title": "바람아 불어라 (Let the wind blow) MV"}
        self.assertEqual(len(formats.candidats("Led Apple", "Let the wind blow", lambda q, o: {"entries": [v2]})), 1)     # et dans l'autre sens

    def test_pas_de_faux_positif(self):
        self.assertEqual(formats.candidats("Led Apple", "Autre chanson", lambda q, o: {"entries": [self.VIDEO]}), [])

    def test_un_titre_entre_parentheses_ne_contourne_pas_les_exclusions(self):
        v = {**self.VIDEO, "title": "LEDApple(레드애플) _ Let the wind blow(바람아 불어라) Dance Practice"}
        self.assertEqual(formats.candidats("Led Apple", "바람아 불어라", lambda q, o: {"entries": [v]}), [])


class OfficielSeulementTest(unittest.TestCase):
    def test_lives_emissions_et_coulisses_jamais_proposes(self):
        entrees = [
            {"id": "ok1", "title": "aespa 에스파 'Supernova' MV", "channel": "SMTOWN", "duration": 194},
            {"id": "tv1", "title": "'최초 공개' aespa - Supernova #엠카운트다운 EP.843", "channel": "Mnet K-POP", "duration": 198},
            {"id": "ft1", "title": "aespa - Supernova / THE FIRST TAKE", "channel": "THE FIRST TAKE", "duration": 271},
            {"id": "lv1", "title": "aespa - Supernova (Live at Coachella)", "channel": "aespa", "duration": 240},
            {"id": "cg1", "title": "aespa 'Supernova' Camerawork Guide", "channel": "aespa", "duration": 255},
            {"id": "dp1", "title": "aespa 'Supernova' Dance Practice", "channel": "SMTOWN", "duration": 200},
            {"id": "ly1", "title": "aespa 'Supernova' Lyrics", "channel": "Jaeguchi", "duration": 179},
            {"id": "co1", "title": "aespa - Supernova | Concert 2024 Tour", "channel": "SMTOWN", "duration": 210}]
        c = formats.candidats("aespa", "Supernova", _faux_youtube(entrees, []))
        self.assertEqual([x["id"] for x in c], ["ok1"])

    def test_republications_de_fans_cachees_quand_la_chaine_officielle_existe(self):
        entrees = [{"id": "of", "title": "aespa 'Savage' MV", "channel": "SMTOWN", "duration": 259},
                   {"id": "fan", "title": "[4K 60FPS] aespa 'Savage' MV | REQUEST", "channel": "Black Hive Studio", "duration": 259}]
        c = formats.candidats("aespa", "Savage", _faux_youtube(entrees, []))
        self.assertEqual([(x["id"], x["officielle"]) for x in c], [("of", True)])
        sans = formats.candidats("aespa", "Savage", _faux_youtube(entrees[1:], []))
        self.assertEqual([(x["id"], x["officielle"]) for x in sans], [("fan", False)])           # rien d'officiel : montré mais signalé

    def test_un_titre_qui_contient_un_mot_interdit_reste_possible(self):
        entrees = [{"id": "lw", "title": "Red Velvet 'Live Wire' MV", "channel": "SMTOWN", "duration": 200}]
        self.assertEqual([x["id"] for x in formats.candidats("Red Velvet", "Live Wire", _faux_youtube(entrees, []))], ["lw"])


class ApercuTest(unittest.TestCase):
    def test_candidats_pour_regarder_et_choisir(self):
        extraire = _faux_youtube(FormatsTest.ENTREES, [])
        c = formats.candidats("BLACKPINK", "Pink Venom", extraire)
        self.assertEqual(c[0]["id"], "bbb")
        self.assertTrue(all(x["note"] > -100 for x in c))                      # « Autre chose » n'est pas proposé
        self.assertTrue(c[0]["url"].endswith("bbb"))

    def test_video_choisie_est_utilisee_directement(self):
        appels = []
        def extraire(q, o):
            appels.append(q)
            return {"title": "t", "channel": "c", "duration": 190, "formats": FormatsTest.FORMATS}
        r = formats.analyser("BLACKPINK", "Pink Venom", extraire, video_id="abcdefghijk")
        self.assertEqual(r["video"]["id"], "abcdefghijk")
        self.assertFalse(any(str(a).startswith("ytsearch") for a in appels))   # pas de recherche : la vidéo est déjà choisie
        r2 = formats.analyser("BLACKPINK", "Pink Venom", extraire, video_id="pas valide")
        self.assertTrue(any(str(a).startswith("ytsearch") for a in appels))    # identifiant douteux : on cherche


class ListeQualitesTest(unittest.TestCase):
    FMTS = [{"format_id": "137", "height": 1080, "width": 1920, "vcodec": "avc1.640028", "tbr": 4016, "fps": 24, "filesize": 92_000_000},
            {"format_id": "248", "height": 1080, "width": 1920, "vcodec": "vp09.00.40.08", "tbr": 2129, "fps": 24, "filesize": 49_000_000},
            {"format_id": "614", "height": 1080, "width": 1920, "vcodec": "vp09.00.40.08", "tbr": 4281, "fps": 24, "format_note": "Premium", "filesize": 99_000_000},
            {"format_id": "271", "height": 1440, "width": 2560, "vcodec": "vp09.00.50.08", "tbr": 8074, "fps": 24, "filesize": 186_000_000},
            {"format_id": "313", "height": 2160, "width": 3840, "vcodec": "vp09.00.50.08", "tbr": 17045, "fps": 24, "filesize": 393_000_000},
            {"format_id": "401", "height": 2160, "width": 3840, "vcodec": "av01.0.12M.08", "tbr": 12607, "fps": 24, "filesize": 291_000_000},
            {"format_id": "135", "height": 480, "width": 854, "vcodec": "avc1.4D401E", "tbr": 960, "fps": 24, "filesize": 22_000_000},
            {"format_id": "140", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 129, "filesize": 3_100_000}]

    def test_une_ligne_par_resolution_et_codec_du_plus_haut_au_plus_bas(self):
        r = formats.liste_qualites("gQlMMD8auMs", lambda q, o: {"title": "t", "duration": 194, "formats": self.FMTS})
        self.assertEqual(r["etat"], "ok")
        self.assertEqual([(q["resolution"], q["codec"], q["format_id"]) for q in r["qualites"]],
                         [("2160p", "vp9", "313"), ("2160p", "av1", "401"), ("1440p", "vp9", "271"), ("1080p", "vp9", "614"), ("1080p", "h264", "137")])
        self.assertTrue(next(q for q in r["qualites"] if q["format_id"] == "614")["premium"])          # le flux Premium gagne à 1080p vp9
        self.assertFalse(any(q["resolution"] == "480p" for q in r["qualites"]))                       # en dessous de 720p : pas proposé

    def test_taille_image_plus_son(self):
        r = formats.liste_qualites("gQlMMD8auMs", lambda q, o: {"duration": 194, "formats": self.FMTS})
        q = r["qualites"][0]
        self.assertEqual((q["taille_image"], q["taille"]), (393_000_000, 393_000_000 + 3_100_000))
        self.assertEqual(r["son"]["debit_kbps"], 129)

    def test_panne_et_identifiant(self):
        self.assertEqual(formats.liste_qualites("x")["etat"], "erreur")
        def casse(q, o):
            raise RuntimeError("HTTP 429")
        self.assertIn("429", formats.liste_qualites("abcdefghijk", casse)["erreur"])


class VersionsTest(unittest.TestCase):
    FORMATS = [{"format_id": "313", "height": 2160, "width": 3840, "vcodec": "vp09.00.50.08", "tbr": 17045, "fps": 24, "filesize": 393_000_000},
               {"format_id": "401", "height": 2160, "width": 3840, "vcodec": "av01.0.12M.08", "tbr": 12607, "fps": 24, "filesize": 291_000_000},
               {"format_id": "251", "acodec": "opus", "vcodec": "none", "abr": 133, "filesize": 3_000_000},
               {"format_id": "140", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 129, "filesize": 3_100_000}]

    def test_format_et_taille_d_une_version(self):
        r = formats.infos_version("gQlMMD8auMs", lambda q, o: {"title": "t", "channel": "BLACKPINK", "duration": 194, "upload_date": "20220819", "formats": self.FORMATS})
        self.assertEqual((r["etat"], r["format"]["resolution"], r["format"]["codec"]), ("ok", "2160p", "vp9"))
        self.assertEqual(r["taille"], 393_000_000 + 3_100_000)                  # meilleure image + son AAC (celui qui sera pris)
        self.assertEqual(r["format"]["audio_aac"]["debit_kbps"], 129)
        self.assertEqual(r["annee"], "2022")

    def test_taille_estimee_quand_youtube_ne_la_donne_pas(self):
        fmts = [{"format_id": "1", "height": 1080, "vcodec": "vp9", "tbr": 4000}, {"format_id": "2", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 128}]
        r = formats.infos_version("abcdefghijk", lambda q, o: {"duration": 200, "formats": fmts})
        self.assertEqual(r["taille"], int(4000 * 1000 * 200 / 8) + int(128 * 1000 * 200 / 8))

    def test_identifiant_invalide_et_panne(self):
        self.assertEqual(formats.infos_version("pas valide")["etat"], "erreur")
        def casse(q, o):
            raise RuntimeError("HTTP 429")
        r = formats.infos_version("abcdefghijk", casse)
        self.assertEqual(r["etat"], "erreur")
        self.assertIn("429", r["erreur"])


if __name__ == "__main__":
    unittest.main()


class RechercheGroupesTest(unittest.TestCase):
    @staticmethod
    def _art(i, nom, score, **k):
        return {"id": f"0000000{i}-0000-0000-0000-000000000000", "name": nom, "score": score, **k}

    def test_le_nom_exact_passe_avant_les_noms_qui_le_contiennent(self):
        """Bug vu : « ALI » renvoyait Nusrat Fateh Ali Khan, Brother Ali… et le groupe ALI n'apparaissait pas."""
        reponse = {"artists": [self._art(1, "Nusrat Fateh Ali Khan", 100), self._art(2, "Brother Ali", 95), self._art(3, "Ali Shaheed Muhammad", 93),
                               self._art(4, "ALI", 92, type="Group", country="JP", disambiguation="ALIEN LIBERTY INTERNATIONAL")]}
        with mock.patch.object(sources, "_get", return_value=reponse):
            r = sources.chercher_groupes("ALI")
        self.assertEqual(r[0]["nom"], "ALI")
        self.assertEqual((r[0]["pays"], r[0]["precision"]), ("JP", "ALIEN LIBERTY INTERNATIONAL"))
        self.assertEqual([x["nom"] for x in r[1:]], ["Ali Shaheed Muhammad", "Nusrat Fateh Ali Khan", "Brother Ali"])       # puis ceux qui COMMENCENT par ce nom, puis les autres par pertinence

    def test_alias_et_ecritures_voisines(self):
        reponse = {"artists": [self._art(1, "Quelqu'un d'autre", 100), self._art(2, "i‐dle", 90, aliases=[{"name": "(G)I-DLE"}, {"name": "아이들"}])]}
        with mock.patch.object(sources, "_get", return_value=reponse):
            self.assertEqual(sources.chercher_groupes("(G)I-DLE")[0]["nom"], "i‐dle")                  # trouvé par son alias
            self.assertEqual(sources.chercher_groupes("아이들")[0]["nom"], "i‐dle")

    def test_la_requete_ne_confond_pas_la_ponctuation_avec_des_operateurs(self):
        self.assertEqual(sources._lucene("(G)I-DLE"), r"\(G\)I\-DLE")
        self.assertEqual(sources._lucene("AC/DC"), r"AC\/DC")
        with mock.patch.object(sources, "_get", return_value={"artists": []}) as g:
            sources.chercher_groupes("(G)I-DLE")
        self.assertEqual(g.call_args.args[1]["query"], r"\(G\)I\-DLE")
        self.assertEqual(g.call_args.args[1]["limit"], 40)

    def test_adresse_de_fiche_musicbrainz(self):
        self.assertEqual(sources.mbid_dans("https://musicbrainz.org/artist/a0bb95b8-9fd9-47dd-af28-d4210f7f2a11"), "a0bb95b8-9fd9-47dd-af28-d4210f7f2a11")
        self.assertEqual(sources.mbid_dans("A0BB95B8-9FD9-47DD-AF28-D4210F7F2A11"), "a0bb95b8-9fd9-47dd-af28-d4210f7f2a11")
        self.assertEqual(sources.mbid_dans("ALI"), "")
        with mock.patch.object(sources, "_get", return_value={"id": "a0bb95b8-9fd9-47dd-af28-d4210f7f2a11", "name": "ALI", "type": "Group", "country": "JP"}):
            self.assertEqual(sources.artiste_par_mbid("a0bb95b8-9fd9-47dd-af28-d4210f7f2a11")["nom"], "ALI")
        with mock.patch.object(sources, "_get", return_value=None):
            self.assertIsNone(sources.artiste_par_mbid("a0bb95b8-9fd9-47dd-af28-d4210f7f2a11"))


class ChainesTest(unittest.TestCase):
    CID = "UCWFMjDuWKlct0jy89k2kkRA"

    def test_identifiant_dans_l_adresse(self):
        from src import chaines
        self.assertEqual(chaines.id_depuis_url(f"https://www.youtube.com/channel/{self.CID}"), self.CID)
        self.assertEqual(chaines.id_depuis_url("https://www.youtube.com/@VIVIZ_official"), "")        # à résoudre auprès de YouTube

    def test_resoudre_refuse_ce_qui_n_est_pas_une_chaine_youtube(self):
        from src import chaines
        for mauvais in ("https://exemple.fr/x", "pas une adresse", f"https://www.youtube.com/watch?v=9JFi7MmjtGA", "https://youtu.be/9JFi7MmjtGA", ""):
            with self.assertRaises(chaines.ChaineError):
                chaines.resoudre(mauvais, extraire=lambda u, o: {})

    def test_resoudre_lit_nom_et_identifiant(self):
        from src import chaines
        r = chaines.resoudre("youtube.com/@VIVIZ_official", extraire=lambda u, o: {"channel": "Viviz", "channel_id": self.CID})
        self.assertEqual(r, {"id": self.CID, "nom": "Viviz", "url": f"https://www.youtube.com/channel/{self.CID}"})
        with self.assertRaises(chaines.ChaineError):
            chaines.resoudre("https://www.youtube.com/@x", extraire=lambda u, o: {"channel": "x"})   # pas d'identifiant : pas une chaîne

    def test_musicbrainz_donne_la_chaine_du_groupe(self):
        art = {"id": "x", "name": "VIVIZ", "relations": [{"type": "youtube", "url": {"resource": f"https://www.youtube.com/channel/{self.CID}"}}]}
        with mock.patch.object(sources, "_get", return_value=art):
            self.assertEqual(sources.fiche_mb("x")["youtube"], [f"https://www.youtube.com/channel/{self.CID}"])

    def test_video_d_une_chaine_officielle_est_reconnue_meme_sans_mot_cle_de_label(self):
        e = {"id": "aaaaaaaaaaa", "title": "VIVIZ - MANIAC", "channel": "BPM Entertainment", "channel_id": "UC6YMr57knEIYXOOKMmYAFXQ", "duration": 196}
        self.assertLess(formats._note(e, "VIVIZ", "MANIAC"), 90)                                         # sans la chaîne : « non reconnue »
        self.assertGreaterEqual(formats._note(e, "VIVIZ", "MANIAC", {"UC6YMr57knEIYXOOKMmYAFXQ"}), 40 + 35)
        c = formats.candidats("VIVIZ", "MANIAC", lambda q, o: {"entries": [e]}, chaines={"UC6YMr57knEIYXOOKMmYAFXQ"})
        self.assertTrue(c[0]["officielle"])
        self.assertEqual(c[0]["channel_id"], "UC6YMr57knEIYXOOKMmYAFXQ")
        self.assertFalse(formats.candidats("VIVIZ", "MANIAC", lambda q, o: {"entries": [e]})[0]["officielle"])

    def test_une_chaine_officielle_ne_rend_pas_un_live_acceptable(self):
        e = {"id": "bbbbbbbbbbb", "title": "VIVIZ - 'MANIAC' Dance Practice", "channel": "VIVIZ", "channel_id": self.CID, "duration": 200}
        self.assertEqual(formats._note(e, "VIVIZ", "MANIAC", {self.CID}), -100)                         # chorégraphie : toujours écartée

    def test_decouverte_dans_les_videos_de_la_chaine(self):
        videos = [{"id": "ccccccccccc", "title": "VIVIZ (비비지) - 'Love Love Love' MV", "channel": "VIVIZ", "channel_id": self.CID, "duration": 200},
                  {"id": "ddddddddddd", "title": "VIVIZ (비비지) - 'Love Love Love' Concert Clip", "channel": "VIVIZ", "channel_id": self.CID, "duration": 209},
                  {"id": "eeeeeeeeeee", "title": "VIVIZ WORLD TOUR 'NEW LEGACY' LOG EP.08", "channel": "VIVIZ", "channel_id": self.CID, "duration": 900}]
        r = formats.decouvrir("VIVIZ", {"love love love"}, lambda q, o: {"entries": []}, chaines={self.CID}, videos_chaine=lambda cid: videos)
        self.assertEqual([(x["titre"], x["video_id"], x["officielle"]) for x in r], [("Love Love Love", "ccccccccccc", True)])


class SourcesOfficiellesTest(unittest.TestCase):
    PL = "PLG8DKAYW6M264immTa4KjaGkYBF1FHJ5X"
    IDLE = "UCritGVo7pLJLUS8wEu32vow"

    def test_playlist_reconnue_mais_pas_un_mix_ni_une_video(self):
        from src import chaines
        r = chaines.resoudre_source(f"https://www.youtube.com/playlist?list={self.PL}",
                                    extraire=lambda u, o: {"title": "Music Videos", "channel": "i-dle (아이들)", "channel_id": self.IDLE})
        self.assertEqual((r["type"], r["id"], r["nom"], r["channel_id"]), ("playlist", self.PL, "Music Videos", self.IDLE))
        with self.assertRaises(chaines.ChaineError):                                                # un « mix » automatique n'est pas une playlist
            chaines.resoudre_source("https://www.youtube.com/watch?v=AE8yKldv_oo&list=RD83C3TZ4Zm_o&index=10", extraire=lambda u, o: {})
        r = chaines.resoudre_source("https://www.youtube.com/@x", extraire=lambda u, o: {"channel": "x", "channel_id": self.IDLE})
        self.assertEqual(r["type"], "chaine")

    def test_nom_de_l_artiste_dans_un_titre(self):
        from src.chaines import contient_nom
        self.assertTrue(contient_nom("우기 (YUQI) - 'Bonnie & Clyde' Official Music Video", ["YUQI", "우기"]))
        self.assertTrue(contient_nom("(여자)아이들((G)I-DLE) - 'Queencard' Official Music Video", ["i‐dle", "(G)I-DLE"]))   # tiret différent
        self.assertFalse(contient_nom("MINNIE 'to the moon :)' Official Music Video", ["YUQI", "우기"]))
        self.assertFalse(contient_nom("Superstar remix", ["IU"]))                                           # nom court : mot entier seulement
        self.assertTrue(contient_nom("IU - Celebrity MV", ["IU"]))

    def test_playlist_officielle_donne_les_clips_solo_meme_sans_morceau_connu_de_musicbrainz(self):
        """Cas YUQI : « Bonnie & Clyde » n'est pas dans les morceaux de MusicBrainz, mais la playlist « Music Videos » d'i-dle le publie."""
        entrees = [{"id": "AE8yKldv_oo", "title": "우기 (YUQI) - 'Bonnie & Clyde' Official Music Video", "channel": "i-dle (아이들)", "channel_id": self.IDLE, "duration": 214},
                   {"id": "bbbbbbbbbbb", "title": "MINNIE 'to the moon :)' Official Music Video", "channel": "i-dle (아이들)", "channel_id": self.IDLE, "duration": 236},
                   {"id": "ccccccccccc", "title": "YUQI 'Stay' Official Music Video", "channel": "i-dle (아이들)", "channel_id": self.IDLE, "duration": 234},
                   {"id": "ddddddddddd", "title": "우기 (YUQI) - 'Freak' Official Lyric Video", "channel": "i-dle (아이들)", "channel_id": self.IDLE, "duration": 190},
                   {"id": "eeeeeeeeeee", "title": "우기 (YUQI) 'Giant' Official Music Video", "channel": "Autre", "channel_id": "UCautre0000000000000000", "duration": 212}]
        r = formats.decouvrir("YUQI", {"stay"}, lambda q, o: {"entries": []}, chaines={self.IDLE}, videos_chaine=lambda cid: [], playlists=[self.PL], videos_playlist=lambda pid: entrees, noms=["YUQI", "우기"])
        self.assertEqual(sorted(x["titre"] for x in r), ["Bonnie & Clyde", "Giant", "Stay"])             # pas MINNIE (autre artiste), pas la vidéo de paroles
        self.assertTrue(next(x for x in r if x["titre"] == "Bonnie & Clyde")["officielle"])
        self.assertEqual(next(x for x in r if x["titre"] == "Bonnie & Clyde")["video_id"], "AE8yKldv_oo")

    def test_date_de_mise_en_ligne_lue_pour_les_clips_sans_morceau_connu(self):
        entrees = [{"id": "AE8yKldv_oo", "title": "우기 (YUQI) - 'Bonnie & Clyde' Official Music Video", "channel": "i-dle", "channel_id": self.IDLE, "duration": 214}]
        def extraire(q, o):
            return {"entries": []} if str(q).startswith("ytsearch") else {"upload_date": "20210520"}
        r = formats.decouvrir("YUQI", {"stay"}, extraire, chaines={self.IDLE}, videos_chaine=lambda cid: [], playlists=[self.PL], videos_playlist=lambda pid: entrees, noms=["YUQI"])
        self.assertEqual(r[0]["date"], "2021-05-20")

    def test_album_propose_d_apres_la_date(self):
        import app as A
        rgs = [{"id": "ep", "titre": "A Page", "type": "EP", "date": "2021-05-20"}, {"id": "s", "titre": "Bonnie & Clyde", "type": "Single", "date": "2021-05-20"},
               {"id": "x", "titre": "YUQ1", "type": "Album", "date": "2024-01-15"}]
        self.assertEqual(A._proposer_album(rgs, "20210520"), ("A Page", "ep"))              # l'EP du jour avant le single du même jour
        self.assertEqual(A._proposer_album(rgs, "2021-05-25"), ("A Page", "ep"))
        self.assertEqual(A._proposer_album(rgs, "20230101"), ("", ""))                       # rien d'assez proche
        self.assertEqual(A._proposer_album(rgs, ""), ("", ""))

    def test_recherche_libre_exige_toujours_un_morceau_connu(self):
        e = {"id": "fffffffffff", "title": "YUQI - 'Inconnu' MV", "channel": "Chaîne quelconque", "channel_id": "UCx00000000000000000000", "duration": 200}
        self.assertEqual(formats.decouvrir("YUQI", {"stay"}, lambda q, o: {"entries": [e]}, noms=["YUQI"]), [])

    def test_musicbrainz_groupe_dont_l_artiste_est_membre(self):
        art = {"id": "x", "name": "YUQI", "relations": [{"type": "member of band", "direction": "forward", "ended": False, "artist": {"id": "0068ae6c-7156-40f9-a81f-39294af6a549", "name": "i‐dle"}},
                                                         {"type": "member of band", "direction": "backward", "artist": {"id": "zzz", "name": "Un membre"}},
                                                         {"type": "member of band", "direction": "forward", "ended": True, "artist": {"id": "old", "name": "Ancien groupe"}}]}
        with mock.patch.object(sources, "_get", return_value=art):
            self.assertEqual(sources.fiche_mb("x")["parents"], [{"id": "0068ae6c-7156-40f9-a81f-39294af6a549", "nom": "i‐dle"}])


class DecouverteTest(unittest.TestCase):
    PISTES = {"maniac", "bop bop", "reset"}

    def _yt(self, entrees):
        return lambda q, o: {"entries": entrees}

    def test_titres_de_videos(self):
        t = formats.titre_du_clip
        self.assertEqual(t("[MV] VIVIZ(비비지) _ MANIAC", "VIVIZ"), "MANIAC")
        self.assertEqual(t("aespa 에스파 'Supernova' MV", "aespa"), "Supernova")
        self.assertEqual(t("VIVIZ - Bop Bop! (Official MV)", "VIVIZ"), "Bop Bop!")
        self.assertEqual(t("(G)I-DLE - 'Queencard' Official Music Video", "(G)I-DLE"), "Queencard")

    def test_seuls_les_morceaux_des_albums_et_les_chaines_officielles_sont_gardes(self):
        entrees = [{"id": "aaaaaaaaaaa", "title": "[MV] VIVIZ(비비지) _ MANIAC", "channel": "1theK (원더케이)", "duration": 196},
                   {"id": "bbbbbbbbbbb", "title": "VIVIZ (비비지) 'MANIAC' Dance Practice", "channel": "BPM Entertainment", "duration": 200},      # chorégraphie : jamais
                   {"id": "ccccccccccc", "title": "VIVIZ - Interview 'Autre'", "channel": "Mnet K-POP", "duration": 300},                       # pas un morceau
                   {"id": "ddddddddddd", "title": "VIVIZ 'Reset' MV", "channel": "Fan Channel", "duration": 190}]                                # chaîne de fan : écartée
        r = formats.decouvrir("VIVIZ", self.PISTES, self._yt(entrees))
        self.assertEqual([(x["titre"], x["video_id"], x["officielle"]) for x in r], [("MANIAC", "aaaaaaaaaaa", True)])

    def test_youtube_en_panne_ne_casse_rien(self):
        def casse(q, o):
            raise RuntimeError("HTTP 429")
        self.assertEqual(formats.decouvrir("VIVIZ", self.PISTES, casse), [])

    def test_clips_mb_ajoute_les_clips_decouverts_avec_leur_album_et_leur_video(self):
        def faux(url, params=None, max_essais=5):
            if url.endswith("/recording"):
                return {"count": 0, "recordings": []}
            if url.endswith("/release-group"):
                return {"count": 1, "release-groups": [{"id": "rgV", "title": "VERSUS", "primary-type": "EP", "first-release-date": "2023-11-02"}]}
            return {"release-count": 1, "releases": [{"title": "VERSUS", "date": "2023-11-02", "release-group": {"id": "rgV", "title": "VERSUS", "primary-type": "EP", "first-release-date": "2023-11-02"},
                                                      "media": [{"tracks": [{"title": "MANIAC"}, {"title": "Autre piste"}]}]}]}
        with mock.patch.object(sources, "_get", side_effect=faux):
            r = sources.clips_mb("x", "VIVIZ", decouverte=lambda pistes: [{"titre": "MANIAC", "video_id": "aaaaaaaaaaa", "duree": 196}] if "maniac" in pistes else [])
        m = next(c for c in r if c["titre"] == "MANIAC")
        self.assertEqual((m["album"], m["annee"], m["rg"], m["video_id"], m["source"]), ("VERSUS", "2023", "rgV", "aaaaaaaaaaa", "youtube"))
