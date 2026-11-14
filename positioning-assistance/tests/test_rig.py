import math
import unittest

import numpy as np

from bps.prs import prs_subframe
from bps.rig import BLOCK, CAS_PERIOD, FS, EmulatedCell, Mixer, correlation_lag, frac_delay, upsample2
from bps.sync import cas_subframe


def real_stream(n_periods: int, p_true: float, seed: int = 1, scale: float = 0.05, noise: float = 1e-4, cell_id: int = 1, only_first: bool = False,
                normal_cp: bool = False) -> np.ndarray:
    """What the transmitter's stream looks like to the mixer: a cell acquisition subframe (PSS, SSS and the cell's own PRS) every 40 ms starting at p_true samples."""
    n = n_periods * CAS_PERIOD
    x = np.zeros(n + 16384, dtype=complex)
    sync = upsample2(cas_subframe(cell_id, 25, normal_cp=normal_cp))
    prs = upsample2(prs_subframe(cell_id, 0, 25, n_f=0, normal_cp=normal_cp))
    for m in range(1 if only_first else n_periods):
        a = p_true + m * CAS_PERIOD
        i0 = int(math.floor(a))
        x[i0: i0 + 16384] += frac_delay((sync + prs) * scale, a - i0)
    rng = np.random.default_rng(seed)
    x = x[:n] + noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return x.astype(np.complex64)


def run(mixer: Mixer, x: np.ndarray) -> np.ndarray:
    out = []
    for i in range(len(x) // BLOCK):
        out.append(mixer.process(x[i * BLOCK:(i + 1) * BLOCK]))
    return np.concatenate(out)


class Helpers(unittest.TestCase):
    def test_upsample_keeps_the_even_samples_and_the_energy(self):
        w = prs_subframe(3, 0, 25, n_f=0, normal_cp=False)
        u = upsample2(w)
        self.assertEqual(len(u), 15360)
        np.testing.assert_allclose(u[::2], w, atol=1e-9)                     # ideal interpolation: the original samples stay
        self.assertAlmostEqual(float(np.sum(np.abs(u) ** 2)) / float(np.sum(np.abs(w) ** 2)), 2.0, places=9)

    def test_frac_delay_and_lag_search(self):
        ref = upsample2(cas_subframe(1, 25, normal_cp=False))
        for delay in (0.0, 0.37, 0.5, 0.93):
            x = np.zeros(40000, dtype=complex)
            x[5000: 5000 + 16384] = frac_delay(ref, delay)
            lag, _ = correlation_lag(x, ref)
            self.assertAlmostEqual(lag, 5000 + delay, delta=0.02, msg=str(delay))

    def test_lag_search_with_noise(self):
        ref = upsample2(cas_subframe(1, 25, normal_cp=False))
        rng = np.random.default_rng(3)
        x = np.zeros(40000, dtype=complex)
        x[7000: 7000 + 16384] = frac_delay(ref * 0.05, 0.61)
        x += 2e-3 * (rng.standard_normal(40000) + 1j * rng.standard_normal(40000))
        lag, _ = correlation_lag(x, ref)
        self.assertAlmostEqual(lag, 7000.61, delta=0.05)


class LabDelays(unittest.TestCase):
    def test_emulated_delay_is_the_rstd_the_lab_ue_would_measure(self):
        from bps import lab
        w = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
        for dt in (0, 40, 300):
            t = w.epoch_gps_ns + dt * 10**9
            m = w.measurement(t, 0, noise_ns=0.0)
            self.assertEqual(w.emulated_delay_s(1, t), 0.0)
            for cid in (2, 3, 4):
                self.assertAlmostEqual(w.emulated_delay_s(cid, t), m.rstd_s[cid], delta=1e-12, msg=f"cell {cid} at +{dt} s")

    def test_the_delay_drifts_with_the_rtd(self):
        from bps import lab
        w = lab.World(epoch_gps_ns=1_500_000_000 * 10**9, seed=2)
        d0 = w.emulated_delay_s(3, w.epoch_gps_ns)
        d100 = w.emulated_delay_s(3, w.epoch_gps_ns + 100 * 10**9)
        self.assertAlmostEqual((d100 - d0) * 1e9, -2.0 * 100, delta=1e-6)     # cell 3 drifts -2 ns per second (lab.TRUE_RTD)


class MixerTests(unittest.TestCase):
    CELLS = [EmulatedCell(2), EmulatedCell(3), EmulatedCell(4)]
    # propagation plus RTD, against the real cell, in seconds: within +-0.5 ms
    DELAYS = {2: 138.8e-6, 3: -50.4e-6, 4: 51.1e-6}
    NORMAL_CP = False

    def stream(self, *a, **kw):
        return real_stream(*a, normal_cp=self.NORMAL_CP, **kw)

    def mixer(self, power_db=-12.0, delay_fn=None, **kw):
        return Mixer(self.CELLS, delay_fn or (lambda cid, t: self.DELAYS[cid]), lambda: 10**18, power_db=power_db, normal_cp=self.NORMAL_CP, **kw)

    def test_nothing_changes_before_the_lock(self):
        x = self.stream(1, 85.3)
        m = self.mixer()
        out = run(m, x[: 10 * BLOCK])
        np.testing.assert_array_equal(out, x[: 10 * BLOCK])
        self.assertIsNone(m.p0)

    def test_locks_onto_the_real_cell_acquisition_subframe(self):
        x = self.stream(4, 85.3)
        m = self.mixer()
        run(m, x)
        self.assertIsNotNone(m.p0)
        # the lock is on one of the cell acquisition subframes (any of them, they repeat every 40 ms)
        self.assertAlmostEqual(((m.p0 - 85.3 + CAS_PERIOD / 2) % CAS_PERIOD) - CAS_PERIOD / 2, 0.0, delta=0.02)
        self.assertGreater(m.lock_quality, 8.0)
        self.assertAlmostEqual(m.scale, 0.05, delta=0.001)                    # the scale of the stream, from the PSS and SSS

    def test_a_strong_match_that_does_not_repeat_is_not_a_lock(self):
        # one clear match, nothing 40 ms before or after it: not the cell acquisition subframes of a cell
        m = self.mixer()
        run(m, self.stream(4, 85.3, only_first=True))
        self.assertIsNone(m.p0)
        self.assertGreater(m.best_lock_ratio, 8.0)            # it was strong: only the periodicity refused it

    def test_a_weak_match_that_repeats_is_not_a_lock(self):
        m = self.mixer()
        run(m, self.stream(4, 85.3, noise=0.25))
        self.assertIsNone(m.p0)
        self.assertLess(m.lock_quality, 8.0)                  # it repeated: only the strength refused it

    def test_only_the_occasions_after_the_lock_are_injected(self):
        # 6 periods: the lock comes after 82 blocks, when the occasions at 85, 614485 and 1228885 are already past. Four are injected, three cells each: the
        # three that fall in the rest of the stream and the one just after its end, which is scheduled a few blocks ahead.
        m = self.mixer()
        run(m, self.stream(6, 85.3))
        self.assertEqual(m.injected, 3 * 4)

    def test_no_lock_without_a_cell(self):
        rng = np.random.default_rng(5)
        x = (1e-3 * (rng.standard_normal(5 * CAS_PERIOD) + 1j * rng.standard_normal(5 * CAS_PERIOD))).astype(np.complex64)
        m = self.mixer()
        out = run(m, x)
        self.assertIsNone(m.p0)
        np.testing.assert_array_equal(out, x[: len(out)])

    def test_the_emulated_cells_arrive_at_their_delays(self):
        p_true = 85.3
        x = self.stream(6, p_true)
        m = self.mixer()
        out = run(m, x)
        refs = {c: upsample2(prs_subframe(c, 0, 25, n_f=0, normal_cp=self.NORMAL_CP)) for c in (1, 2, 3, 4)}
        # an occasion after the lock and its injections are all done: measure every cell in a window around it, independently of the mixer
        occasion = 4
        a = p_true + occasion * CAS_PERIOD
        lo = int(a) - 12000
        win = out[lo: lo + 40000].astype(complex)
        toa = {}
        for c in (1, 2, 3, 4):
            lag, _ = correlation_lag(win, refs[c])
            toa[c] = (lag + lo - a) / FS
        # the real cell sits at 0 (its own PRS, with the others leaking into the estimate); the others at their delays: differences within 5 ns
        for c in (2, 3, 4):
            self.assertAlmostEqual((toa[c] - toa[1]) * 1e9, self.DELAYS[c] * 1e9, delta=5.0, msg=f"cell {c}")
        self.assertLess(abs(toa[1]) * 1e9, 5.0)

    def test_the_emulated_cells_can_send_their_pss_and_sss_instead_of_prs(self):
        p_true = 85.3
        x = self.stream(6, p_true)
        m = Mixer([EmulatedCell(2)], lambda cid, t: self.DELAYS[2], lambda: 10**18, power_db=-6.0, normal_cp=self.NORMAL_CP, signal="pss_sss")
        out = run(m, x)
        occasion = 4
        a = p_true + occasion * CAS_PERIOD
        lo = int(a) - 12000
        added = (out[lo: lo + 40000] - x[lo: lo + 40000]).astype(complex)       # what the mixer put in: only the emulated cell's signal
        sync_ref = upsample2(cas_subframe(2, 25, normal_cp=self.NORMAL_CP))
        prs_ref = upsample2(prs_subframe(2, 0, 25, n_f=0, normal_cp=self.NORMAL_CP))
        lag, peak_sync = correlation_lag(added, sync_ref)
        _, peak_prs = correlation_lag(added, prs_ref)
        self.assertAlmostEqual((lag + lo - a) / FS * 1e9, self.DELAYS[2] * 1e9, delta=3.0)
        self.assertGreater(peak_sync, 5 * peak_prs)                              # it is the PSS and SSS that were added, not the PRS
        m_prs = Mixer([EmulatedCell(2)], lambda cid, t: self.DELAYS[2], lambda: 10**18, power_db=-6.0, normal_cp=self.NORMAL_CP)
        added_prs = (run(m_prs, x)[lo: lo + 40000] - x[lo: lo + 40000]).astype(complex)
        self.assertGreater(correlation_lag(added_prs, prs_ref)[1], 5 * correlation_lag(added_prs, sync_ref)[1])   # and by default it is the PRS
        with self.assertRaises(ValueError):
            Mixer([EmulatedCell(2)], lambda c, t: 0.0, lambda: 0, signal="crs")

    def test_no_cell_is_added_before_the_stated_time(self):
        x = self.stream(8, 85.3)
        late = Mixer(self.CELLS, lambda cid, t: self.DELAYS[cid], lambda: 10**18, power_db=-6.0, normal_cp=self.NORMAL_CP, inject_after_s=0.2)
        out = run(late, x)
        diff = np.abs(out.astype(complex) - x.astype(complex))
        first_added = int(np.argmax(diff > 1e-6))
        self.assertGreaterEqual(first_added, int(0.2 * FS))                      # nothing before 200 ms of the stream (the lock alone takes about 120 ms)
        self.assertGreater(float(diff[int(0.2 * FS):].max()), 1e-3)              # and the cells are there afterwards
        early = Mixer(self.CELLS, lambda cid, t: self.DELAYS[cid], lambda: 10**18, power_db=-6.0, normal_cp=self.NORMAL_CP)
        self.assertLess(int(np.argmax(np.abs(run(early, x).astype(complex) - x.astype(complex)) > 1e-6)), int(0.2 * FS))   # by default they start at the first occasion after the lock

    def test_the_real_cell_is_untouched_outside_the_injections(self):
        x = self.stream(6, 85.3)
        m = self.mixer()
        out = run(m, x)
        # far from every cell acquisition subframe the output equals the input
        far = slice(3 * BLOCK, 3 * BLOCK + 5 * BLOCK)
        np.testing.assert_array_equal(out[far], x[far][: len(out[far])])
        self.assertGreater(m.injected, 0)

    def test_power_of_the_emulated_cells(self):
        x = self.stream(6, 85.3)
        m = self.mixer(power_db=-12.0)
        out = run(m, x)
        diff = (out.astype(complex) - x[: len(out)].astype(complex))
        a = 85.3 + 4 * CAS_PERIOD
        i0 = int(a) - 12000
        seg = diff[i0: i0 + 40000]               # holds every injected subframe of this occasion
        w = upsample2(prs_subframe(3, 0, 25, n_f=0, normal_cp=self.NORMAL_CP))
        # amplitude of cell 3's PRS in the added signal: projection on its own waveform at its delay (sub-sample aligned)
        lag, _ = correlation_lag(seg, w)
        i1 = int(math.floor(lag))
        aligned = frac_delay(w, lag - i1, len(w) + 64)[: len(w)]
        proj = abs(np.vdot(aligned, seg[i1: i1 + len(w)])) / np.vdot(aligned, aligned).real
        self.assertAlmostEqual(proj / m.scale, 10 ** (-12.0 / 20.0), delta=0.01)

    def test_a_delay_that_drifts_is_followed(self):
        calls = []

        def delay(cid, t):
            calls.append(t)
            return self.DELAYS[cid] + 1e-9 * len(calls)       # moves by 1 ns per scheduled injection

        x = self.stream(7, 85.3)
        m = Mixer(self.CELLS, delay, lambda: 10**18, power_db=-12.0, normal_cp=self.NORMAL_CP)
        run(m, x)
        self.assertGreater(len(set(calls)), 0)
        self.assertGreaterEqual(len(calls), 6)

    def test_only_the_25_prb_carrier_is_supported(self):
        with self.assertRaises(ValueError):
            Mixer(self.CELLS, lambda c, t: 0.0, lambda: 0, n_rb=50)


class NoiseTests(unittest.TestCase):
    def test_the_noise_gives_the_stated_snr_per_resource_element_at_the_modem(self):
        # a stream with nothing in it but the real cell; its noise-free copy is the difference
        x = real_stream(5, 85.3, scale=0.05, noise=0.0)
        for snr in (0.0, 10.0, 20.0):
            m = Mixer([], lambda c, t: 0.0, lambda: 10**18, snr_db=snr, seed=4)
            out = run(m, x)
            noise = out.astype(complex) - x[: len(out)].astype(complex)
            noise = noise[m._lock_len + 3 * BLOCK:]            # after the lock, which adds none
            # the modem's step down to 7.68 Msps: a low-pass that keeps the lower half of the band, then every second sample (the variance of the kept samples is that of the
            # filtered noise)
            n = len(noise)
            spec = np.fft.fft(noise)
            spec[n // 4: n - n // 4] = 0.0
            var_down = float(np.mean(np.abs(np.fft.ifft(spec)) ** 2))
            es = m.scale ** 2
            self.assertAlmostEqual(10 * math.log10(es / var_down), snr, delta=0.4, msg=f"{snr} dB")

    def test_no_noise_before_the_lock_or_when_not_asked(self):
        x = real_stream(3, 85.3)
        quiet = run(Mixer([], lambda c, t: 0.0, lambda: 10**18), x)
        np.testing.assert_array_equal(quiet, x[: len(quiet)])
        m = Mixer([], lambda c, t: 0.0, lambda: 10**18, snr_db=5.0)
        out = run(m, x)
        np.testing.assert_array_equal(out[: m._lock_len], x[: m._lock_len])


class PerCellCpMixerTests(unittest.TestCase):
    """An emulated cell may have a cyclic prefix of its own: its PRS is built with it, the real cell's with the real cell's."""

    def test_a_cell_with_its_own_prefix_is_built_with_it(self):
        cells = [EmulatedCell(2, normal_cp=True), EmulatedCell(3), EmulatedCell(4, normal_cp=False)]
        m = Mixer(cells, lambda cid, t: 0.0, lambda: 10**18, normal_cp=False)
        want = {2: True, 3: False, 4: False}
        for cid, normal in want.items():
            np.testing.assert_allclose(m._waves[cid], upsample2(prs_subframe(cid, 0, 25, n_f=0, normal_cp=normal)))
        self.assertFalse(np.allclose(m._waves[2], upsample2(prs_subframe(2, 0, 25, n_f=0, normal_cp=False))))
        m2 = Mixer([EmulatedCell(3, normal_cp=False)], lambda cid, t: 0.0, lambda: 10**18, normal_cp=True)
        np.testing.assert_allclose(m2._waves[3], upsample2(prs_subframe(3, 0, 25, n_f=0, normal_cp=False)))
        m3 = Mixer([EmulatedCell(3)], lambda cid, t: 0.0, lambda: 10**18, normal_cp=True)
        np.testing.assert_allclose(m3._waves[3], upsample2(prs_subframe(3, 0, 25, n_f=0, normal_cp=True)))


class CfoMixerTests(unittest.TestCase):
    """An emulated cell with its own oscillator: its subframe is rotated by the offset, the others are not."""

    def injected(self, cells):
        m = Mixer(cells, lambda cid, t: 0.0, lambda: 10**18, power_db=0.0)
        x = real_stream(4, 85.3, scale=0.05, noise=0.0)
        out = run(m, x)
        return m, out.astype(complex) - x[: len(out)].astype(complex)

    def test_the_offset_rotates_the_injected_subframe(self):
        _, base = self.injected([EmulatedCell(2)])
        _, rot = self.injected([EmulatedCell(2, cfo_hz=1000.0)])
        n = np.arange(len(base))
        sel = np.abs(base) > 0.2 * np.abs(base).max()
        ratio = rot[sel] * np.conj(base[sel])
        # the rotation is the offset from the start of the subframe: the phase slope of rot / base over the subframe is 2 pi f / FS per sample
        idx = np.nonzero(sel)[0]
        first = idx[0]
        slope = np.polyfit(idx - first, np.unwrap(np.angle(ratio)), 1)[0]
        self.assertAlmostEqual(slope, 2 * np.pi * 1000.0 / FS, delta=1e-5)
        self.assertAlmostEqual(float(np.sum(np.abs(rot) ** 2)), float(np.sum(np.abs(base) ** 2)), delta=1e-6 * float(np.sum(np.abs(base) ** 2)))   # the energy is the same

    def test_other_cells_are_not_rotated(self):
        _, a = self.injected([EmulatedCell(2), EmulatedCell(3)])
        _, b = self.injected([EmulatedCell(2), EmulatedCell(3, cfo_hz=500.0)])
        _, only2 = self.injected([EmulatedCell(2)])
        _, only3 = self.injected([EmulatedCell(3)])
        _, only3_rot = self.injected([EmulatedCell(3, cfo_hz=500.0)])
        np.testing.assert_allclose(a, only2 + only3, atol=1e-6)               # the cells add: the sum is the parts (float32 stream)
        np.testing.assert_allclose(b, only2 + only3_rot, atol=1e-6)           # and only cell 3 is rotated
        self.assertGreater(float(np.max(np.abs(b - a))), 0.01)

    def test_the_command_line_form(self):
        from bps.apps import _cell_cfo
        self.assertEqual(_cell_cfo(["3=150", "4=-80.5"]), {3: 150.0, 4: -80.5})
        self.assertEqual(_cell_cfo(None), {})
        for bad in ("3", "x=1", "3=fast"):
            with self.assertRaises(SystemExit):
                _cell_cfo([bad])


class NormalCpMixerTests(MixerTests):
    """The same, for a real cell with the normal cyclic prefix: the cyclic prefix is a parameter (TS 36.211 clause 6.1)."""
    NORMAL_CP = True

    def test_the_wrong_cyclic_prefix_does_not_lock_as_well(self):
        # a mixer told the wrong cyclic prefix correlates with a subframe that is not the one on the air: its match is clearly weaker
        right = self.mixer()
        run(right, self.stream(4, 85.3))
        wrong = Mixer(self.CELLS, lambda cid, t: self.DELAYS[cid], lambda: 10**18, normal_cp=False)
        run(wrong, self.stream(4, 85.3))
        self.assertIsNotNone(right.p0)
        self.assertLess(wrong.best_lock_ratio, right.best_lock_ratio / 2)


if __name__ == "__main__":
    unittest.main()
