# SIB13/MBSFN test matrix results

| ID | Phase | Status | Decoded MCS | BLER | Note |
|---|---|---|---|---|---|
| ti_2_4 | time_interleaving | PASS (non-compliant) | 9 | 0 | TS 36.300 §15.3.3: TI'd MCH shouldn't carry MCCH, but this eNB always configures pmch_info_list[0] (MCCH-adjacent) -- runs, not spec-compliant. |
| ti_4_8 | time_interleaving | FAIL | 9 | 0 | mcch.pmch_list.0.time_interleaving_m: expected 8, got 4 |
| ti_8_16 | time_interleaving | FAIL | 9 | 0 | mcch.pmch_list.0.time_interleaving_m: expected 16, got 4 |
| ti_16_32 | time_interleaving | FAIL | 9 | 1 | mcch.pmch_list.0.time_interleaving_n: expected 16, got 8; mcch.pmch_list.0.time_interleaving_m: expected 32, got 4 |
| ti_disabled_restore | time_interleaving | FAIL | 9 | 1 | mcch.pmch_list.0.time_interleaving_n: expected 0, got 16 |
