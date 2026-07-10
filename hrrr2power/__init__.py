"""hrrr2power: download and extract weather-model data for power applications.

Product-specific logic lives in subpackages (e.g. hrrr2power.subh for HRRR
subhourly data). This top-level package holds only generic helpers shared
across products: yaml config loading (config.py) and nearest-grid-point site
extraction (grid.py).
"""
