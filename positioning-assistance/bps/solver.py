"""UE-side position solution from RSTD measurements. [design] unless marked [spec].

Model: [spec] TS 36.214 V19.0.0 clause 5.1.12 defines RSTD as T_SubframeRx_j - T_SubframeRx_i, with the reference
cell's subframe "closest in time". With propagation d/c and a relative transmit delay RTD_j (cell j's subframe grid
minus the reference's, wrapped to +-0.5 ms):

    RSTD_j = (|x - p_j| - |x - p_ref|) / c + RTD_j + noise

The unknown is the UE position x in a local east-north-up frame (metres). Absolute time does not enter the geometry.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .timebase import C_MPS, TimeAnchor, SUBFRAME_NS


@dataclass
class Solution:
    position_enu: np.ndarray  # metres
    covariance_m2: np.ndarray  # same axes as position (2x2 when height is fixed, else 3x3)
    rms_residual_s: float
    iterations: int
    converged: bool

    @property
    def horizontal_sigma_m(self) -> float:
        """1-sigma radial horizontal uncertainty from the east-north covariance block."""
        return float(np.sqrt(np.trace(self.covariance_m2[:2, :2])))


def _residuals_jacobian(x, cells, ref, rstd_s, rtd_s, free):
    d = np.linalg.norm(cells - x, axis=1)
    pred = (d - d[ref]) / C_MPS + rtd_s
    idx = [i for i in range(len(cells)) if i != ref]
    r = rstd_s[idx] - pred[idx]
    unit = (x - cells) / d[:, None]  # d|x-p|/dx
    jac = -(unit[idx] - unit[ref]) / C_MPS
    return r, jac[:, free], idx


def solve_rstd(
    cells_enu: np.ndarray,
    rstd_s: np.ndarray,
    rtd_s: np.ndarray,
    sigma_s: np.ndarray,
    ref: int = 0,
    fix_up_m: float | None = None,
    starts: int = 9,
    max_iter: int = 60,
) -> Solution:
    """Weighted Levenberg-Marquardt on the RSTD residuals; best of several starts (hyperbolic fixes can have two roots).

    cells_enu, rstd_s, rtd_s, sigma_s are length N (entries at the reference index are ignored for rstd/sigma).
    Needs N >= 3 with the height fixed (2D) or N >= 4 for 3D. [spec] The deck states "At least 2 (3) assistance eNBs
    are needed to solve the 2D (3D) positioning problem", i.e. N = 3 (4) including the reference cell.
    """
    cells = np.asarray(cells_enu, float)
    n = len(cells)
    free = [0, 1] if fix_up_m is not None else [0, 1, 2]
    if n - 1 < len(free):
        raise ValueError(f"{n} cells give {n - 1} RSTDs, need at least {len(free)} for {len(free)}D")
    idx = [i for i in range(n) if i != ref]
    w = 1.0 / np.asarray(sigma_s, float)[idx] ** 2

    centre = cells.mean(axis=0)
    span = max(np.ptp(cells[:, 0]), np.ptp(cells[:, 1]), 1.0)
    rng = np.random.default_rng(0)
    seeds = [centre] + [centre + rng.uniform(-span, span, 3) * np.array([1, 1, 0]) for _ in range(starts - 1)]

    best = None
    for s in seeds:
        x = s.copy()
        if fix_up_m is not None:
            x[2] = fix_up_m
        lam = 1e-3
        converged = False
        it = 0
        for it in range(1, max_iter + 1):
            r, jac, _ = _residuals_jacobian(x, cells, ref, rstd_s, rtd_s, free)
            cost = float(r @ (w * r))
            jtj = jac.T @ (w[:, None] * jac)
            g = jac.T @ (w * r)
            step = -np.linalg.solve(jtj + lam * np.diag(np.diag(jtj)) + 1e-30 * np.eye(len(free)), g)
            x_new = x.copy()
            x_new[free] += step
            r_new, _, _ = _residuals_jacobian(x_new, cells, ref, rstd_s, rtd_s, free)
            if float(r_new @ (w * r_new)) < cost:
                x, lam = x_new, lam * 0.3
                if np.linalg.norm(step) < 1e-3:
                    converged = True
                    break
            else:
                if np.linalg.norm(step) < 1e-3:  # no improving step of any size left: at the minimum to within 1 mm
                    converged = True
                    break
                lam *= 10
                if lam > 1e12:
                    break
        r, jac, _ = _residuals_jacobian(x, cells, ref, rstd_s, rtd_s, free)
        cost = float(r @ (w * r))
        if best is None or cost < best[0]:
            cov = np.linalg.pinv(jac.T @ (w[:, None] * jac))
            best = (cost, x.copy(), cov, float(np.sqrt(np.mean(r**2))), it, converged)
    cost, x, cov, rms, it, conv = best
    return Solution(x, cov, rms, it, conv)


def ue_time_ns(anchor: TimeAnchor, rx_abs_subframe: int, distance_to_ref_m: float) -> int:
    """GPS time at the UE antenna when the reference cell's subframe `rx_abs_subframe` starts arriving.

    [spec] the SIB16 time is "referenced at the network, i.e., without compensating for RF propagation delay"
    (TS 36.331 V19.4.0 clause 6.3.2), so once the position (and so the distance) is known the delay is added back.
    """
    return anchor.tx_time_ns(rx_abs_subframe) + round(distance_to_ref_m / C_MPS * 1e9)


__all__ = ["Solution", "solve_rstd", "ue_time_ns", "SUBFRAME_NS"]
