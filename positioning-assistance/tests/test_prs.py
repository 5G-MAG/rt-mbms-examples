import math
import unittest
from unittest import mock

import numpy as np

from bps import lab
from bps.prs import (prs_active, PrsCell, correlation_lag, gold_sequence, measure_rstd, occasion_subframe, ofdm_subframe, prs_c_init,
                     prs_sequence, prs_slot_grid, prs_subframe, sample_rate_hz, sync_pbch_core_mask)
from bps.sim import quantise_rstd_ts, rstd_step_ts
from bps.timebase import is_prs_occasion_start
from bps.assistance import AssistanceDocument, Cell, Prs
from bps.solver import solve_rstd
from bps.timebase import C_MPS, TS_S

# First 100 bits of c(n) from srsRAN's srsran_sequence_LTE_pr (lib/src/phy/common/sequence.c in rt-mbms-tx, built and run on its own),
# an implementation independent of bps.prs, for the seeds below. The last three seeds are PRS c_init values:
# N_ID 0 / slot 0 / l 3, N_ID 4095 / slot 19 / l 6, and N_ID 300 / slot 7 / l 5 with extended cyclic prefix.
ORACLE = {
    0x12345: "1101011001010111011110100111101100111010100001111011001101001001010110011010101100010010100000000101",
    0x2C01: "0111011110011010000110100100101111101100100101011111000101100000011111000111000010101101101011011111",
    0x792DB7FF: "1101110101101001100011001100110111101010000110010101101000100100001101111111010010111000010100101010",
    0x2463A58: "0110001110111001101110110000010001110111011110110010100001000100010001011000110100100000000101110011",
}


class Sequence(unittest.TestCase):
    def test_gold_matches_an_independent_implementation(self):
        for seed, bits in ORACLE.items():
            self.assertEqual("".join(map(str, gold_sequence(seed, 100))), bits, hex(seed))

    def test_c_init_values(self):
        # worked by hand from the clause 6.10.4.1 expression
        self.assertEqual(prs_c_init(0, 0, 3, True), 2**10 * (7 * 1 + 3 + 1) * 1 + 0 + 1)
        self.assertEqual(prs_c_init(4095, 19, 6, True), 2**28 * 7 + 2**10 * (7 * 20 + 6 + 1) * (2 * 511 + 1) + 2 * 511 + 1)
        self.assertEqual(prs_c_init(300, 7, 5, False), 2**10 * (7 * 8 + 5 + 1) * (2 * 300 + 1) + 2 * 300 + 0)
        self.assertEqual(prs_c_init(300, 7, 5, False), 0x2463A58)  # the oracle seed above

    def test_c_init_range(self):
        for bad in (-1, 4096):
            with self.assertRaises(ValueError):
                prs_c_init(bad, 0, 0)

    def test_sequence_length_and_unit_modulus(self):
        r = prs_sequence(17, 4, 3)
        self.assertEqual(len(r), 2 * 110)  # m = 0 .. 2 N_RB^max,DL - 1
        np.testing.assert_allclose(np.abs(r), 1.0)
        np.testing.assert_allclose(np.abs(r.real), 1 / math.sqrt(2))

    def test_other_cells_are_nearly_orthogonal(self):
        a, b = prs_sequence(10, 4, 3), prs_sequence(11, 4, 3)
        self.assertLess(abs(np.vdot(a, b)) / len(a), 0.25)


class Mapping(unittest.TestCase):
    def test_symbols_and_spacing(self):
        g = prs_slot_grid(0, 2, 25)  # even slot without PSS, SSS or PBCH, N_ID 0 so v_shift 0
        used = {l: np.flatnonzero(g[:, l]) for l in range(7)}
        self.assertEqual([l for l in range(7) if len(used[l])], [3, 5, 6])
        for l, k in used.items():
            if len(k):
                self.assertEqual(len(k), 2 * 25)
                self.assertTrue(np.all(np.diff(k) == 6))
        # k mod 6 = (6 - l + v_shift) mod 6: 3, 1, 0
        self.assertEqual([used[l][0] % 6 for l in (3, 5, 6)], [3, 1, 0])

    def test_odd_slot_symbols(self):
        g = prs_slot_grid(0, 3, 25)
        self.assertEqual([l for l in range(7) if np.any(g[:, l])], [1, 2, 3, 5, 6])
        g4 = prs_slot_grid(0, 3, 25, four_pbch_ports=True)
        self.assertEqual([l for l in range(7) if np.any(g4[:, l])], [2, 3, 5, 6])

    def test_v_shift_follows_the_identity(self):
        a = prs_slot_grid(0, 0, 25)
        b = prs_slot_grid(7, 0, 25)  # 7 mod 6 = 1
        self.assertEqual((np.flatnonzero(b[:, 3])[0] - np.flatnonzero(a[:, 3])[0]) % 6, 1)

    def test_narrower_prs_bandwidth_is_centred_in_the_band(self):
        g = prs_slot_grid(5, 0, 25, n_rb_prs=6)
        k = np.flatnonzero(g[:, 3])
        self.assertEqual(len(k), 12)
        # k = 6 (m + N_DL - N_PRS) + shift, shift in 0..5: first element 114..119, 12 elements 6 apart, so the 6 PRB are centred in 25
        self.assertTrue(114 <= k[0] <= 119)
        self.assertTrue(np.all(np.diff(k) == 6))
        self.assertLessEqual(abs(k[0] - (300 - 1 - k[-1])), 6)
        r = prs_sequence(5, 0, 3)
        np.testing.assert_allclose(g[k, 3], r[np.arange(12) + 110 - 6])  # m' = m + N_max - N_PRS

    def test_extended_cp_symbols(self):
        g = prs_slot_grid(0, 2, 25, normal_cp=False)
        self.assertEqual([l for l in range(6) if np.any(g[:, l])], [4, 5])
        g = prs_slot_grid(0, 3, 25, normal_cp=False)
        self.assertEqual([l for l in range(6) if np.any(g[:, l])], [1, 2, 4, 5])

    def test_bad_bandwidths(self):
        for args in ((0, 0, 25, 26), (0, 0, 111, 10), (0, 0, 25, 0)):
            with self.assertRaises(ValueError):
                prs_slot_grid(*args)


class Exclusion(unittest.TestCase):
    """Clause 6.10.4: PRS is not mapped onto the PSS, SSS or PBCH core resource elements. The pattern is that of an MBMS-dedicated cell
    (5G Broadcast): PSS and SSS in slot 0 and the PBCH core set in slot 1, in the radio frames with n_f mod 4 = 0 only; nothing in slot 10."""

    def test_mask_shapes(self):
        n = 25
        m = sync_pbch_core_mask(0, n, n_f=0)
        self.assertEqual(int(m.sum()), 2 * 62)
        self.assertEqual(sorted(set(np.nonzero(m)[1])), [5, 6])           # SSS before PSS, the last two of 7 symbols
        self.assertEqual((np.flatnonzero(m[:, 6])[0], np.flatnonzero(m[:, 6])[-1]), (119, 180))   # n - 31 + 150 for n = 0 and 61
        self.assertTrue(np.array_equal(m[:, 5], m[:, 6]))
        m = sync_pbch_core_mask(1, n, n_f=0)
        self.assertEqual(int(m.sum()), 72 * 4)
        self.assertEqual(sorted(set(np.nonzero(m)[1])), [0, 1, 2, 3])
        self.assertEqual((np.flatnonzero(m[:, 0])[0], np.flatnonzero(m[:, 0])[-1]), (114, 185))   # 150 - 36 + k' for k' = 0 and 71

    def test_nothing_in_other_slots_and_nothing_in_subframe_5(self):
        for n_s in (2, 3, 9, 10, 11, 19):      # slots 10 and 11 are subframe 5: no PSS, SSS or PBCH on an MBMS-dedicated cell
            for n_f in (0, 4, 8):
                self.assertEqual(int(sync_pbch_core_mask(n_s, 25, n_f=n_f).sum()), 0, (n_s, n_f))

    def test_only_frames_with_n_f_mod_4_equal_0(self):
        for n_f in (1, 2, 3, 5, 6, 7, 9, 1023):
            for n_s in (0, 1):
                self.assertEqual(int(sync_pbch_core_mask(n_s, 25, n_f=n_f).sum()), 0, (n_s, n_f))
        for n_f in (4, 8, 12, 16, 1020):
            self.assertEqual(int(sync_pbch_core_mask(0, 25, n_f=n_f).sum()), 124, n_f)
            self.assertEqual(int(sync_pbch_core_mask(1, 25, n_f=n_f).sum()), 288, n_f)

    def test_other_fdd_cells_have_sync_in_slots_0_and_10_and_pbch_in_every_frame(self):
        for n_f in (0, 1, 2, 3, 4, 1023):
            for n_s, expect in ((0, 124), (10, 124), (1, 288), (11, 0), (2, 0), (19, 0)):
                self.assertEqual(int(sync_pbch_core_mask(n_s, 25, n_f=n_f, mbms_dedicated=False).sum()), expect, (n_f, n_s))
        m = sync_pbch_core_mask(10, 25, mbms_dedicated=False)
        self.assertEqual(sorted(set(np.nonzero(m)[1])), [5, 6])
        self.assertTrue(np.array_equal(m, sync_pbch_core_mask(0, 25, mbms_dedicated=False)))

    def test_the_grid_follows_the_cell_type(self):
        for n_s in (0, 1, 10):
            ded = prs_slot_grid(3, n_s, 25, n_f=1, mbms_dedicated=True)
            oth = prs_slot_grid(3, n_s, 25, n_f=1, mbms_dedicated=False)
            if n_s == 10:
                self.assertTrue(np.array_equal(ded != 0, ded != 0) and np.count_nonzero(oth) < np.count_nonzero(ded))
            else:
                self.assertLess(np.count_nonzero(oth), np.count_nonzero(ded))     # frame 1 has no sync signals on a dedicated cell

    def test_mask_extended_cp(self):
        m = sync_pbch_core_mask(0, 25, normal_cp=False)
        self.assertEqual(m.shape, (300, 6))
        self.assertEqual(sorted(set(np.nonzero(m)[1])), [4, 5])

    def test_prs_re_counts_in_a_slot_with_pss_and_sss(self):
        # N_ID 0 so v_shift 0: k = 6 m + 3 (l = 3), 6 m + 1 (l = 5), 6 m (l = 6), m = 0..49. PSS and SSS cover k = 119..180.
        g = prs_slot_grid(0, 0, 25, n_f=0)
        counts = {l: int(np.count_nonzero(g[:, l])) for l in range(7)}
        self.assertEqual(counts, {0: 0, 1: 0, 2: 0, 3: 50, 4: 0, 5: 50 - 10, 6: 50 - 11})   # l=5: m=20..29, l=6: m=20..30 removed
        g = prs_slot_grid(0, 0, 25, n_f=1)     # a frame without PSS and SSS: all PRS elements present
        self.assertEqual({l: int(np.count_nonzero(g[:, l])) for l in (3, 5, 6)}, {3: 50, 5: 50, 6: 50})

    def test_prs_re_counts_in_a_slot_with_pbch(self):
        # slot 1: PRS on l = 1, 2, 3, 5, 6; PBCH core covers l = 0..3, k = 114..185: m with 6m+5, 6m+4, 6m+3 inside are removed
        g = prs_slot_grid(0, 1, 25, n_f=0)
        counts = {l: int(np.count_nonzero(g[:, l])) for l in range(7)}
        self.assertEqual(counts, {0: 0, 1: 50 - 12, 2: 50 - 12, 3: 50 - 12, 4: 0, 5: 50, 6: 50})
        g = prs_slot_grid(0, 1, 25, n_f=2)
        self.assertEqual({l: int(np.count_nonzero(g[:, l])) for l in (1, 2, 3, 5, 6)}, {1: 50, 2: 50, 3: 50, 5: 50, 6: 50})

    def test_slot_10_keeps_all_its_prs(self):
        g = prs_slot_grid(0, 10, 25, n_f=0)
        self.assertEqual({l: int(np.count_nonzero(g[:, l])) for l in (3, 5, 6)}, {3: 50, 5: 50, 6: 50})

    def test_no_prs_on_a_masked_element_and_the_rest_is_the_sequence(self):
        n_rb, nid = 25, 11
        for n_s in (0, 1):
            g = prs_slot_grid(nid, n_s, n_rb, n_f=4)
            self.assertFalse(np.any(g[sync_pbch_core_mask(n_s, n_rb, n_f=4)]))
            for k, l in zip(*np.nonzero(g)):
                m = (k - ((6 - l + nid % 6) % 6)) // 6          # invert k = 6 m + ((6 - l + v_shift) mod 6)
                self.assertEqual(g[k, l], prs_sequence(nid, n_s, l)[m + 110 - n_rb], (n_s, k, l))

    def test_waveform_follows_the_frame(self):
        with_sync = prs_subframe(5, 0, 25, n_f=0)
        without = prs_subframe(5, 0, 25, n_f=1)
        self.assertFalse(np.allclose(with_sync, without))
        np.testing.assert_allclose(prs_subframe(5, 0, 25, n_f=4), with_sync)          # same pattern every 4 frames
        np.testing.assert_allclose(prs_subframe(5, 0, 25, n_f=2), without)
        self.assertLess(np.sum(np.abs(with_sync) ** 2), np.sum(np.abs(without) ** 2))  # PRS elements were taken out of it

    def test_measure_rstd_passes_the_frame_to_the_waveform(self):
        seen = []
        real = prs_subframe

        def spy(*args, **kwargs):
            seen.append(kwargs.get("n_f"))
            return real(*args, **kwargs)

        with mock.patch("bps.prs.prs_subframe", spy):
            measure_rstd([PrsCell(1, 2400, 0.0), PrsCell(2, 2401, 1e-6)], 25, 30.0, np.random.default_rng(1), n_f=4)
        self.assertEqual(seen, [4, 4])

    def test_other_bandwidths_centre_the_masks(self):
        for n_rb in (6, 50, 100):
            m = sync_pbch_core_mask(0, n_rb)
            k = np.flatnonzero(m[:, 6])
            self.assertEqual((k[0], k[-1]), (6 * n_rb - 31, 6 * n_rb + 30))


class Waveform(unittest.TestCase):
    def test_ofdm_round_trip(self):
        n_rb = 25
        grids = [prs_slot_grid(3, 2, n_rb), prs_slot_grid(3, 3, n_rb)]
        wave = ofdm_subframe(grids, n_rb)
        n_fft = 512
        self.assertEqual(len(wave), 2 * (7 * n_fft + 160 * n_fft // 2048 + 6 * 144 * n_fft // 2048))
        self.assertEqual(len(wave), int(round(1e-3 * sample_rate_hz(n_rb))))  # one subframe is 1 ms
        pos = 0
        for grid in grids:
            for l in range(7):
                cp = 160 * n_fft // 2048 if l == 0 else 144 * n_fft // 2048
                sym = wave[pos + cp: pos + cp + n_fft]
                pos += cp + n_fft
                spec = np.fft.fft(sym) / math.sqrt(n_fft)
                k_rel = np.arange(12 * n_rb) - 6 * n_rb
                bins = np.where(k_rel < 0, n_fft + k_rel, k_rel + 1)
                np.testing.assert_allclose(spec[bins], grid[:, l], atol=1e-9)

    def test_extended_cp_round_trip_and_length(self):
        n_rb, n_fft, cp = 25, 512, 128                                       # extended CP: 512 samples at 2048 points, 128 at 512
        grids = [prs_slot_grid(3, 2, n_rb, normal_cp=False), prs_slot_grid(3, 3, n_rb, normal_cp=False)]
        wave = ofdm_subframe(grids, n_rb)
        self.assertEqual(len(wave), 2 * 6 * (n_fft + cp))
        self.assertEqual(len(wave), int(round(1e-3 * sample_rate_hz(n_rb))))   # still one subframe of 1 ms
        pos = 0
        for grid in grids:
            for l in range(6):
                sym = wave[pos + cp: pos + cp + n_fft]
                np.testing.assert_allclose(wave[pos: pos + cp], sym[-cp:])      # the prefix is the tail of the symbol
                pos += cp + n_fft
                spec = np.fft.fft(sym) / math.sqrt(n_fft)
                k_rel = np.arange(12 * n_rb) - 6 * n_rb
                bins = np.where(k_rel < 0, n_fft + k_rel, k_rel + 1)
                np.testing.assert_allclose(spec[bins], grid[:, l], atol=1e-9)

    def test_prs_subframe_takes_the_cp_type(self):
        self.assertEqual(len(prs_subframe(3, 1, 25, normal_cp=False)), 7680)
        self.assertFalse(np.allclose(prs_subframe(3, 1, 25, normal_cp=False), prs_subframe(3, 1, 25, normal_cp=True)))

    def test_cyclic_prefix_is_a_copy_of_the_symbol_tail(self):
        w = prs_subframe(3, 1, 25)
        cp0 = 160 * 512 // 2048
        np.testing.assert_allclose(w[:cp0], w[cp0 + 512 - cp0: cp0 + 512])


class Estimator(unittest.TestCase):
    def test_delay_is_recovered_to_a_nanosecond_at_high_snr(self):
        rng = np.random.default_rng(1)
        for delay in (0.0, 3.3e-6, -7.1e-6, 123.456e-6, -250e-6):
            cells = [PrsCell(1, 2401, 0.0), PrsCell(2, 2402, delay)]
            est = measure_rstd(cells, 25, 40.0, rng)[0]
            self.assertAlmostEqual(est, delay, delta=1e-9, msg=str(delay))

    def test_later_arrival_gives_positive_rstd(self):
        rng = np.random.default_rng(2)
        self.assertGreater(measure_rstd([PrsCell(1, 2401, 0.0), PrsCell(2, 2402, 5e-6)], 25, 20.0, rng)[0], 0)
        self.assertLess(measure_rstd([PrsCell(1, 2401, 0.0), PrsCell(2, 2402, -5e-6)], 25, 20.0, rng)[0], 0)

    def test_accuracy_over_many_draws(self):
        # 0 dB per sample, four cells in four subframes, 25 PRB. Measured rms is about 1.4 ns; the threshold leaves room for draws.
        rng = np.random.default_rng(3)
        errs = []
        for _ in range(8):
            d = [0.0, 3.3e-6 + rng.uniform(-1, 1) * 1e-6, -7.1e-6 + rng.uniform(-1, 1) * 1e-6, 11.7e-6]
            cells = [PrsCell(i + 1, 2401 + i, d[i]) for i in range(4)]
            est = measure_rstd(cells, 25, 0.0, rng)
            errs += [est[i] - (d[i + 1] - d[0]) for i in range(3)]
        self.assertLess(float(np.sqrt(np.mean(np.square(errs)))), 6e-9)

    def test_weak_cell_in_the_same_subframe_as_strong_ones(self):
        rng = np.random.default_rng(4)
        d = [0.0, 8e-6, -12e-6, 20e-6]
        cells = [PrsCell(i + 1, 2401, d[i], p) for i, p in enumerate((0, -10, -15, -20))]
        est = measure_rstd(cells, 25, 10.0, rng)
        for i in range(3):
            self.assertAlmostEqual(est[i], d[i + 1] - d[0], delta=30e-9, msg=f"cell {i + 2}")

    def test_occasion_subframe_from_the_table(self):
        # Delta_PRS = I_PRS - first index of its row (Table 6.10.4.3-1); the first occasion is in subframe Delta_PRS mod 10
        self.assertEqual([occasion_subframe(i) for i in (2400, 2401, 2402, 2403, 2404)], [0, 1, 2, 3, 4])
        self.assertEqual([occasion_subframe(i) for i in (2405, 2414, 2415, 2434, 0, 5, 159, 160)], [0, 9, 0, 9, 0, 5, 9, 0])

    def test_subframe_0_occasions_in_frames_with_and_without_sync_signals(self):
        rng = np.random.default_rng(6)
        for n_f in (0, 1, 4):       # 0 and 4 carry PSS, SSS and PBCH in subframe 0, 1 does not
            for index in (2400, 2405, 2415):   # PRS in subframe 0
                est = measure_rstd([PrsCell(1, 2401, 0.0), PrsCell(2, index, 5.5e-6)], 25, 30.0, rng, n_f=n_f)[0]
                self.assertAlmostEqual(est, 5.5e-6, delta=2e-9, msg=f"index {index} frame {n_f}")
        est = measure_rstd([PrsCell(1, 2400, 0.0), PrsCell(2, 2400, -4e-6)], 25, 30.0, rng, n_f=0)[0]   # both in subframe 0, same window
        self.assertAlmostEqual(est, -4e-6, delta=2e-9)

    def test_reserved_index_is_refused(self):
        with self.assertRaises(ValueError):
            occasion_subframe(2555)

    def test_inputs_that_cannot_be_modelled_are_refused(self):
        rng = np.random.default_rng(5)
        ok = [PrsCell(1, 2401, 0.0), PrsCell(2, 2402, 1e-6)]
        with self.assertRaises(ValueError):
            measure_rstd(ok, 20, 0.0, rng)                                    # no FFT size for 20 PRB
        with self.assertRaises(ValueError):
            measure_rstd(ok[:1], 25, 0.0, rng)                                # no neighbour
        for delay in (-1.2e-3, 1.5e-3):                                       # beyond the window, would wrap
            with self.assertRaises(ValueError, msg=str(delay)):
                measure_rstd([ok[0], PrsCell(2, 2402, delay)], 25, 0.0, rng)
        # the edges that do fit are right
        est = measure_rstd([ok[0], PrsCell(2, 2402, 0.9e-3)], 25, 30.0, rng)[0]
        self.assertAlmostEqual(est, 0.9e-3, delta=2e-9)
        est = measure_rstd([ok[0], PrsCell(2, 2402, -0.9e-3)], 25, 30.0, rng)[0]
        self.assertAlmostEqual(est, -0.9e-3, delta=2e-9)

    def test_correlation_lag_is_sub_sample(self):
        ref = prs_subframe(9, 1, 25)
        fs = sample_rate_hz(25)
        rx = np.zeros(len(ref) + 2000, dtype=complex)
        n = len(rx)
        padded = np.zeros(n, dtype=complex)
        padded[: len(ref)] = ref
        f = np.fft.fftfreq(n, d=1 / fs)
        shifted = np.fft.ifft(np.fft.fft(padded) * np.exp(-2j * np.pi * f * (500.37 / fs)))
        self.assertAlmostEqual(correlation_lag(shifted, ref), 500.37, delta=0.1)


class ReportingStep(unittest.TestCase):
    def test_step_and_quantiser_agree(self):
        self.assertEqual(rstd_step_ts(4096 * TS_S), 1)
        self.assertEqual(rstd_step_ts(-4096 * TS_S), 1)
        self.assertEqual(rstd_step_ts(4096.5 * TS_S), 5)
        self.assertEqual(rstd_step_ts(-9000 * TS_S), 5)
        rng = np.random.default_rng(8)
        for x in rng.uniform(-15000, 15000, 500) * TS_S:
            self.assertLessEqual(abs(quantise_rstd_ts(x) - x), rstd_step_ts(x) * TS_S / 2 + 1e-15)

    def test_lab_weights_follow_the_reporting_step(self):
        w = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
        m = w.measurement_from_prs(w.epoch_gps_ns + 40 * 10**9, 0, np.random.default_rng(1), snr_db=10.0)
        # at this time cell 2 is at about 4266 Ts (5 Ts step), cells 3 and 4 below 4096 Ts (1 Ts step)
        self.assertAlmostEqual(m.sigma_by_cell[2], math.hypot(5e-9, 5 * TS_S / math.sqrt(12)), places=12)
        for cid in (3, 4):
            self.assertAlmostEqual(m.sigma_by_cell[cid], math.hypot(5e-9, TS_S / math.sqrt(12)), places=12)
        self.assertEqual(m.sigma_s, m.sigma_by_cell[2])


class LabPrsConfiguration(unittest.TestCase):
    def test_every_occasion_is_a_cell_acquisition_subframe(self):
        # On the demo carrier (MBMS-dedicated, MBSFN subframes at 1.25 kHz) the only 15 kHz subframes are subframe 0 of the frames with
        # n_f mod 4 = 0; PRS is defined for 15 kHz only, so no occasion may fall anywhere else.
        net = lab.network()
        for site in (net.reference, *net.neighbours):
            self.assertEqual(site.prs.num_dl_frames, 1)      # one subframe per occasion: the next one is MBSFN at 1.25 kHz
            occasions = [(f, sf) for f in range(1024) for sf in range(10) if is_prs_occasion_start(f, sf, site.prs.configuration_index)]
            self.assertTrue(occasions)
            self.assertTrue(all(f % 4 == 0 and sf == 0 for f, sf in occasions), site.phys_cell_id)

    def test_the_check_would_notice_another_configuration(self):
        occasions = [(f, sf) for f in range(1024) for sf in range(10) if is_prs_occasion_start(f, sf, 2401)]   # T 5, Delta 1
        self.assertFalse(all(f % 4 == 0 and sf == 0 for f, sf in occasions))


class FromWaveformsToFix(unittest.TestCase):
    def test_lab_measurement_agrees_with_the_formula_within_the_reporting_step(self):
        w = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
        rng = np.random.default_rng(1)
        t = w.epoch_gps_ns + 40 * 10**9
        m = w.measurement_from_prs(t, 0, rng, snr_db=10.0)
        f = w.measurement(t, 0, noise_ns=0.0)
        for cid in (2, 3, 4):
            # 5 Ts above 4096 Ts, 1 Ts below: half a bin, plus a few ns of estimator error
            step = 5 * TS_S if abs(f.rstd_s[cid]) > 4096 * TS_S else TS_S
            self.assertLessEqual(abs(m.rstd_s[cid] - f.rstd_s[cid]), step / 2 + 10e-9, cid)

    def test_position_from_waveform_rstds_and_its_claimed_uncertainty(self):
        # 24 instants over 120 s: the 5 Ts reporting step of cell 2 makes its error a ramp as the RTD drifts, so the instants must span it
        w = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
        errs, claimed = [], []
        for k in range(24):
            t = w.epoch_gps_ns + (40 + 5 * k) * 10**9
            m = w.measurement_from_prs(t, 0, np.random.default_rng(k), snr_db=0.0)
            rstd = np.array([0.0] + [m.rstd_s[c] for c in (2, 3, 4)])
            rtd = np.array([0.0] + [((w.true_rtd_ns(c, t) + 500_000) % 1_000_000 - 500_000) * 1e-9 for c in (2, 3, 4)])
            sigma = np.array([1.0] + [m.sigma_by_cell[c] for c in (2, 3, 4)])
            sol = solve_rstd(lab.CELLS_ENU, rstd, rtd, sigma, ref=0, fix_up_m=lab.UE_ENU[2])
            errs.append(float(np.linalg.norm(sol.position_enu[:2] - lab.UE_ENU[:2])))
            claimed.append(sol.horizontal_sigma_m)
        self.assertLess(max(errs), 40.0, errs)
        rmse, sigma_rms = math.sqrt(np.mean(np.square(errs))), math.sqrt(np.mean(np.square(claimed)))
        # claimed and actual agree within a factor of 1.5 either way, and no fix is beyond 3 claimed sigma
        self.assertLess(rmse, 1.5 * sigma_rms, (rmse, sigma_rms))
        self.assertGreater(rmse, sigma_rms / 1.5, (rmse, sigma_rms))
        self.assertTrue(all(e < 3 * c for e, c in zip(errs, claimed)), list(zip(errs, claimed)))


class PrsActive(unittest.TestCase):
    def test_occasions_without_muting(self):
        # index 2435: T_PRS 40, Delta_PRS 0
        self.assertEqual([a for a in range(0, 130) if prs_active(2435, 1, a)], [0, 40, 80, 120])
        self.assertEqual([a for a in range(0, 90) if prs_active(2435, 2, a)], [0, 1, 40, 41, 80, 81])
        self.assertEqual([a for a in range(0, 90) if prs_active(2436, 1, a)], [1, 41, 81])        # Delta_PRS 1
        self.assertEqual([a for a in range(0, 12) if prs_active(2400, 1, a)], [0, 5, 10])         # T_PRS 5, Delta_PRS 0

    def test_the_subframe_number_wraps_at_10240(self):
        self.assertTrue(prs_active(2435, 1, 10240))
        self.assertTrue(prs_active(2435, 1, 10240 + 40))
        self.assertFalse(prs_active(2435, 1, 10240 + 1))
        self.assertTrue(prs_active(2435, 2, 10240 + 1))      # second subframe of the occasion at SFN 0

    def test_the_occasion_that_follows_the_wrap_still_counts_its_second_subframe(self):
        self.assertTrue(prs_active(2475, 2, 1))              # T_PRS 80, Delta 0, N_PRS 2: subframe 1 of the occasion at 0

    def test_muting_sequence(self):
        mute = "0110"                                         # T_REP 4: occasions 0 and 3 (mod 4) muted
        got = [a for a in range(0, 400) if prs_active(2435, 1, a, mute)]
        # occasion k = a / 40 is active when mute[k % 4] == '1', i.e. k % 4 is 1 or 2: k = 1, 2, 5, 6, 9 -> 40, 80, 200, 240, 360
        self.assertEqual(got, [40, 80, 200, 240, 360])

    def test_a_zero_bit_mutes_every_subframe_of_the_occasion(self):
        self.assertFalse(prs_active(2435, 2, 0, "01"))
        self.assertFalse(prs_active(2435, 2, 1, "01"))
        self.assertTrue(prs_active(2435, 2, 40, "01"))
        self.assertTrue(prs_active(2435, 2, 41, "01"))

    def test_long_muting_sequence_is_cut_to_the_first_n_bits(self):
        # index 1120: T_PRS 1280, Delta 0: 8 occasions per 10240 subframes. A 16-bit sequence times 1280 exceeds 10240, so only its first 8 bits count.
        seq = "1" + "0" * 7 + "1" * 8
        got = [a for a in range(0, 10240, 1280) if prs_active(1120, 1, a, seq)]
        self.assertEqual(got, [0])                            # the trailing 1s are never used
        self.assertTrue(prs_active(1120, 1, 10240, seq))      # and the sequence restarts at the wrap

    def test_muting_with_a_subframe_offset(self):
        # index 2437: T_PRS 40, Delta_PRS 2: occasions at 2, 42, 82, ...; "01" mutes the even ones (k = 0, 2, ...)
        self.assertEqual([a for a in range(0, 130) if prs_active(2437, 1, a, "01")], [42, 122])
        self.assertEqual([a for a in range(0, 130) if prs_active(2437, 2, a, "01")], [42, 43, 122, 123])

    def test_short_sequence_with_a_long_period_is_not_cut(self):
        self.assertEqual([a for a in range(0, 5120, 1280) if prs_active(1120, 1, a, "10")], [0, 2560])


class AssistanceSchema(unittest.TestCase):
    def doc(self, **prs):
        ref = Cell(1, 46.0, 6.0, 400.0, Prs(25, 2435, 1, **prs), cp_length="extended")
        return AssistanceDocument(1, 0, 10**9, ref, [])

    def test_defaults_and_effective_id(self):
        p = Prs(25, 2435, 1)
        self.assertIsNone(p.prs_id)
        self.assertEqual(p.effective_id(7), 7)                 # the cell identity unless configured
        self.assertEqual(Prs(25, 2435, 1, prs_id=300).effective_id(7), 300)

    def test_round_trip_with_the_new_fields(self):
        d = self.doc(prs_id=300, muting="0110")
        back = AssistanceDocument.from_json(d.to_json())
        self.assertEqual((back.reference.prs.prs_id, back.reference.prs.muting), (300, "0110"))

    def test_a_document_without_the_optional_fields_still_loads(self):
        import json
        raw = json.loads(self.doc(prs_id=300, muting="0110").to_json())
        for k in ("prsID-r14", "prs-MutingInfo-r9"):
            raw["reference"]["referenceCellInfo"]["prsInfo"].pop(k)
        back = AssistanceDocument.from_json(json.dumps(raw))
        self.assertIsNone(back.reference.prs.prs_id)
        self.assertIsNone(back.reference.prs.muting)

    def test_invalid_values_are_refused(self):
        for prs in ({"prs_id": 4096}, {"prs_id": -1}, {"muting": "011"}, {"muting": "0x"}, {"muting": ""}, {"muting": "01" * 600}):
            with self.assertRaises(ValueError, msg=str(prs)):
                self.doc(**prs).validate()
        self.doc(prs_id=4095, muting="01" * 512).validate()     # the largest allowed


if __name__ == "__main__":
    unittest.main()
