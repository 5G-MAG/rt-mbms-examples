"""WGS84 geodetic <-> local east-north-up. [design] Standard formulae; no 3GPP clause is involved."""
from __future__ import annotations

import numpy as np

_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)


def lla_to_ecef(lat_deg, lon_deg, h_m):
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    n = _A / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
    return np.array([(n + h_m) * np.cos(lat) * np.cos(lon), (n + h_m) * np.cos(lat) * np.sin(lon), (n * (1 - _E2) + h_m) * np.sin(lat)])


def ecef_to_lla(xyz):
    x, y, z = xyz
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - _E2))
    for _ in range(6):
        n = _A / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - _E2 * n / (n + h)))
    return float(np.degrees(lat)), float(np.degrees(lon)), float(h)


def _rot(lat_deg, lon_deg):
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    return np.array([
        [-np.sin(lon), np.cos(lon), 0],
        [-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)],
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)],
    ])


def lla_to_enu(origin_lla, lla):
    return _rot(origin_lla[0], origin_lla[1]) @ (lla_to_ecef(*lla) - lla_to_ecef(*origin_lla))


def enu_to_lla(origin_lla, enu):
    return ecef_to_lla(lla_to_ecef(*origin_lla) + _rot(origin_lla[0], origin_lla[1]).T @ np.asarray(enu, float))
