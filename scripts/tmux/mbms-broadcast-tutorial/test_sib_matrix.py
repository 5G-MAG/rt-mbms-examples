#!/usr/bin/env python3
"""SIB13/MBSFN parameter test-matrix helper.

Wraps the eNB's live control socket (127.0.0.1:2100, line-based GET/SET
protocol) and the modem's REST API (http://10.80.0.2:3010/modem-api/, reached
via the receive-netns veth pair, NOT localhost) to apply a declared set of
SIB13/MBSFN parameter changes, wait for over-the-air propagation, and verify
the modem actually decoded what was set.

Does NOT start/stop/restart the eNB or modem processes -- those stay a
manual, confirmed step (see the fembms-mbsfn-cfo-investigation and
sequential-noodling-newt test plan). Only handles cases reachable via the
live control socket (no restart needed). Cases that need a restart (n_prb,
and the SCS-via-config-file route) are listed with a NOTE for the manual
procedure instead of being run automatically.

Usage:
    python3 test_sib_matrix.py --list          # show the case matrix, don't run
    python3 test_sib_matrix.py --case mcs_2    # run a single case by id
    python3 test_sib_matrix.py --phase mcs     # run all cases in one phase
    python3 test_sib_matrix.py                 # run every live-reloadable case
"""
import argparse
import json
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass, field

CONTROL_ADDR = ("127.0.0.1", 2100)
REST_BASE = "http://10.80.0.2:3010/modem-api"
PROPAGATION_WAIT_S = 8  # mcch_modification_period (5.12s) + repetition (0.64s) + decode margin


def control_send(line: str, timeout: float = 3.0) -> str:
    """Send one line to the eNB control socket, return its response."""
    with socket.create_connection(CONTROL_ADDR, timeout=timeout) as s:
        s.sendall((line.strip() + "\n").encode())
        s.settimeout(timeout)
        chunks = []
        try:
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
        except socket.timeout:
            pass
        return b"".join(chunks).decode(errors="replace")


def control_get() -> dict:
    resp = control_send("GET")
    out = {}
    for line in resp.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def control_set(**kwargs) -> str:
    tokens = " ".join(f"{k}={v}" for k, v in kwargs.items())
    return control_send(f"SET {tokens}")


def rest_get(path: str) -> dict:
    with urllib.request.urlopen(f"{REST_BASE}/{path}", timeout=5) as r:
        return json.loads(r.read().decode())


@dataclass
class Case:
    id: str
    phase: str
    description: str
    live_reloadable: bool
    apply: dict = field(default_factory=dict)  # control_set() kwargs, if live_reloadable
    manual_note: str = ""  # what to do instead, if not live_reloadable
    verify: dict = field(default_factory=dict)  # expected sib_info field -> value (dotted path)
    compliance_note: str = ""  # e.g. TS 36.300 §15.3.3 caveat


def get_nested(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if part.isdigit():
            cur = cur[int(part)]
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


# ---- Test matrix (mirrors the approved plan's phases 2-4 + pmch_bandwidth
# part of phase 5; phases 1 (SCS) and the n_prb part of phase 5 need a
# restart and are listed as manual_note cases for the written procedure) ----

CASES = [
    Case("mcs_2", "mcs", "MCS=2 (low QPSK)", True,
         apply={"embms.mcs": 2},
         verify={"mcch.pmch_list.0.data_mcs": 2}),
    Case("mcs_9_baseline", "mcs", "MCS=9 (baseline)", True,
         apply={"embms.mcs": 9},
         verify={"mcch.pmch_list.0.data_mcs": 9}),
    Case("mcs_10", "mcs", "MCS=10 (16QAM low boundary)", True,
         apply={"embms.mcs": 10},
         verify={"mcch.pmch_list.0.data_mcs": 10}),
    Case("mcs_16", "mcs", "MCS=16 (16QAM high boundary)", True,
         apply={"embms.mcs": 16},
         verify={"mcch.pmch_list.0.data_mcs": 16}),
    Case("mcs_17", "mcs", "MCS=17 (64QAM low boundary)", True,
         apply={"embms.mcs": 17},
         verify={"mcch.pmch_list.0.data_mcs": 17}),
    Case("mcs_28", "mcs", "MCS=28 (64QAM high boundary -- likely clamped for n_prb=25, that's the point)", True,
         apply={"embms.mcs": 28},
         verify={}),  # deliberately no fixed expectation -- check actual decoded value against clamping
    Case("mcs_table2_22", "mcs", "MCS=22 with 256QAM table (Rel-19)", True,
         apply={"embms.use_mcs_table2": "true", "embms.mcs": 22},
         verify={"mcch.pmch_list.0.use_mcs_table2": True}),

    Case("freq_interleaving_enable", "freq_interleaving", "Rel-19 pmch-TFI-Config frequency interleaving (on/off flag, no N/M like time interleaving)", True,
         apply={"embms.freq_interleaving": "true"},
         verify={"mcch.pmch_list.0.freq_interleaving": True}),
    Case("freq_interleaving_disable_restore", "freq_interleaving", "restore freq_interleaving disabled after the test", True,
         apply={"embms.freq_interleaving": "false"},
         verify={"mcch.pmch_list.0.freq_interleaving": False}),

    # NOTE 2026-07-15: verify expectations updated after root-causing the "M always
    # decodes as 4" bug. Real cause: sf_alloc_end under this baseline (mch_sched_period_rf=64,
    # no CAS muting, additional_non_mbsfn_subframes=0) is 623, which has no divisor in the
    # legal pmch-TimeInterleavingM-r19 enum {4,8,16,32} at ANY valid mch_sched_period_rf
    # (confirmed by direct computation) -- so time interleaving is structurally infeasible
    # under this baseline, for any N/M. Fixed rrc.cc to disable TI honestly (N=0,M=0, clear
    # warning) instead of silently defaulting to a misleading M=4. These cases now correctly
    # expect disable, not their nominal N/M -- they verify the eNB recognizes infeasibility
    # safely, not that TI functionally works (it structurally cannot, under this baseline).
    Case("ti_2_4", "time_interleaving", "N=2, M=4 -- expect safe disable, sf_alloc_end=623 has no legal M divisor", True,
         apply={"embms.time_interleaving_n": 2, "embms.time_interleaving_m": 4},
         verify={"mcch.pmch_list.0.time_interleaving_n": 0, "mcch.pmch_list.0.time_interleaving_m": 0},
         compliance_note="TS 36.211 §6.5.3: sf_alloc_end=623 has no divisor among the legal M values {4,8,16,32} -- eNB correctly disables TI rather than signal an infeasible M."),
    Case("ti_4_8", "time_interleaving", "N=4, M=8 -- expect safe disable, same reason as ti_2_4", True,
         apply={"embms.time_interleaving_n": 4, "embms.time_interleaving_m": 8},
         verify={"mcch.pmch_list.0.time_interleaving_n": 0, "mcch.pmch_list.0.time_interleaving_m": 0},
         compliance_note="Same TS 36.211 §6.5.3 infeasibility as ti_2_4."),
    Case("ti_8_16", "time_interleaving", "N=8, M=16 -- expect safe disable, same reason as ti_2_4", True,
         apply={"embms.time_interleaving_n": 8, "embms.time_interleaving_m": 16},
         verify={"mcch.pmch_list.0.time_interleaving_n": 0, "mcch.pmch_list.0.time_interleaving_m": 0},
         compliance_note="Same TS 36.211 §6.5.3 infeasibility as ti_2_4."),
    Case("ti_16_32", "time_interleaving", "N=16, M=32 (max) -- expect safe disable, same reason as ti_2_4", True,
         apply={"embms.time_interleaving_n": 16, "embms.time_interleaving_m": 32},
         verify={"mcch.pmch_list.0.time_interleaving_n": 0, "mcch.pmch_list.0.time_interleaving_m": 0},
         compliance_note="Same TS 36.211 §6.5.3 infeasibility as ti_2_4."),
    Case("ti_disabled_restore", "time_interleaving", "restore N=0 (disabled) after the sweep", True,
         apply={"embms.time_interleaving_n": 0, "embms.time_interleaving_m": 4},
         verify={"mcch.pmch_list.0.time_interleaving_n": 0}),

    Case("muting_4_2", "cas_muting", "k_cas=4, n_cas=2", True,
         apply={"embms.cas_muting": "true", "embms.k_cas": 4, "embms.n_cas": 2},
         verify={"sib1.cas_muting_enabled": True, "sib1.k_cas": 4, "sib1.n_cas": 2}),
    Case("muting_8_4", "cas_muting", "k_cas=8, n_cas=4", True,
         apply={"embms.cas_muting": "true", "embms.k_cas": 8, "embms.n_cas": 4},
         verify={"sib1.cas_muting_enabled": True, "sib1.k_cas": 8, "sib1.n_cas": 4}),
    Case("muting_16_8", "cas_muting", "k_cas=16, n_cas=8", True,
         apply={"embms.cas_muting": "true", "embms.k_cas": 16, "embms.n_cas": 8},
         verify={"sib1.cas_muting_enabled": True, "sib1.k_cas": 16, "sib1.n_cas": 8}),
    Case("muting_32_16", "cas_muting", "k_cas=32, n_cas=16", True,
         apply={"embms.cas_muting": "true", "embms.k_cas": 32, "embms.n_cas": 16},
         verify={"sib1.cas_muting_enabled": True, "sib1.k_cas": 32, "sib1.n_cas": 16}),
    Case("muting_disabled_restore", "cas_muting", "restore muting disabled after the sweep", True,
         apply={"embms.cas_muting": "false"},
         verify={"sib1.cas_muting_enabled": False}),

    Case("bw_pmch_30", "bandwidth", "pmch_bandwidth=30 (extended coverage) at n_prb=25", True,
         apply={"embms.pmch_bandwidth": 30},
         verify={"sib13.areas.0.pmch_bandwidth": 30}),
    Case("bw_pmch_35", "bandwidth", "pmch_bandwidth=35 at n_prb=25", True,
         apply={"embms.pmch_bandwidth": 35},
         verify={"sib13.areas.0.pmch_bandwidth": 35}),
    Case("bw_pmch_40", "bandwidth", "pmch_bandwidth=40 at n_prb=25", True,
         apply={"embms.pmch_bandwidth": 40},
         verify={"sib13.areas.0.pmch_bandwidth": 40}),
    Case("bw_pmch_0_restore", "bandwidth", "restore pmch_bandwidth=0 (=carrier) after the sweep", True,
         apply={"embms.pmch_bandwidth": 0},
         verify={"sib13.areas.0.pmch_bandwidth": 0}),

    # Restart-needed cases: not run by this script, listed for the written procedure.
    Case("scs_15khz", "scs", "SCS=15kHz", False,
         manual_note="Edit sib.conf.mbsfn subcarrier_spacing='khz15', clear [embms] subcarrier_spacing to empty (control-socket override rejects khz15 explicitly), restart eNB+modem."),
    Case("scs_7dot5khz", "scs", "SCS=7.5kHz", False,
         manual_note="[embms] subcarrier_spacing=khz7dot5 in enb_baseline.conf, restart eNB+modem."),
    Case("scs_2dot5khz", "scs", "SCS=2.5kHz", False,
         manual_note="[embms] subcarrier_spacing=khz2dot5 in enb_baseline.conf, restart eNB+modem."),
    Case("bw_nprb_6", "bandwidth", "n_prb=6", False,
         manual_note="[enb] n_prb=6 in enb_baseline.conf, restart eNB+modem (not live-reloadable)."),
    Case("bw_nprb_15", "bandwidth", "n_prb=15", False,
         manual_note="[enb] n_prb=15 in enb_baseline.conf, restart eNB+modem."),
    Case("bw_nprb_50", "bandwidth", "n_prb=50 (already validated this session as CINR baseline)", False,
         manual_note="[enb] n_prb=50 in enb_baseline.conf, restart eNB+modem."),
    Case("bw_nprb_75", "bandwidth", "n_prb=75", False,
         manual_note="[enb] n_prb=75 in enb_baseline.conf, restart eNB+modem."),
    Case("bw_nprb_100", "bandwidth", "n_prb=100", False,
         manual_note="[enb] n_prb=100 in enb_baseline.conf, restart eNB+modem. Watch for the BER-calc segfault workaround (KNOWN_ISSUES.md / README.md) if this crashes."),

    Case("interact_ti4_8_muting8_4", "interaction", "TI(4,8) + muting(8,4) combined", True,
         apply={"embms.time_interleaving_n": 4, "embms.time_interleaving_m": 8,
                "embms.cas_muting": "true", "embms.k_cas": 8, "embms.n_cas": 4},
         verify={"mcch.pmch_list.0.time_interleaving_n": 4, "mcch.pmch_list.0.time_interleaving_m": 8,
                 "sib1.cas_muting_enabled": True, "sib1.k_cas": 8, "sib1.n_cas": 4},
         compliance_note="Same §15.3.3 caveat as the standalone TI cases."),
    Case("interact_ti8_16_muting16_8", "interaction", "TI(8,16) + muting(16,8) combined", True,
         apply={"embms.time_interleaving_n": 8, "embms.time_interleaving_m": 16,
                "embms.cas_muting": "true", "embms.k_cas": 16, "embms.n_cas": 8},
         verify={"mcch.pmch_list.0.time_interleaving_n": 8, "mcch.pmch_list.0.time_interleaving_m": 16,
                 "sib1.cas_muting_enabled": True, "sib1.k_cas": 16, "sib1.n_cas": 8},
         compliance_note="Same §15.3.3 caveat as the standalone TI cases."),
]


def run_case(c: Case) -> dict:
    result = {"id": c.id, "phase": c.phase, "description": c.description}
    if not c.live_reloadable:
        result["status"] = "MANUAL"
        result["note"] = c.manual_note
        return result

    set_resp = control_set(**c.apply)
    if set_resp.strip().startswith("ERROR"):
        result["status"] = "FAIL"
        result["note"] = f"control SET rejected: {set_resp.strip()}"
        return result

    time.sleep(PROPAGATION_WAIT_S)

    try:
        sib_info = rest_get("sib_info")
    except Exception as e:
        result["status"] = "FAIL"
        result["note"] = f"REST query failed: {e}"
        return result

    mismatches = []
    for path, expected in c.verify.items():
        actual = get_nested(sib_info, path)
        if actual != expected:
            mismatches.append(f"{path}: expected {expected!r}, got {actual!r}")

    try:
        mch = rest_get("mch_status/0")
    except Exception:
        mch = {}

    result["decoded_mcs"] = get_nested(sib_info, "mcch.pmch_list.0.data_mcs")
    result["bler"] = mch.get("bler")
    result["mch_present"] = mch.get("present")

    if mismatches:
        result["status"] = "FAIL"
        result["note"] = "; ".join(mismatches)
    elif c.compliance_note:
        result["status"] = "PASS (non-compliant)"
        result["note"] = c.compliance_note
    else:
        result["status"] = "PASS"
        result["note"] = ""
    return result


def print_report(results: list):
    print(f"\n{'ID':<28} {'Phase':<14} {'Status':<18} {'BLER':<8} Note")
    print("-" * 110)
    for r in results:
        print(f"{r['id']:<28} {r['phase']:<14} {r['status']:<18} "
              f"{str(r.get('bler', '-')):<8} {r.get('note', '')}")


def write_report_md(results: list, path: str):
    lines = ["# SIB13/MBSFN test matrix results\n",
             "| ID | Phase | Status | Decoded MCS | BLER | Note |",
             "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['phase']} | {r['status']} | "
                      f"{r.get('decoded_mcs', '-')} | {r.get('bler', '-')} | {r.get('note', '')} |")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list cases, don't run")
    ap.add_argument("--case", help="run a single case by id")
    ap.add_argument("--phase", help="run all cases in one phase")
    ap.add_argument("--report", default="sib_matrix_report.md")
    args = ap.parse_args()

    if args.list:
        for c in CASES:
            tag = "LIVE" if c.live_reloadable else "MANUAL"
            print(f"{c.id:<28} [{tag:<6}] {c.phase:<14} {c.description}")
        return

    if args.case:
        cases = [c for c in CASES if c.id == args.case]
        if not cases:
            print(f"no such case: {args.case}", file=sys.stderr)
            sys.exit(1)
    elif args.phase:
        cases = [c for c in CASES if c.phase == args.phase]
    else:
        cases = CASES

    results = [run_case(c) for c in cases]
    print_report(results)
    write_report_md(results, args.report)


if __name__ == "__main__":
    main()
