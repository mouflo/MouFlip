import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import sons


def _cfg(tmp, fmt="m4a"):
    (tmp / "musique").mkdir(exist_ok=True)
    sons.configurer(tmp / "data", lambda: str(tmp / "musique"), lambda: str(tmp / "clips"), lambda: {"format": fmt, "debit_m4a": "256k", "debit_mp3": "320k"},
                    lambda: 89.0, None, None, ecriture=True)


def _info(titre, artiste="aespa", chaine="aespa", desc="Provided to YouTube by SM Entertainment\n\nSpicy", duree=197, **kw):
    return {"id": "x1", "title": titre, "track": titre, "channel": chaine, "artist": artiste, "description": desc, "duration": duree, **kw}


class ChoixDuSonTest(unittest.TestCase):
    def test_son_du_label_accepte(self):
        r = sons.choisir("aespa", "Spicy", 205, [_info("Spicy")])
        self.assertEqual(r["id"], "x1")

    def test_topic_accepte_sans_description(self):
        r = sons.choisir("JEON SOYEON", "NOBODY", 169, [_info("NOBODY (NOBODY)", artiste="SOYEON", chaine="SOYEON - Topic", desc="")])
        self.assertIsNotNone(r)

    def test_refus_fan_remix_live_et_mauvais_artiste(self):
        self.assertIsNone(sons.choisir("aespa", "Spicy", 205, [_info("Spicy", chaine="Rubik Music", desc="Spicy lyrics", artiste="")]))      # pas le son du label
        self.assertIsNone(sons.choisir("aespa", "Spicy", 205, [_info("Spicy (Remix)")]))
        self.assertIsNone(sons.choisir("aespa", "Spicy", 205, [_info("Spicy", artiste="Autre", chaine="Autre")]))
        self.assertIsNone(sons.choisir("aespa", "Spicy", 205, [_info("Spicy", duree=40)]))                                                  # durée absurde

    def test_la_duree_departage(self):
        a, b = _info("Spicy", duree=300), _info("Spicy", duree=200)
        b["id"] = "bon"
        self.assertEqual(sons.choisir("aespa", "Spicy", 205, [a, b])["id"], "bon")


class SurTest(unittest.TestCase):
    def test_sur_selon_la_duree(self):
        c = {"duree": 205}
        self.assertTrue(sons.sur(c, {"duration": 197})[0])
        self.assertFalse(sons.sur(c, {"duration": 120})[0])
        self.assertFalse(sons.sur({"duree": 0}, {"duration": 197})[0])
        self.assertFalse(sons.sur(c, {})[0])


class RangementSonTest(unittest.TestCase):
    def _pret(self, tmp, fmt="m4a"):
        _cfg(tmp, fmt)
        c = sons.ajouter("aespa", "m1", {"titre": "Spicy", "album": "MY WORLD", "annee": "2023", "duree": 200})
        rep = tmp / "data" / "sons" / c["id"]
        rep.mkdir(parents=True)
        f = rep / f"final.{fmt}"
        f.write_bytes(b"x" * 1000)
        c.update(etat="pret", fichier=str(f), piste=2)
        return c

    def test_chemin_et_rangement_sans_remplacement(self):
        tmp = Path(tempfile.mkdtemp())
        c = self._pret(tmp)
        p = sons.plan(c)
        self.assertTrue(p["chemin"].endswith("/aespa/MY WORLD/aespa - MY WORLD - 02 - Spicy.m4a"))
        r = sons.ranger(c["id"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(Path(p["chemin"]).read_bytes(), b"x" * 1000)
        self.assertEqual(sons.SONS[c["id"]]["etat"], "range")
        c2 = self._pret(tmp)                                               # même destination : refusé, rien n'est remplacé
        Path(p["chemin"]).write_bytes(b"ancien")
        r2 = sons.ranger(c2["id"])
        self.assertFalse(r2["ok"])
        self.assertEqual(Path(p["chemin"]).read_bytes(), b"ancien")

    def test_dossier_existant_reconnu_sans_majuscules(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "musique" / "Aespa" / "My World").mkdir(parents=True)
        c = self._pret(tmp)
        self.assertTrue(sons.plan(c)["dossier_album"].endswith("/Aespa/My World"))

    def test_morceau_deja_present_detecte(self):
        tmp = Path(tempfile.mkdtemp())
        _cfg(tmp)
        d = tmp / "musique" / "aespa" / "Armageddon"
        d.mkdir(parents=True)
        (d / "aespa - Armageddon - 01 - Supernova.mp3").write_bytes(b"x")
        c = {"artiste": "aespa", "alias": [], "titre": "Supernova"}
        self.assertTrue(sons._deja_dans_la_musique(c).endswith("Supernova.mp3"))
        self.assertEqual(sons._deja_dans_la_musique({**c, "titre": "Autre"}), "")

    def test_ranger_tous(self):
        tmp = Path(tempfile.mkdtemp())
        c = self._pret(tmp)
        r = sons.ranger_tous()
        self.assertEqual((r["ranges"], r["erreurs"]), (1, []))

    def test_doublon_en_file_et_reprise(self):
        tmp = Path(tempfile.mkdtemp())
        _cfg(tmp)
        with mock.patch.object(sons, "_reveiller"):
            a = sons.ajouter("aespa", "m1", {"titre": "Spicy"})
            b = sons.ajouter("aespa", "m1", {"titre": "Spicy"})
        self.assertEqual(a["id"], b["id"])
        sons.SONS[a["id"]]["etat"] = "telechargement"
        sons._sauver()
        _cfg(tmp)                                                           # redémarrage : le travail coupé reprend depuis le début
        self.assertEqual(sons.SONS[a["id"]]["etat"], "attente")


if __name__ == "__main__":
    unittest.main()
