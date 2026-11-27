"""``bank_patch._find_each`` == ``find_all`` aguja por aguja (con solapes)."""

import random

from src.audio import bank_patch as bp


def test_find_each_matches_find_all():
    rnd = random.Random(0)
    for trial in range(300):
        alphabet = bytes(rnd.sample(range(256), 2 if trial % 2 else 256))
        buf = bytes(rnd.choice(alphabet) for _ in range(rnd.randint(0, 400)))
        needles = [bytes(rnd.choice(alphabet) for _ in range(16)) for _ in range(4)]
        for _ in range(6):  # agujas presentes en cualquier alineación, y solapadas
            if len(buf) >= 16:
                at = rnd.randrange(len(buf) - 15)
                needles.append(buf[at : at + 16])
        needles += [b"\x00" * 16, alphabet[:1] * 16]
        buf += needles[0] + alphabet[:1] * 40
        got = bp._find_each(buf, dict.fromkeys(needles))
        assert got == {n: bp.find_all(buf, n) for n in needles}
