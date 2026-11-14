"""Golden vectors for the C++ implementation of the PSS and SSS signal and of the time of arrival taken from them in rt-mbms-modem (src/SyncSignal.h). [design]

Written from `bps.sync` (TS 36.211 V19.3.0 clauses 6.11.1 and 6.11.2, MBMS-dedicated cell: PSS and SSS in slot 0 of subframe 0), which was compared with Table 6.11.2.1-1
for all 168 groups. One block per case, the C++ test reads the file and compares:

  grid <name> <cell_id> <n_rb_dl> <normal_cp> <count>        then <count> lines `k l re im`: the non-zero elements of slot 0
  wave <name> <cell_id> <n_rb_dl> <normal_cp> <n_samples> <energy> <count>   then <count> lines `index re im`: samples of the cell acquisition subframe
  window <name> <n_rb_dl> <normal_cp> <n_cells> <n_samples>  then <n_cells> lines `cell_id delay_ns power_db`, then <n_samples> lines `re im`: three subframes in
                                                              which every cell's cell acquisition subframe arrives `delay_ns` after the middle subframe's nominal
                                                              start, no noise; the C++ test measures the delays back

    PYTHONPATH=. python3 tools/gen_sync_golden.py > sync_golden.txt
"""
import numpy as np

from bps.prs import sample_rate_hz
from bps.sync import _delayed, cas_subframe, sync_slot_grid

GRIDS = [(0, 25, 1), (1, 25, 1), (2, 25, 0), (3, 6, 1), (100, 25, 1), (167 * 3 + 2, 25, 1), (299, 50, 0), (503, 100, 1), (30, 15, 1), (31, 75, 0)]
WAVES = [(1, 25, 1), (1, 25, 0), (2, 25, 0), (300, 25, 1), (7, 50, 0), (503, 6, 1)]
WAVE_INDICES = [0, 7, 150, 511, 1000, 2000, 3000, 3500, 3840, 4000, 4300, 5000, 5500, 6000, 7000, 7600]
# name, n_rb, normal_cp, cells (cell_id, delay_ns, power_db)
WINDOWS = [("win_6prb_three", 6, 1, [(1, 0.0, 0.0), (2, 1234.5, -3.0), (302, -2345.25, -6.0)]),
           ("win_6prb_ext_two", 6, 0, [(1, 0.0, 0.0), (7, 3010.0, -9.0)])]


def main() -> None:
    for cell_id, n_rb, normal in GRIDS:
        g = sync_slot_grid(cell_id, n_rb, bool(normal))
        nz = [(k, l) for k in range(g.shape[0]) for l in range(g.shape[1]) if g[k, l] != 0]
        print(f"grid g{cell_id}_{n_rb}_{normal} {cell_id} {n_rb} {normal} {len(nz)}")
        for k, l in nz:
            print(f"{k} {l} {g[k, l].real:.9f} {g[k, l].imag:.9f}")
    for cell_id, n_rb, normal in WAVES:
        w = cas_subframe(cell_id, n_rb, bool(normal))
        idx = [i for i in WAVE_INDICES if i < len(w)]
        print(f"wave w{cell_id}_{n_rb}_{normal} {cell_id} {n_rb} {normal} {len(w)} {float(np.sum(np.abs(w) ** 2)):.6f} {len(idx)}")
        for i in idx:
            print(f"{i} {w[i].real:.9f} {w[i].imag:.9f}")
    for name, n_rb, normal, cells in WINDOWS:
        fs = sample_rate_hz(n_rb)
        sf = int(round(1e-3 * fs))
        n_win = 3 * sf
        freqs = np.fft.fftfreq(n_win, d=1 / fs)
        rx = np.zeros(n_win, dtype=complex)
        for cell_id, delay_ns, power_db in cells:
            ref = cas_subframe(cell_id, n_rb, bool(normal))
            padded = np.zeros(n_win, dtype=complex)
            padded[: len(ref)] = ref
            rx += 10 ** (power_db / 20) * _delayed(np.fft.fft(padded), freqs, 1e-3 + delay_ns * 1e-9)
        print(f"window {name} {n_rb} {normal} {len(cells)} {n_win}")
        for cell_id, delay_ns, power_db in cells:
            print(f"{cell_id} {delay_ns} {power_db}")
        for v in rx:
            print(f"{v.real:.9f} {v.imag:.9f}")


if __name__ == "__main__":
    main()
