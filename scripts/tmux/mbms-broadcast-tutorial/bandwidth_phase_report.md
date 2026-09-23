# SIB13/MBSFN test matrix results

| ID | Phase | Status | Decoded MCS | BLER | Note |
|---|---|---|---|---|---|
| bw_pmch_30 | bandwidth | FAIL | 9 | 0 | sib13.areas.0.pmch_bandwidth: expected 30, got 0 |
| bw_pmch_35 | bandwidth | FAIL | 9 | 0 | sib13.areas.0.pmch_bandwidth: expected 35, got 0 |
| bw_pmch_40 | bandwidth | FAIL | 9 | 0 | sib13.areas.0.pmch_bandwidth: expected 40, got 0 |
| bw_pmch_0_restore | bandwidth | PASS | 9 | 0 |  |
| bw_nprb_6 | bandwidth | MANUAL | - | - | [enb] n_prb=6 in enb_baseline.conf, restart eNB+modem (not live-reloadable). |
| bw_nprb_15 | bandwidth | MANUAL | - | - | [enb] n_prb=15 in enb_baseline.conf, restart eNB+modem. |
| bw_nprb_50 | bandwidth | MANUAL | - | - | [enb] n_prb=50 in enb_baseline.conf, restart eNB+modem. |
| bw_nprb_75 | bandwidth | MANUAL | - | - | [enb] n_prb=75 in enb_baseline.conf, restart eNB+modem. |
| bw_nprb_100 | bandwidth | MANUAL | - | - | [enb] n_prb=100 in enb_baseline.conf, restart eNB+modem. Watch for the BER-calc segfault workaround (KNOWN_ISSUES.md / README.md) if this crashes. |
