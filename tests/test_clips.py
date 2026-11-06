"""MouFlip, premier morceau (lecture seule) : noms, clips déjà présents, choix de la vidéo YouTube et du meilleur format, routes.
Aucun réseau (MusicBrainz, TheAudioDB et YouTube sont remplacés), dossier « Video clip » fabriqué dans un dossier temporaire."""
import os
import sys
import tempfile
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
        self.assertEqual(sources._album_choisi(rel, "Kill This Love"), ("Kill This Love", "2019-04-09"))
        self.assertEqual(sources._album_choisi([], "Shut Down"), ("Shut Down", ""))

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
        A._CACHE_GROUPES.clear()
        self.c = A.app.test_client()
        self.c.environ_base["REMOTE_ADDR"] = "192.168.1.50"
        self.assertEqual(self.c.post("/login", data={"username": "admin", "password": "mdp-de-test-123"}).status_code, 302)

    def tearDown(self):
        self.A.DOSSIER_CLIPS = self._racine
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
                mock.patch.object(sources, "images_audiodb", return_value={"biographie": "Un groupe.", "images": {}}):
            d = self.c.get("/api/groupe/" + mb).get_json()
        self.assertEqual([(c["titre"], c["deja"]) for c in d["clips"]], [("Pink Venom", True), ("Shut Down", False)])
        self.assertTrue(d["clips"][1]["chemin_prevu"].endswith("/BLACKPINK/BORN PINK/BLACKPINK - BORN PINK - Shut Down.mkv"))
        self.assertEqual(d["nb_locaux"], 1)
        self.assertEqual(avant, sorted(str(p) for p in self.racine.rglob("*")))     # rien d'écrit dans « Video clip »

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

    def test_pages_et_outils(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/reglages").status_code, 200)
        o = self.c.get("/api/outils").get_json()
        self.assertIn("yt_dlp", o)
        self.assertEqual(self.c.post("/api/settings/dossier", json={"dossier": "/nexiste/pas"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
