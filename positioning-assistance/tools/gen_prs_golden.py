"""Golden vectors for the C implementation of the PRS mapping in rt-mbms-tx and rt-mbms-modem. [design]

Writes, for a list of cases, the non-zero resource elements of one slot as `bps.prs.prs_slot_grid` gives them (TS 36.211 V19.3.0 clauses 6.10.4.1 and
6.10.4.2, with the PSS, SSS and PBCH core set left empty), one line per element: `k l re im`. It also writes the samples of a few time-domain subframe waveforms at fixed indices (`wave ...`, then `index re im`) and, for a list of PRS configurations, how many subframes of 0..10239 are active
and the sum of their numbers (`active index N_PRS muting count sum`). The C test reads this file and compares.

    PYTHONPATH=. python3 tools/gen_prs_golden.py > prs_golden.txt
"""
import sys

import numpy as np

from bps.prs import prs_active, prs_slot_grid, prs_subframe

# name, n_id_prs, n_s, n_rb_dl, n_rb_prs, four_pbch_ports, n_f, mbms_dedicated, normal_cp (the last is optional and defaults to 1)
CASES = [
    ("ded_sync_slot0", 1, 0, 25, 25, 0, 0, 1),
    ("ded_pbch_slot1", 1, 1, 25, 25, 0, 0, 1),
    ("ded_pbch_slot1_4ports", 1, 1, 25, 25, 1, 0, 1),
    ("ded_frame1_no_sync", 1, 0, 25, 25, 0, 1, 1),
    ("ded_slot10_no_sync", 4, 10, 25, 25, 0, 0, 1),
    ("plain_slot2", 2, 2, 25, 25, 0, 0, 1),
    ("plain_slot3_narrow", 2, 3, 25, 15, 0, 0, 1),
    ("id300_slot4_50prb", 300, 4, 50, 25, 0, 0, 1),
    ("id4095_slot19_100prb", 4095, 19, 100, 100, 0, 0, 1),
    ("id7_slot5_6prb", 7, 5, 6, 6, 0, 0, 1),
    ("other_sync_slot0", 1, 0, 25, 25, 0, 1, 0),
    ("other_sync_slot10", 1, 10, 25, 25, 0, 1, 0),
    ("other_pbch_slot1_frame1", 5, 1, 25, 25, 0, 1, 0),
]
# extended cyclic prefix, as the 5G Broadcast demo carrier uses
CASES += [("ext_ded_slot0", 1, 0, 25, 25, 0, 0, 1, 0), ("ext_ded_slot1", 1, 1, 25, 25, 0, 0, 1, 0), ("ext_ded_slot1_4ports", 1, 1, 25, 25, 1, 0, 1, 0),
          ("ext_ded_frame1_slot0", 1, 0, 25, 25, 0, 1, 1, 0), ("ext_plain_slot2", 3, 2, 25, 25, 0, 0, 1, 0), ("ext_plain_slot3_narrow", 3, 3, 25, 15, 0, 0, 1, 0),
          ("ext_other_slot10", 2, 10, 25, 25, 0, 1, 0, 0), ("ext_id4095_slot19_100prb", 4095, 19, 100, 100, 0, 0, 1, 0)]
# every frequency shift (N_ID^PRS mod 6) at the cell acquisition slots, so that a PRS element lands on each edge of the PSS, SSS and PBCH core sets
for _v in range(6):
    CASES.append((f"edge_sync_v{_v}", _v, 0, 25, 25, 0, 0, 1))
    CASES.append((f"edge_pbch_v{_v}", _v, 1, 25, 25, 0, 0, 1))
    CASES.append((f"ext_edge_sync_v{_v}", _v, 0, 25, 25, 0, 0, 1, 0))
    CASES.append((f"ext_edge_pbch_v{_v}", _v, 1, 25, 25, 0, 0, 1, 0))

# time-domain waveforms: name, n_id_prs, n_rb_dl, n_f, subframe, normal_cp
WAVES = [("wave_ext_cas", 1, 25, 0, 0, 0), ("wave_normal_cas", 1, 25, 0, 0, 1), ("wave_ext_id300_sf3", 300, 25, 1, 3, 0), ("wave_ext_50prb", 7, 50, 0, 0, 0)]
WAVE_INDICES = [0, 1, 127, 128, 129, 639, 640, 641, 1279, 3839, 3840, 3841, 4000, 5000, 7000, 7679]


# index, N_PRS, muting sequence ('-' for none)
ACTIVE = [(2435, 1, "-"), (2435, 2, "-"), (2436, 1, "-"), (2400, 1, "-"), (2475, 2, "-"), (2435, 1, "0110"), (2435, 2, "01"),
          (1120, 1, "1000000011111111"), (1120, 1, "10"), (0, 6, "-"), (160, 4, "1100"), (2554, 1, "-")]


def main() -> None:
    print("# PRS golden vectors: python3 tools/gen_prs_golden.py (rt-mbms-examples positioning-assistance), TS 36.211 V19.3.0 6.10.4")
    for case in CASES:
        name, nid, n_s, n_rb_dl, n_rb_prs, four, n_f, ded = case[:8]
        normal_cp = case[8] if len(case) > 8 else 1
        g = prs_slot_grid(nid, n_s, n_rb_dl, n_rb_prs, normal_cp=bool(normal_cp), four_pbch_ports=bool(four), n_f=n_f, mbms_dedicated=bool(ded))
        ks, ls = g.nonzero()
        print(f"case {name} {nid} {n_s} {n_rb_dl} {n_rb_prs} {four} {n_f} {ded} {normal_cp} {len(ks)}")
        for k, l in sorted(zip(ks.tolist(), ls.tolist()), key=lambda t: (t[1], t[0])):
            print(f"{k} {l} {g[k, l].real:.9f} {g[k, l].imag:.9f}")
    for name, nid, n_rb_dl, n_f, subframe, normal_cp in WAVES:
        w = prs_subframe(nid, subframe, n_rb_dl, n_f=n_f, normal_cp=bool(normal_cp))
        energy = float(np.sum(np.abs(w) ** 2))
        print(f"wave {name} {nid} {n_rb_dl} {n_f} {subframe} {normal_cp} {len(w)} {energy:.6f} {len(WAVE_INDICES)}")
        for i in WAVE_INDICES:
            print(f"{i} {w[i].real:.9f} {w[i].imag:.9f}")
    for index, n_prs, muting in ACTIVE:
        ticks = [t for t in range(10240) if prs_active(index, n_prs, t, None if muting == "-" else muting)]
        print(f"active {index} {n_prs} {muting} {len(ticks)} {sum(ticks)}")


if __name__ == "__main__":
    sys.exit(main())
