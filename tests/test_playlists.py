import unittest
from unittest import mock

from src import playlists


class PlaylistsTest(unittest.TestCase):
    def test_identifiants(self):
        self.assertEqual(playlists.id_playlist("123456"), "123456")
        self.assertEqual(playlists.id_playlist("https://www.deezer.com/fr/playlist/3155776842?utm=x"), "3155776842")
        self.assertEqual(playlists.id_playlist("https://www.deezer.com/playlist/77"), "77")
        self.assertEqual(playlists.id_playlist("n'importe quoi"), "")

    def test_titre_cle_et_versions(self):
        self.assertEqual(playlists.titre_cle("Hype Boy (Korean Ver.)"), playlists.titre_cle("Hype Boy"))
        self.assertEqual(playlists.titre_cle("Spicy - Remastered 2024"), playlists.titre_cle("Spicy"))
        self.assertEqual(playlists.titre_cle("Song (feat. X)"), playlists.titre_cle("Song"))
        self.assertTrue(playlists.version_alternative("Savage (Remix)"))
        self.assertTrue(playlists.version_alternative("Savage - Instrumental"))
        self.assertFalse(playlists.version_alternative("Savage (feat. X)"))
        self.assertFalse(playlists.version_alternative("Savage"))

    def test_clip_du_morceau(self):
        clips = [{"titre": "Next Level"}, {"titre": "Savage"}]
        self.assertEqual(playlists.clip_du_morceau("Savage (Korean Ver.)", clips)["titre"], "Savage")
        self.assertIsNone(playlists.clip_du_morceau("Autre", clips))

    def test_morceaux_depuis_une_date(self):
        pages = [{"data": [{"id": 1, "title": "Vieux", "artist": {"name": "A"}, "time_add": 100},
                           {"id": 2, "title": "Neuf", "artist": {"name": "B"}, "time_add": 300},
                           {"id": 3, "title": "Moyen", "artist": {"name": "C"}, "time_add": 200}]}]
        with mock.patch.object(playlists, "_get", side_effect=pages):
            r = playlists.morceaux("9", 150)
        self.assertEqual([m["titre"] for m in r], ["Neuf", "Moyen"])           # le plus récent d'abord, le vieux est ignoré

    def test_date(self):
        self.assertGreater(playlists.date_en_ts("2026-10-15"), 0)
        self.assertEqual(playlists.date_en_ts("15/10/2026"), playlists.date_en_ts("2026-10-15"))
        self.assertEqual(playlists.date_en_ts(""), 0)
        with self.assertRaises(ValueError):
            playlists.date_en_ts("hier")


if __name__ == "__main__":
    unittest.main()


class TravailPlaylistTest(unittest.TestCase):
    def test_classement_des_morceaux(self):
        import tempfile
        from pathlib import Path
        import app
        tmp = Path(tempfile.mkdtemp())
        morceaux = [{"deezer_id": 1, "titre": "Spicy (Korean Ver.)", "artiste": "aespa", "ajoute": 5},
                    {"deezer_id": 2, "titre": "Spicy (Remix)", "artiste": "aespa", "ajoute": 4},
                    {"deezer_id": 3, "titre": "Chanson sans clip", "artiste": "aespa", "ajoute": 3},
                    {"deezer_id": 4, "titre": "Truc", "artiste": "Inconnu Total", "ajoute": 2},
                    {"deezer_id": 5, "titre": "Savage", "artiste": "aespa", "ajoute": 1}]
        groupe = {"clips": [{"titre": "Spicy", "album": "MY WORLD", "deja": False}, {"titre": "Savage", "album": "Savage", "deja": True}]}
        app._PL_ETAT["9"] = {"en_cours": True}
        with mock.patch.object(app, "CACHE_DIR", tmp), mock.patch.object(app.playlists, "morceaux", return_value=morceaux), \
                mock.patch.object(app.sources, "chercher_groupes", side_effect=lambda n, k=8: [{"mbid": "m1", "nom": "aespa"}] if n == "aespa" else []), \
                mock.patch.object(app, "_groupe_complet", return_value=groupe):
            app._chercher_clips_playlist("9", "2026-01-01")
        r = app._playlist_resultat.__wrapped__("9") if hasattr(app._playlist_resultat, "__wrapped__") else None
        import json
        r = json.loads((tmp / "playlists" / "9-resultat.json").read_text(encoding="utf-8"))
        self.assertEqual([l["etat"] for l in r["lignes"]], ["clip", "version", "sans_clip", "artiste_inconnu", "deja"])
        self.assertEqual(r["lignes"][0]["clip"], "Spicy")
        self.assertFalse(app._PL_ETAT["9"]["en_cours"])


class TestTitresDoubles(unittest.TestCase):
    def test_titre_double_et_video_clip(self):
        from src import playlists
        c = playlists.clip_du_morceau("어쩌다", [{"titre": "How Come? / 어쩌다－(video clip)"}])
        self.assertIsNotNone(c)
        self.assertIsNone(playlists.clip_du_morceau("Autre", [{"titre": "How Come? / 어쩌다－(video clip)"}]))

    def test_variantes_artiste(self):
        from src import playlists
        self.assertEqual(playlists.variantes_artiste("레이디스 코드 (LADIES` CODE)")[1:], ["레이디스 코드", "LADIES' CODE"])
