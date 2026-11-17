"""A scenario for the positioning lab: where the cells and the UE are, what the cells' timing and received powers are, and how noisy the channel is. [design]

The lab (bps.lab) has one fixed four-cell world. A scenario file replaces its numbers, so that the same real chain (rt-mbms-tx, the mixer, rt-mbms-modem, the
assistance document and the UE) runs on another geometry: the publisher, the mixer and the UE all read the file named by the environment variable BPS_SCENARIO and
see the same cells, RTDs and UE position. Without the variable nothing changes.

    {"name": "city-edge", "ue_enu": [2500, -3200, 1.5],
     "cells_enu": [[0, 0, 30], [9000, 2500, 45], [-4000, 8000, 35], [1500, -10500, 50]],      # cell 1 is the real cell, 2..4 are emulated
     "rtd": {"2": [123456, 3.0], "3": [-80000, -2.0], "4": [40000, 1.5]},                      # base ns at the world epoch, drift ns per s
     "power_db": {"2": -6, "3": -6, "4": -6},                                                    # received power against the real cell's, or:
     "path_loss_exponent": 3.0,                                                                  #   derived from the distances to the UE
     "tx_offset_db": {"2": 0, "3": 0, "4": 0},                                                   # with path loss: transmit power against the real cell's
     "snr_db": null, "cfo_hz": {"3": 150}, "monitor_noise_ns": 20, "cp": "extended", "seconds": 150}

The path loss model is only d^-n between the cells and the UE, with no shadowing and one path; it makes the received power of a distant cell fall the way a
simple model says, nothing more. All fields are optional; the lab's own values apply to those left out.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

ENV = "BPS_SCENARIO"
EMULATED = (2, 3, 4)


@dataclass
class Scenario:
    name: str = "lab"
    ue_enu: Optional[list] = None
    cells_enu: Optional[list] = None
    rtd: dict = field(default_factory=dict)          # {cell: [base_ns, drift_ns_per_s]}
    power_db: dict = field(default_factory=dict)     # {cell: dB against the real cell's PSS and SSS elements}
    path_loss_exponent: Optional[float] = None
    tx_offset_db: dict = field(default_factory=dict)
    snr_db: Optional[float] = None
    cfo_hz: dict = field(default_factory=dict)
    monitor_noise_ns: Optional[float] = None         # 1-sigma of the monitoring receiver's arrival timestamps; the document's RTD is estimated from them
    cp: str = "extended"
    seconds: int = 150

    def validate(self) -> None:
        if self.cells_enu is not None and len(self.cells_enu) != 4:
            raise ValueError("the lab chain has four cells: the real cell and the emulated 2, 3 and 4")
        for key in ("ue_enu",):
            v = getattr(self, key)
            if v is not None and len(v) != 3:
                raise ValueError(f"{key} is east, north, up in metres")
        if self.cells_enu is not None and any(len(p) != 3 for p in self.cells_enu):
            raise ValueError("a cell position is east, north, up in metres")
        for table in (self.rtd, self.power_db, self.tx_offset_db, self.cfo_hz):
            for k in table:
                if int(k) not in EMULATED:
                    raise ValueError(f"cell {k}: only the emulated cells 2, 3 and 4 take a setting")
        for k, v in self.rtd.items():
            if len(v) != 2:
                raise ValueError(f"rtd of cell {k}: [base ns, drift ns per s]")
        if self.cp not in ("extended", "normal"):
            raise ValueError("cp is extended or normal")
        if self.monitor_noise_ns is not None and self.monitor_noise_ns <= 0:
            raise ValueError("monitor_noise_ns must be positive")
        if self.path_loss_exponent is not None and self.path_loss_exponent <= 0:
            raise ValueError("path_loss_exponent must be positive")

    def received_power_db(self, cells_enu, ue_enu) -> dict:
        """Received power of each emulated cell against the real cell's at the UE: the explicit `power_db`, else the path loss model plus the transmit offset, else -6 dB
        (the lab's default)."""
        cells, ue = np.asarray(cells_enu, float), np.asarray(ue_enu, float)
        d = np.linalg.norm(cells - ue, axis=1)
        out = {}
        for cid in EMULATED:
            if str(cid) in self.power_db:
                out[cid] = float(self.power_db[str(cid)])
            elif self.path_loss_exponent is not None:
                out[cid] = float(-10.0 * self.path_loss_exponent * math.log10(max(d[cid - 1], 1.0) / max(d[0], 1.0)) + self.tx_offset_db.get(str(cid), 0.0))
            else:
                out[cid] = -6.0
        return out


def load(path: str) -> Scenario:
    with open(path) as fh:
        raw = json.load(fh)
    known = {f for f in Scenario.__dataclass_fields__}
    extra = set(raw) - known
    if extra:
        raise ValueError(f"unknown scenario field(s): {sorted(extra)}")
    for k in ("rtd", "power_db", "tx_offset_db", "cfo_hz"):
        if k in raw:
            raw[k] = {str(c): v for c, v in raw[k].items()}
    s = Scenario(**raw)
    s.validate()
    return s


def from_env() -> Optional[Scenario]:
    path = os.environ.get(ENV)
    return load(path) if path else None


def apply_to_lab(lab_module) -> Optional[Scenario]:
    """Overrides the numbers of bps.lab with those of the scenario named by BPS_SCENARIO (called when bps.lab is imported)."""
    s = from_env()
    if s is None:
        return None
    if s.cells_enu is not None:
        lab_module.CELLS_ENU = np.array(s.cells_enu, float)
    if s.ue_enu is not None:
        lab_module.UE_ENU = np.array(s.ue_enu, float)
    if s.monitor_noise_ns is not None:
        lab_module.MONITOR_NOISE_NS = float(s.monitor_noise_ns)
    for k, (base, drift) in s.rtd.items():
        lab_module.TRUE_RTD[int(k)] = (float(base), float(drift))
    return s
