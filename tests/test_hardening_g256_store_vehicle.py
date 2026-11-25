import tempfile
import unittest

from src.attrs import store


class VehicleNameTest(unittest.TestCase):
    def test_rechaza_vehiculo_con_ruta(self):
        class D:
            vehicle = "../x"
            id = "d1"
            supersedes = None
            target = "t"

        from unittest import mock

        with (
            tempfile.TemporaryDirectory() as t,
            mock.patch.object(store.S, "from_dict"),
            mock.patch.object(store.S, "to_dict", return_value={}),
        ):
            with self.assertRaises(store.StoreError):
                store.append_decision(t, D())


if __name__ == "__main__":
    unittest.main()
