import math
import unittest

import numpy as np

from bps import lab
from bps.prs import sync_pbch_core_mask
from bps.solver import solve_rstd
from bps.sync import cas_subframe, pss_sequence, split_cell_id, sss_indices, sss_sequence, sync_slot_grid

# Independent references.
# PSS_K: the PSS value is exp(-j pi k / 63); k per N_ID^(2) was read off srsRAN's srsran_pss_generate (rt-mbms-tx lib/src/phy/sync/pss.c,
# built and run on its own). srsRAN evaluates the phase in float32, so values agree to about 3e-4, not to rounding.
# SSS: srsRAN's srsran_sss_generate, subframe 0 and subframe 5 variants, for the cell identities listed ('+' is +1, '-' is -1). The full range
# of 504 identities was compared once against it with no mismatch; these are the edge cases kept here.
# M_TABLE: (m0, m1) for N_ID^(1) = 0..167, parsed from Table 6.11.2.1-1 of the TS 36.211 V19.3.0 text export.
PSS_K = {
    0: [0, 50, 24, 48, 122, 120, 42, 14, 36, 108, 104, 24, 120, 14, 84, 78, 122, 90, 108, 50, 42, 84, 50, 66, 6, 122, 36, 0, 14, 78, 66, 66, 78, 14, 0, 36, 122, 6, 66, 50, 84, 42, 50, 108, 90, 122, 78, 84, 14, 120, 24, 104, 108, 36, 14, 42, 120, 122, 48, 24, 50, 0],
    1: [0, 58, 48, 96, 76, 114, 84, 112, 72, 90, 40, 48, 114, 112, 42, 30, 76, 54, 90, 58, 84, 42, 58, 6, 12, 76, 72, 0, 112, 30, 6, 6, 30, 112, 0, 72, 76, 12, 6, 58, 42, 84, 58, 90, 54, 76, 30, 42, 112, 114, 48, 40, 90, 72, 112, 84, 114, 76, 96, 48, 58, 0],
    2: [0, 68, 78, 30, 50, 12, 42, 14, 54, 36, 86, 78, 12, 14, 84, 96, 50, 72, 36, 68, 42, 84, 68, 120, 114, 50, 54, 0, 14, 96, 120, 120, 96, 14, 0, 54, 50, 114, 120, 68, 84, 42, 68, 36, 72, 50, 96, 84, 14, 12, 78, 86, 36, 54, 14, 42, 12, 50, 30, 78, 68, 0],
}
SSS = {
    0: ("+++-+++++-++------+-++++-+++------+--+-+--++-++-++++-+---++++-", "+++-++-+-++++-+++-+--++-+-++-----+-----++++-++-++-+-+++-++----"),
    1: ("+-+++----+-++-++-++----+-+------+-+----+-+-+---++--++-++-+-+--", "+-+++-+-+--+----+++-+---+-------++---+-++---+-+-++-----++++-+-"),
    2: ("+++--+++++++-++--+-++--++-----+-+-++----++--++----+--+++++++-+", "+++--+-+--++++-+++-+-----+----+-++-+-+-----+-+++-+++++-+-+--++"),
    89: ("++-+-++--++-++-+-----+++--+++++++++++-+-++++--+---------+++--+", "--++-+++-+---+-++++++-+-+-+---+++-+-+---+-+-+-+++++---------+-"),
    90: ("+++-+-+-+-+--+-+--+++++--+++-----++--+----+---+-+++----+--+-+-", "+++---++-+------+--+----+-++---++--+--+++--+-+--++-+---+-+++--"),
    261: ("+-+-+++++-+------++-+-+--+++-+-+-+++---+--++--+++++----+-++-+-", "--+++------+++++--+-+---+-+-+-++++++++-++++--++-++-+-+--+-++--"),
    419: ("-----+-+-+---+----+-+--+++-++-+-++-+--+-----+++-+--+++--+-++--", "-+++-++--+-+---++++-++++++++--+++-++++--+++++++++-+--+------+-"),
    420: ("++++++++++++-+-+--+-+-+---+--+----+-------+---+++++--+-+-++++-", "+---++-++++--+--+-+-+--+----++-+---++-+-++---++-+---+---+----+"),
    501: ("+-++--++-+-+-+--++---+--++++-+-++--+--+-+--+----+------+--+--+", "-+--+-+++---+--+++-+--+++++++-----++--+---+++++----+-+--++---+"),
    502: ("+++--+--+-+++++++---+-+-++---+-+---+-++-++++-++++++-+++-----++", "---+++---++---+-+--+++-+++--+---+-++-++--+-++--+-++++-+++++-++"),
    503: ("+-+++-++---+--+-+-++--+------+++-----+++-++-+-+--+-+--+-+-+-+-", "-+----++++--+++++-+--+-+----+-+-+-+--+++++---+--++---+++-+--+-"),
}
M_TABLE = [
    (0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9), (9, 10), (10, 11), (11, 12),
    (12, 13), (13, 14), (14, 15), (15, 16), (16, 17), (17, 18), (18, 19), (19, 20), (20, 21), (21, 22), (22, 23), (23, 24),
    (24, 25), (25, 26), (26, 27), (27, 28), (28, 29), (29, 30), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6), (5, 7),
    (6, 8), (7, 9), (8, 10), (9, 11), (10, 12), (11, 13), (12, 14), (13, 15), (14, 16), (15, 17), (16, 18), (17, 19),
    (18, 20), (19, 21), (20, 22), (21, 23), (22, 24), (23, 25), (24, 26), (25, 27), (26, 28), (27, 29), (28, 30), (0, 3),
    (1, 4), (2, 5), (3, 6), (4, 7), (5, 8), (6, 9), (7, 10), (8, 11), (9, 12), (10, 13), (11, 14), (12, 15),
    (13, 16), (14, 17), (15, 18), (16, 19), (17, 20), (18, 21), (19, 22), (20, 23), (21, 24), (22, 25), (23, 26), (24, 27),
    (25, 28), (26, 29), (27, 30), (0, 4), (1, 5), (2, 6), (3, 7), (4, 8), (5, 9), (6, 10), (7, 11), (8, 12),
    (9, 13), (10, 14), (11, 15), (12, 16), (13, 17), (14, 18), (15, 19), (16, 20), (17, 21), (18, 22), (19, 23), (20, 24),
    (21, 25), (22, 26), (23, 27), (24, 28), (25, 29), (26, 30), (0, 5), (1, 6), (2, 7), (3, 8), (4, 9), (5, 10),
    (6, 11), (7, 12), (8, 13), (9, 14), (10, 15), (11, 16), (12, 17), (13, 18), (14, 19), (15, 20), (16, 21), (17, 22),
    (18, 23), (19, 24), (20, 25), (21, 26), (22, 27), (23, 28), (24, 29), (25, 30), (0, 6), (1, 7), (2, 8), (3, 9),
    (4, 10), (5, 11), (6, 12), (7, 13), (8, 14), (9, 15), (10, 16), (11, 17), (12, 18), (13, 19), (14, 20), (15, 21),
    (16, 22), (17, 23), (18, 24), (19, 25), (20, 26), (21, 27), (22, 28), (23, 29), (24, 30), (0, 7), (1, 8), (2, 9),
]


def signs(s):
    return np.array([1.0 if c == "+" else -1.0 for c in s])


class Sequences(unittest.TestCase):
    def test_pss_matches_an_independent_implementation(self):
        n = np.arange(62)
        for n2, ks in PSS_K.items():
            np.testing.assert_allclose(pss_sequence(n2), np.exp(-1j * np.pi * np.array(ks) / 63), atol=1e-12)   # the k are exact integers
        self.assertEqual(len(PSS_K), 3)

    def test_pss_is_the_zadoff_chu_expression(self):
        for n2, u in ((0, 25), (1, 29), (2, 34)):                         # Table 6.11.1.1-1
            d = pss_sequence(n2)
            for n in (0, 1, 30, 31, 61):
                expect = -u * n * (n + 1) / 63 if n <= 30 else -u * (n + 1) * (n + 2) / 63
                self.assertAlmostEqual(d[n], np.exp(1j * np.pi * expect), places=12)
            np.testing.assert_allclose(np.abs(d), 1.0)

    def test_sss_matches_an_independent_implementation(self):
        for cid, (s0, s5) in SSS.items():
            n1, n2 = split_cell_id(cid)
            np.testing.assert_array_equal(sss_sequence(n1, n2, 0), signs(s0), err_msg=f"cell {cid} subframe 0")
            np.testing.assert_array_equal(sss_sequence(n1, n2, 5), signs(s5), err_msg=f"cell {cid} subframe 5")
            for sf in (1, 4):
                np.testing.assert_array_equal(sss_sequence(n1, n2, sf), signs(s0))        # subframes 0 to 4 share the first variant
            for sf in (6, 9):
                np.testing.assert_array_equal(sss_sequence(n1, n2, sf), signs(s5))

    def test_the_two_sss_variants_differ(self):
        self.assertFalse(np.array_equal(sss_sequence(10, 1, 0), sss_sequence(10, 1, 5)))

    def test_m_indices_equal_table_6_11_2_1_1(self):
        self.assertEqual(len(M_TABLE), 168)
        for n1 in range(168):
            self.assertEqual(sss_indices(n1), M_TABLE[n1], n1)

    def test_cell_identity_split(self):
        self.assertEqual(split_cell_id(0), (0, 0))
        self.assertEqual(split_cell_id(503), (167, 2))
        self.assertEqual(split_cell_id(4), (1, 1))                       # 3 * 1 + 1
        for cid in range(504):
            n1, n2 = split_cell_id(cid)
            self.assertEqual(3 * n1 + n2, cid)

    def test_ranges_are_enforced(self):
        for call in (lambda: split_cell_id(504), lambda: split_cell_id(-1), lambda: pss_sequence(3), lambda: sss_indices(168),
                     lambda: sss_sequence(0, 3), lambda: sss_sequence(0, 0, 10)):
            with self.assertRaises(ValueError):
                call()


class Mapping(unittest.TestCase):
    def test_pss_and_sss_symbols_and_subcarriers(self):
        g = sync_slot_grid(4, 25)                                         # cell 4: N1 1, N2 1
        self.assertEqual(sorted(set(np.nonzero(g)[1])), [5, 6])           # SSS in the symbol before the last, PSS in the last
        k = np.flatnonzero(g[:, 6])
        self.assertEqual((k[0], k[-1], len(k)), (119, 180, 62))           # n - 31 + 150
        np.testing.assert_allclose(g[k, 6], pss_sequence(1))
        np.testing.assert_allclose(g[k, 5].real, sss_sequence(1, 1, 0))
        self.assertTrue(np.array_equal(np.flatnonzero(g[:, 5]), k))

    def test_extended_cp_uses_the_last_two_symbols_of_six(self):
        g = sync_slot_grid(1, 25, normal_cp=False)
        self.assertEqual(sorted(set(np.nonzero(g)[1])), [4, 5])

    def test_the_resource_elements_are_exactly_those_the_prs_model_keeps_clear(self):
        for n_rb in (6, 25, 50, 100):
            g = sync_slot_grid(7, n_rb)
            mask = sync_pbch_core_mask(0, n_rb, n_f=0)                    # PSS and SSS rows of the exclusion
            self.assertTrue(np.array_equal(g != 0, mask), n_rb)

    def test_bad_bandwidth_is_refused(self):
        with self.assertRaises(ValueError):
            sync_slot_grid(1, 20)


class Waveform(unittest.TestCase):
    def test_only_slot_0_carries_signal(self):
        w = cas_subframe(5, 25)
        self.assertEqual(len(w), 7680)                                    # one subframe at 7.68 MHz
        self.assertGreater(np.sum(np.abs(w[:3840]) ** 2), 0)
        np.testing.assert_allclose(w[3840:], 0.0)

    def test_extended_cp_energy_is_in_symbols_4_and_5(self):
        # extended CP: 6 symbols per slot of 512 + 128 samples; the SSS is in symbol N_symb - 2 = 4 and the PSS in symbol 5
        w = cas_subframe(5, 25, normal_cp=False)
        self.assertEqual(len(w), 7680)
        for l in range(6):
            useful = w[l * 640 + 128: l * 640 + 640]
            self.assertAlmostEqual(float(np.sum(np.abs(useful) ** 2)), 62.0 if l in (4, 5) else 0.0, places=9, msg=f"symbol {l}")
        np.testing.assert_allclose(w[3840:], 0.0)

    def test_energy_is_in_the_pss_and_sss_symbols_only(self):
        # 25 PRB: 512-point FFT, cyclic prefix 40 samples for the first symbol of a slot and 36 for the others (160 and 144 at 2048 points).
        # With 62 unit resource elements per symbol the energy of a symbol's useful part is 62 (Parseval with the sqrt(N) scaling).
        w = cas_subframe(5, 25)
        pos = 0
        for l in range(7):
            pre = 40 if l == 0 else 36
            useful = w[pos + pre: pos + pre + 512]
            pos += pre + 512
            expected = 62.0 if l in (5, 6) else 0.0
            self.assertAlmostEqual(float(np.sum(np.abs(useful) ** 2)), expected, places=9, msg=f"symbol {l}")
        self.assertEqual(pos, 3840)

if __name__ == "__main__":
    unittest.main()
