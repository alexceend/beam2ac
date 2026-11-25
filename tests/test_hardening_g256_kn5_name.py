import unittest

from src.mesh.build import check_kn5_name


class Kn5NameTest(unittest.TestCase):
    def test_validos(self):
        self.assertEqual(check_kn5_name("coche.kn5"), "coche.kn5")

    def test_invalidos(self):
        for bad in (
            "a/b.kn5",
            "..%sx.kn5" % chr(92),
            "..",
            "x.txt",
            "C:x.kn5",
            ".kn5",
            "a..b.kn5",
        ):
            with self.assertRaises(RuntimeError, msg=bad):
                check_kn5_name(bad)


if __name__ == "__main__":
    unittest.main()
