"""Turns the results of bps.scenario_run into one static HTML page. [design]

    python3 -m bps.scenario_report result.json [result2.json ...] [-o report.html]

One result gives its accuracy figures, a plan view (cells, the UE's true position, every fix), the cumulative distribution of the error, the claimed against
the real error and the time-difference error of each cell. Several results are compared in one table and one distribution plot, so a sweep over a parameter
reads at a glance. The page is self-contained (inline SVG, no scripts, no network).
"""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

import numpy as np

PALETTE = ["#1f6feb", "#d1242f", "#1a7f37", "#9a6700", "#8250df", "#0a7e8c"]
W, H, M = 460, 360, 44          # plot size and margin, in px

CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#1f2328;background:#fff}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem}table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #d0d7de;padding:.25rem .6rem;text-align:right}
th:first-child,td:first-child{text-align:left}.plots{display:flex;flex-wrap:wrap;gap:1rem}svg{background:#f6f8fa;border:1px solid #d0d7de}.note{color:#57606a}
@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}td,th,svg{border-color:#30363d}svg{background:#161b22}.note{color:#8d96a0}}"""


def _fmt(v, nd=1) -> str:
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{nd}f}"


def _step(span: float) -> float:
    """A tick step of 1, 2 or 5 times a power of ten that gives about four to six ticks over `span`."""
    raw = span / 5.0
    base = 10 ** math.floor(math.log10(raw))
    return next(m * base for m in (1, 2, 5, 10) if m * base >= raw)


def _scale(lo: float, hi: float, a: float, b: float):
    span = (hi - lo) or 1.0
    return lambda v: a + (v - lo) / span * (b - a)


def _axes(xlab: str, ylab: str, xticks, yticks, sx, sy) -> str:
    out = [f'<rect x="{M}" y="{M // 2}" width="{W - M - 12}" height="{H - M - M // 2}" fill="none" stroke="currentColor" opacity=".35"/>']
    for t in xticks:
        out.append(f'<text x="{sx(t):.1f}" y="{H - M + 16}" font-size="10" text-anchor="middle" fill="currentColor">{t:g}</text>')
    for t in yticks:
        out.append(f'<text x="{M - 6}" y="{sy(t) + 3:.1f}" font-size="10" text-anchor="end" fill="currentColor">{t:g}</text>')
    out.append(f'<text x="{(M + W - 12) / 2:.0f}" y="{H - 6}" font-size="11" text-anchor="middle" fill="currentColor">{html.escape(xlab)}</text>')
    out.append(f'<text transform="translate(12 {H / 2:.0f}) rotate(-90)" font-size="11" text-anchor="middle" fill="currentColor">{html.escape(ylab)}</text>')
    return "".join(out)


def plan_view(res: dict, colour: str) -> str:
    """Cells and the true UE position from the scenario, and every fix the UE gave, east and north in metres."""
    cells = np.array(res["cells_enu"], float)
    ue = np.array(res["ue_enu"], float)
    fixes = [r for r in res["records"] if r["kind"] == "fix"]
    pts = [(ue[0] + f["east_err_m"], ue[1] + f["north_err_m"]) for f in fixes]
    xs = list(cells[:, 0]) + [ue[0]] + [p[0] for p in pts]
    ys = list(cells[:, 1]) + [ue[1]] + [p[1] for p in pts]
    pad = 0.08 * max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    lo_x, hi_x, lo_y, hi_y = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    side = max(hi_x - lo_x, hi_y - lo_y)                       # same scale on both axes
    cx, cy = (lo_x + hi_x) / 2, (lo_y + hi_y) / 2
    lo_x, hi_x, lo_y, hi_y = cx - side / 2, cx + side / 2, cy - side / 2, cy + side / 2
    sx, sy = _scale(lo_x, hi_x, M, W - 12), _scale(lo_y, hi_y, H - M, M // 2)
    step = _step(side) if side > 0 else 1
    ticks = lambda lo, hi: [t * step for t in range(math.ceil(lo / step), math.floor(hi / step) + 1)]
    out = [f'<svg width="{W}" height="{H}" role="img" aria-label="Plan view: cells, true position and fixes">',
           _axes("east (m)", "north (m)", ticks(lo_x, hi_x), ticks(lo_y, hi_y), sx, sy)]
    for x, y in pts:
        out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2.2" fill="{colour}" opacity=".45"/>')
    for i, (x, y, _z) in enumerate(cells, 1):
        out.append(f'<path d="M{sx(x):.1f} {sy(y) - 7:.1f} l6 11 h-12 z" fill="currentColor"/>'
                   f'<text x="{sx(x) + 8:.1f}" y="{sy(y) + 4:.1f}" font-size="11" fill="currentColor">cell {i}</text>')
    out.append(f'<path d="M{sx(ue[0]) - 6:.1f} {sy(ue[1]):.1f} h12 M{sx(ue[0]):.1f} {sy(ue[1]) - 6:.1f} v12" stroke="#d1242f" stroke-width="2"/>'
               f'<text x="{sx(ue[0]) + 8:.1f}" y="{sy(ue[1]) - 8:.1f}" font-size="11" fill="#d1242f">true position</text></svg>')
    return "".join(out)


def error_scatter(res: dict, colour: str) -> str:
    """Every fix as its east and north error in metres around the true position, with the circle of the rms error (the plan view is too coarse to show it)."""
    fixes = [r for r in res["records"] if r["kind"] == "fix"]
    pts = [(f["east_err_m"], f["north_err_m"]) for f in fixes]
    lim = max([abs(v) for p in pts for v in p] + [res["summary"].get("rms_m", 0.0), 1.0]) * 1.15
    step = _step(2 * lim)
    lim = math.ceil(lim / step) * step
    sx, sy = _scale(-lim, lim, M, W - 12), _scale(-lim, lim, H - M, M // 2)
    ticks = [i * step for i in range(-int(lim / step), int(lim / step) + 1)]
    out = [f'<svg width="{W}" height="{H}" role="img" aria-label="East and north error of every fix">',
           _axes("east error (m)", "north error (m)", ticks, ticks, sx, sy),
           f'<path d="M{sx(0):.1f} {sy(-lim):.1f} V{sy(lim):.1f} M{sx(-lim):.1f} {sy(0):.1f} H{sx(lim):.1f}" stroke="currentColor" opacity=".3"/>']
    for x, y in pts:
        out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="2.4" fill="{colour}" opacity=".55"/>')
    rms = res["summary"].get("rms_m")
    if rms:
        out.append(f'<ellipse cx="{sx(0):.1f}" cy="{sy(0):.1f}" rx="{sx(rms) - sx(0):.1f}" ry="{sy(0) - sy(rms):.1f}" fill="none" stroke="#d1242f" stroke-dasharray="4 3"/>'
                   f'<text x="{sx(0) + 6:.1f}" y="{sy(rms) - 4:.1f}" font-size="10" fill="#d1242f">rms error</text>')
    out.append("</svg>")
    return "".join(out)


def cdf_plot(results: list[dict]) -> str:
    """Cumulative distribution of the horizontal error, one curve per result."""
    curves = [(r["name"], sorted(f["error_m"] for f in r["records"] if f["kind"] == "fix")) for r in results]
    top = max([c[-1] for _, c in curves if c] + [1.0])
    step = _step(top)
    xmax = math.ceil(top / step) * step
    sx, sy = _scale(0, xmax, M, W - 12), _scale(0, 1, H - M, M // 2)
    out = [f'<svg width="{W + 150}" height="{H}" role="img" aria-label="Cumulative distribution of the position error">',
           _axes("horizontal error (m)", "share of fixes", [i * step for i in range(int(xmax / step) + 1)], [0, .25, .5, .75, 1], sx, sy)]
    for i, (name, e) in enumerate(curves):
        if not e:
            continue
        pts = " ".join(f"{sx(v):.1f},{sy((k + 1) / len(e)):.1f}" for k, v in enumerate(e))
        out.append(f'<polyline points="{sx(0):.1f},{sy(0):.1f} {pts}" fill="none" stroke="{PALETTE[i % len(PALETTE)]}" stroke-width="1.8"{' stroke-dasharray="5 3"' if i >= len(PALETTE) else ''}/>'
                   f'<text x="{W - 6}" y="{M // 2 + 14 + 14 * i}" font-size="11" fill="{PALETTE[i % len(PALETTE)]}">{html.escape(name)}</text>')
    out.append("</svg>")
    return "".join(out)


def claimed_plot(res: dict, colour: str) -> str:
    """Real error against the error the solver claims: points under the diagonal are fixes better than claimed."""
    fixes = [r for r in res["records"] if r["kind"] == "fix"]
    top = max([max(f["error_m"], f["claimed_m"]) for f in fixes] + [1.0])
    step = _step(top)
    lim = math.ceil(top / step) * step
    sx, sy = _scale(0, lim, M, W - 12), _scale(0, lim, H - M, M // 2)
    ticks = [i * step for i in range(int(lim / step) + 1)]
    out = [f'<svg width="{W}" height="{H}" role="img" aria-label="Real error against claimed error">',
           _axes("claimed accuracy (m)", "real error (m)", ticks, ticks, sx, sy),
           f'<line x1="{sx(0):.1f}" y1="{sy(0):.1f}" x2="{sx(lim):.1f}" y2="{sy(lim):.1f}" stroke="currentColor" opacity=".4" stroke-dasharray="4 3"/>']
    for f in fixes:
        out.append(f'<circle cx="{sx(f["claimed_m"]):.1f}" cy="{sy(f["error_m"]):.1f}" r="2.2" fill="{colour}" opacity=".5"/>')
    out.append("</svg>")
    return "".join(out)


def summary_table(results: list[dict]) -> str:
    cols = [("fixes", "fixes", 0), ("rms_m", "rms (m)", 1), ("mean_m", "mean (m)", 1), ("p50_m", "median (m)", 1), ("p95_m", "95 % (m)", 1), ("max_m", "max (m)", 1),
            ("claimed_mean_m", "claimed (m)", 1), ("claimed_ratio", "real / claimed", 2)]
    rows = ["<tr><th>scenario</th>" + "".join(f"<th>{h}</th>" for _, h, _ in cols) + "<th>refusals</th></tr>"]
    for r in results:
        s = r["summary"]
        refusals = ", ".join(f"{html.escape(k)}: {v}" for k, v in s["refusals"].items()) or "none"
        rows.append(f"<tr><td>{html.escape(r['name'])}</td>" + "".join(f"<td>{_fmt(s.get(k), nd)}</td>" for k, _, nd in cols) + f"<td>{refusals}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def rstd_table(res: dict) -> str:
    """Error of each measured time difference against the one the geometry gives, in ns (1 ns = 0.3 m of range)."""
    per = res["summary"].get("rstd_error_ns", {})
    if not per:
        return "<p class=note>No fix, so no time differences to compare.</p>"
    rows = ["<tr><th>cell</th><th>n</th><th>mean (ns)</th><th>sd (ns)</th><th>rms (ns)</th><th>rms (m of range)</th></tr>"]
    for cid, v in per.items():
        rows.append(f"<tr><td>{html.escape(str(cid))}</td><td>{v['n']}</td><td>{v['mean']:.1f}</td><td>{v['sd']:.1f}</td><td>{v['rms']:.1f}</td><td>{v['rms'] * 0.299792458:.1f}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def scenario_table(res: dict) -> str:
    """What the run was given, so a figure is never read without its conditions."""
    sc = res["scenario"]
    rows = [("received power per emulated cell (dB)", ", ".join(f"cell {c}: {p:.1f}" for c, p in sorted(res["received_power_db"].items()))),
            ("true position (east, north, up in m)", ", ".join(f"{v:.1f}" for v in res["ue_enu"])),
            ("emulated time difference of each cell (ns base, ns/s drift)", json.dumps(sc["rtd"]) if "rtd" in sc else "lab default"),
            ("noise (Es/N0, dB)", sc.get("snr_db", "none")), ("monitoring receiver timestamp noise (ns, 1-sigma; the document's RTD is estimated from it)", sc.get("monitor_noise_ns", "20 (lab default)")), ("carrier offset (Hz)", json.dumps(sc["cfo_hz"]) if "cfo_hz" in sc else "none"), ("cyclic prefix", sc.get("cp", "extended")),
            ("UE duration (s)", res["ue_seconds"]), ("revisions", ", ".join(f"{k} {v}" for k, v in res["revisions"].items()))]
    return "<table>" + "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in rows) + "</table>"


def page(results: list[dict]) -> str:
    body = ["<h1>Positioning accuracy on the 5G Broadcast platform</h1>",
            "<p class=note>Measured on the real chain: transmitter, rig with emulated cells, modem measuring the PRS, assistance document and SIB16 over the air, UE solver. "
            "The error is the distance between the solved position and the position the scenario gives the UE. Cells 2 to 4 are PRS-only subframes the rig adds to the transmitter's signal (cell 1), each at its own delay and power; "
            "the figures say what the method and this platform give under those conditions, not what a field deployment would.</p>",
            "<h2>Summary</h2>", summary_table(results), "<h2>Distribution of the error</h2>", cdf_plot(results)]
    for i, r in enumerate(results):
        colour = PALETTE[i % len(PALETTE)]
        body += [f"<h2>{html.escape(r['name'])}</h2>", scenario_table(r), '<div class="plots">', error_scatter(r, colour), claimed_plot(r, colour), plan_view(r, colour), "</div>",
                 "<h3>Time-difference error per cell</h3>", rstd_table(r)]
    return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Positioning accuracy</title><style>{CSS}</style><body>{''.join(body)}</body></html>")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", nargs="+")
    ap.add_argument("-o", "--out", default="report.html")
    a = ap.parse_args()
    results = []
    for p in a.results:
        with open(p) as fh:
            results.append(json.load(fh))
    Path(a.out).write_text(page(results))
    print(a.out)


if __name__ == "__main__":
    main()
