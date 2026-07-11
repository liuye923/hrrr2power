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


def find_surrounding_cells(target_lat, target_lon, grid_lats, grid_lons):
    """Return the 2x2 box of grid cells geometrically surrounding
    (target_lat, target_lon), as 4 dicts in the order
    [(y0,x0), (y0,x1), (y1,x0), (y1,x1)] - cell 0 is always the nearest
    point (from find_nearest_grid_point); cells 1-3 fill out whichever
    quadrant the target actually falls in, so the site sits inside the box
    rather than the box always extending in a fixed direction (needed for
    e.g. later bilinear interpolation).

    HRRR's native grid is a Lambert conformal projection, not lat/lon-
    aligned, so "which neighbor is toward the target" can't be read off
    lat/lon signs directly - it's found by projecting the target's offset
    from the nearest point onto the grid's own local x/y direction vectors
    (finite differences to the nearest point's immediate neighbors).
    """
    ny, nx = grid_lats.shape
    y0, x0, _, _, _ = find_nearest_grid_point(target_lat, target_lon, grid_lats, grid_lons)

    # Clipped at the domain edge rather than erroring - degenerates to a
    # duplicated cell there, which is an acceptable fallback since real
    # sites aren't expected right at HRRR's domain boundary.
    x1 = min(x0 + 1, nx - 1)
    xm1 = max(x0 - 1, 0)
    y1 = min(y0 + 1, ny - 1)
    ym1 = max(y0 - 1, 0)

    # HRRR's grid_lons come back in 0-360 convention while site longitudes
    # are typically -180..180 - a raw subtraction near the 0/360 seam (e.g.
    # target=-101.1, grid=258.9) gives a ~360 degree difference instead of
    # the true ~0, which would completely swamp the projection below and
    # pick the wrong quadrant almost everywhere. Wrap every longitude
    # difference to its shortest-path equivalent in (-180, 180] first.
    def lon_diff(a, b):
        return (a - b + 180) % 360 - 180

    dx_vec = np.array([grid_lats[y0, x1] - grid_lats[y0, x0], lon_diff(grid_lons[y0, x1], grid_lons[y0, x0])])
    dy_vec = np.array([grid_lats[y1, x0] - grid_lats[y0, x0], lon_diff(grid_lons[y1, x0], grid_lons[y0, x0])])
    t_vec = np.array([target_lat - grid_lats[y0, x0], lon_diff(target_lon, grid_lons[y0, x0])])

    x_other = x1 if np.dot(t_vec, dx_vec) >= 0 else xm1
    y_other = y1 if np.dot(t_vec, dy_vec) >= 0 else ym1

    cells = []
    for y in (y0, y_other):
        for x in (x0, x_other):
            dlat = np.radians(grid_lats[y, x] - target_lat)
            dlon = np.radians(grid_lons[y, x] - target_lon)
            a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(target_lat)) * \
                np.cos(np.radians(grid_lats[y, x])) * np.sin(dlon / 2) ** 2
            dist_km = 6371 * 2 * np.arcsin(np.sqrt(a))
            cells.append({
                "y_index": int(y), "x_index": int(x),
                "grid_lat": float(grid_lats[y, x]), "grid_lon": float(grid_lons[y, x]),
                "distance_km": float(dist_km),
            })
    return cells


def generate_grid_indices(
    sites: pd.DataFrame, sample_grib: Path, backend_kwargs: dict | None = None, num_cells: int = 1
) -> pd.DataFrame:
    """Compute nearest grid (x_index, y_index) for each site from any single grib2 file.

    backend_kwargs is passed through to cfgrib.open_datasets - pass whatever
    decode options your product needs (e.g. hrrr2power.subh.extract.CFGRIB_KWARGS).
    Any file for the product works as the grid source, since every message
    from a given model/domain shares the same native grid regardless of
    variable.

    num_cells=1 (default) returns one row per site (unchanged from before -
    the nearest grid cell only). num_cells=4 returns 4 rows per site (an
    added "cell" column, 0-3) - the 2x2 box of grid cells surrounding the
    site, see find_surrounding_cells. No other num_cells value is supported.
    """
    if num_cells not in (1, 4):
        raise ValueError(f"num_cells must be 1 or 4, got {num_cells}")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = cfgrib.open_datasets(str(sample_grib), backend_kwargs=backend_kwargs or {})[0]

    grid_lats = ds["latitude"].values
    grid_lons = ds["longitude"].values

    rows = []
    for _, row in sites.iterrows():
        if num_cells == 1:
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
        else:
            for cell_idx, cell in enumerate(
                find_surrounding_cells(row["latitude"], row["longitude"], grid_lats, grid_lons)
            ):
                rows.append({
                    "site_name": row["site_name"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "cell": cell_idx,
                    **cell,
                })
    result = pd.DataFrame(rows)

    # Report distances for the nearest cell only (cell 0) - the other 3
    # cells of a 2x2 box are farther by construction, so folding them into
    # the mean/max would misleadingly inflate this sanity-check stat.
    nearest = result[result["cell"] == 0] if "cell" in result.columns else result
    suffix = f"; {num_cells} cells/site" if num_cells > 1 else ""
    print(f"Grid indices: mean nearest-cell distance {nearest['distance_km'].mean():.2f} km, "
          f"max {nearest['distance_km'].max():.2f} km{suffix}")
    return result
