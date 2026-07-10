"""Nearest-grid-point lookup for extracting site timeseries from any lat/lon grid.

Product-agnostic: works against a single grib2 (or any cfgrib-readable) file
that carries 2D `latitude`/`longitude` coordinates, regardless of which
weather model/product it came from.
"""

import warnings
from pathlib import Path

import cfgrib
import numpy as np
import pandas as pd


def find_nearest_grid_point(target_lat, target_lon, grid_lats, grid_lons):
    """Return (y_idx, x_idx, grid_lat, grid_lon, distance_km) of the closest grid cell."""
    dlat = np.radians(grid_lats - target_lat)
    dlon = np.radians(grid_lons - target_lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(target_lat)) * \
        np.cos(np.radians(grid_lats)) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    distance_km = 6371 * c
    y_idx, x_idx = np.unravel_index(np.argmin(distance_km), distance_km.shape)
    return (
        int(y_idx), int(x_idx),
        float(grid_lats[y_idx, x_idx]), float(grid_lons[y_idx, x_idx]),
        float(distance_km[y_idx, x_idx]),
    )


def generate_grid_indices(
    sites: pd.DataFrame, sample_grib: Path, backend_kwargs: dict | None = None
) -> pd.DataFrame:
    """Compute nearest grid (x_index, y_index) for each site from any single grib2 file.

    backend_kwargs is passed through to cfgrib.open_datasets - pass whatever
    decode options your product needs (e.g. hrrr2power.subh.extract.CFGRIB_KWARGS).
    Any file for the product works as the grid source, since every message
    from a given model/domain shares the same native grid regardless of
    variable.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = cfgrib.open_datasets(str(sample_grib), backend_kwargs=backend_kwargs or {})[0]

    grid_lats = ds["latitude"].values
    grid_lons = ds["longitude"].values

    rows = []
    for _, row in sites.iterrows():
        y_idx, x_idx, glat, glon, dist_km = find_nearest_grid_point(
            row["latitude"], row["longitude"], grid_lats, grid_lons
        )
        rows.append({
            "site_name": row["site_name"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "grid_lat": glat,
            "grid_lon": glon,
            "x_index": x_idx,
            "y_index": y_idx,
            "distance_km": dist_km,
        })
    result = pd.DataFrame(rows)
    print(f"Grid indices: mean distance {result['distance_km'].mean():.2f} km, "
          f"max {result['distance_km'].max():.2f} km")
    return result
