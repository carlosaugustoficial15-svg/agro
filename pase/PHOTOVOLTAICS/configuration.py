#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#Copyright (c) 2020-2024 - University of Liège - Digital Energy and Agriculture Lab (DEAL)
#Authors : Roxane Bruhwyler (roxane.bruhwyler@uliege.be or roxane.bruhwyler@hotmail.com) and Nicolas De Cock (nicolas.decock1@gmail.com)
#This file is part of the PASE software, and is distributed under the MIT license.


from __future__ import annotations

import logging
from typing import Iterable, Union, Dict, List, Any, Tuple, Optional
import numpy as np
import pandas as pd
import pyvista as pyv

from pase.DATA_MANAGEMENT.visualization_in_3D import compute_ground_extent
from pase.PHOTOVOLTAICS.structure import build_structure, compute_flush_panel_offset
from pase.pase_math import (compute_panel_grid_positions,
                            compute_block_centers,
                            rotate_about_z, rotate_points, rotate_geometry,
                            translate_geometry)

pyv.global_theme.allow_empty_mesh = True

# ----- Constants -----
EPS = 1e-12
DEFAULT_NORMAL = np.array([0.0, 0.0, 1.0], dtype=float)
DEFAULT_PANEL_THICKNESS = 0.0
DEFAULT_PANEL_Z_DIM = 0.1

logger = logging.getLogger(__name__)


# ----- Utilities -----
def merge_polydata(datasets: List[pyv.PolyData], *, extract_surface: bool = True) -> pyv.PolyData:
    """
    Merge multiple ``pyvista.PolyData`` datasets efficiently.

    Parameters
    ----------
    datasets : list of pyvista.PolyData
        Datasets to merge. Non-PolyData items are ignored.
    extract_surface : bool, default True
        If ``True``, extract the outer surface after merging (useful for
        ray tracing or visualization). If ``False``, return the raw merged
        geometry (which may include volumetric cells).

    Returns
    -------
    pyvista.PolyData
        The merged geometry. If ``datasets`` is empty, returns an empty
        ``pyvista.PolyData``.

    Notes
    -----
    Uses a temporary ``pyvista.MultiBlock`` and ``combine()`` to merge inputs.
    If ``combine()`` returns a non-PolyData dataset, ``extract_geometry()``
    is used before the optional surface extraction.

    Examples
    --------
    >>> merged = merge_polydata([pd1, pd2, pd3], extract_surface=True)
    """
    if not datasets:
        return pyv.PolyData()

    mb = pyv.MultiBlock()
    for d in datasets:
        if isinstance(d, pyv.PolyData):
            mb.append(d)

    merged = mb.combine()
    if isinstance(merged, pyv.PolyData):
        return merged

    geom = merged.extract_geometry()
    return geom.extract_surface() if extract_surface else geom


def name_matches_flag(
    name: Optional[str],
    flags: Sequence[str],
    *,
    exact: bool = False,
    case_sensitive: bool = False
) -> bool:
    """
    Check whether a name matches any of the provided flags.

    Parameters
    ----------
    name : str or None
        Name to test. ``None`` never matches.
    flags : sequence of str
        Candidate flags to match against.
    exact : bool, default False
        If ``True``, require an exact equality; otherwise use substring match.
    case_sensitive : bool, default False
        If ``True``, comparisons are case sensitive; otherwise both the name
        and flags are lowercased prior to comparison.

    Returns
    -------
    bool
        ``True`` if a match is found under the chosen mode, ``False`` otherwise.

    Examples
    --------
    >>> name_matches_flag("PV_12", ["PV"])          # substring
    True
    >>> name_matches_flag("pv_12", ["PV"], case_sensitive=True)
    False
    >>> name_matches_flag("PV", ["PV","INV"], exact=True)
    True
    """
    if name is None:
        return False

    if not case_sensitive:
        name = name.lower()
        flags = [f.lower() for f in flags]

    if exact:
        return name in flags
    return any(flag in name for flag in flags)


def get_block_by_name(mb: pyv.MultiBlock, name: str) -> Optional[pyv.PolyData]:
    """
    Retrieve a child block by its name (O(n)).

    Parameters
    ----------
    mb : pyvista.MultiBlock
        Parent multiblock container.
    name : str
        Name to search for (as returned by ``mb.get_block_name(i)``).

    Returns
    -------
    pyvista.PolyData or None
        The matching block if found and it is a ``PolyData``; otherwise ``None``.

    Notes
    -----
    Iterates linearly over ``mb.n_blocks`` and returns the first name match.
    """
    for i in range(mb.n_blocks):
        if mb.get_block_name(i) == name:
            return mb[i]
    return None


# ----- MultiBlock Extensions -----

class MultiBlockPASE(pyv.MultiBlock):
    """
    PyVista ``MultiBlock`` extension with convenience filters and geometry queries.

    Notes
    -----
    - Treats blocks as PV-like panels by convention but works with any PolyData.
    - Naming-based filters allow selecting subsets without touching underlying data.
    """

    def get_polydata_by_flag(
        self,
        flag: Union[str, Iterable[str]],
        *,
        exact: bool = False,
        case_sensitive: bool = False,
    ) -> "MultiBlockPASE":
        """
        Filter blocks by name using one or multiple flags.

        Parameters
        ----------
        flag : str or Iterable[str]
            Flag(s) to match against block names (e.g., ``"PV"``).
        exact : bool, default False
            If ``True``, require exact equality with the block name; otherwise
            a substring match is used.
        case_sensitive : bool, default False
            If ``True``, comparisons are case sensitive.

        Returns
        -------
        MultiBlockPASE
            A new ``MultiBlockPASE`` containing only matching ``PolyData`` children,
            preserving their original names.

        Examples
        --------
        >>> pv_only = mb.get_polydata_by_flag("PV")
        >>> subset = mb.get_polydata_by_flag(["PV", "INV"], case_sensitive=True)
        """
        flags = [flag] if isinstance(flag, str) else list(flag)
        result = MultiBlockPASE()

        for i in range(self.n_blocks):
            name = self.get_block_name(i)
            if name_matches_flag(name, flags, exact=exact, case_sensitive=case_sensitive):
                child = self[i]
                if isinstance(child, pyv.PolyData):
                    result.append(child, name=name)

        return result

    # ---- Geometry Queries ----

    def get_lowest_corners(self, polydata: pyv.PolyData) -> np.ndarray:
        """
        Return the two lowest (in Z) distinct corners of a rectangular box.

        Parameters
        ----------
        polydata : pyvista.PolyData
            Rectangular parallelepiped (box-like) geometry.

        Returns
        -------
        numpy.ndarray, shape (2, 3)
            Two 3D points at the minimum Z level with different (x, y).

        Raises
        ------
        ValueError
            If the input has no points, if fewer than two lowest points exist,
            or if all lowest points share the same (x, y) (degenerate geometry).

        Notes
        -----
        Points are compared with tolerance ``EPS`` for Z and ``np.allclose`` for (x, y).
        """
        points = np.asarray(polydata.points, dtype=float)
        if points.size == 0:
            raise ValueError("PolyData has no points")

        z = points[:, 2]
        z_min = float(z.min())

        mask_lowest = np.abs(z - z_min) < EPS
        lowest_points = points[mask_lowest]

        if len(lowest_points) < 2:
            raise ValueError("Expected at least 2 points at minimum z for a rectangular box")

        first = lowest_points[0]
        for point in lowest_points[1:]:
            if not np.allclose(point[:2], first[:2], atol=EPS):
                return np.vstack([first, point])

        raise ValueError("All lowest points have same x,y coordinates (degenerate geometry)")

    def get_lowest_corners_multiblock(
        self,
        flag: Union[str, Iterable[str]] = ("PV",)
    ) -> np.ndarray:
        """
        Compute lowest-corner pairs for all blocks matching a flag.

        Parameters
        ----------
        flag : str or Iterable[str], default ("PV",)
            Name filter passed to ``get_polydata_by_flag``.

        Returns
        -------
        numpy.ndarray, shape (N, 2, 3)
            For each matching block, the two lowest corners. If no matches,
            returns an empty array with shape ``(0, 2, 3)``.

        See Also
        --------
        get_lowest_corners : Corner extraction for a single ``PolyData``.
        """
        geometry = self.get_polydata_by_flag(flag)
        corners = [self.get_lowest_corners(g) for g in geometry]
        return np.stack(corners, axis=0) if corners else np.empty((0, 2, 3), float)

    def get_area_multiblock(
        self,
        flag: Union[str, Iterable[str]] = ("PV",)
    ) -> np.ndarray:
        """
        Return surface areas for all blocks matching a flag.

        Parameters
        ----------
        flag : str or Iterable[str], default ("PV",)
            Name filter passed to ``get_polydata_by_flag``.

        Returns
        -------
        numpy.ndarray, shape (N,)
            Areas of each matching ``PolyData`` (as floats). Empty if no matches.
        """
        geometry = self.get_polydata_by_flag(flag)
        return np.array([float(g.area) for g in geometry], dtype=float)

    def get_z_min_multiblock(
        self,
        flag: Union[str, Iterable[str]] = ("PV",)
    ) -> np.ndarray:
        """
        Return minimum Z for all blocks matching a flag.

        Parameters
        ----------
        flag : str or Iterable[str], default ("PV",)
            Name filter passed to ``get_polydata_by_flag``.

        Returns
        -------
        numpy.ndarray, shape (N,)
            Minimum Z (lower bound of ``g.bounds``) per matching dataset.
        """
        geometry = self.get_polydata_by_flag(flag)
        return np.array([float(g.bounds[4]) for g in geometry], dtype=float)

    def get_normal_multiblock(
        self,
        flag: Union[str, Iterable[str]] = ("PV",)
    ) -> np.ndarray:
        """
        Estimate an average outward normal for each matching block.

        Parameters
        ----------
        flag : str or Iterable[str], default ("PV",)
            Name filter passed to ``get_polydata_by_flag``.

        Returns
        -------
        numpy.ndarray, shape (N, 3)
            Unit normals for each matching block. Falls back to
            ``DEFAULT_NORMAL`` when normals cannot be computed.

        Notes
        -----
        - Triangulates the geometry and computes **cell normals** only.
        - Discards invalid/zero-length normals; averages remaining unit normals.
        - Uses ``auto_orient_normals=True`` and ``consistent_normals=True`` for stability.

        Warnings
        --------
        If normal computation fails for a dataset, a warning is logged and
        ``DEFAULT_NORMAL`` is returned for that entry.
        """
        geometry = self.get_polydata_by_flag(flag)
        normals: List[np.ndarray] = []

        for g in geometry:
            try:
                gg = g.triangulate(copy=True).compute_normals(
                    cell_normals=True,
                    point_normals=False,
                    auto_orient_normals=True,
                    consistent_normals=True,
                )

                cell_normals = np.asarray(gg.cell_normals, float)

                if cell_normals.size == 0:
                    normals.append(DEFAULT_NORMAL.copy())
                    continue

                norms = np.linalg.norm(cell_normals, axis=1, keepdims=True)
                valid_mask = (norms[:, 0] > EPS) & np.isfinite(norms[:, 0])

                if not valid_mask.any():
                    normals.append(DEFAULT_NORMAL.copy())
                    continue

                unit_normals = cell_normals[valid_mask] / norms[valid_mask]
                mean_normal = unit_normals.mean(axis=0)
                mean_norm = np.linalg.norm(mean_normal)

                if not np.isfinite(mean_norm) or mean_norm <= EPS:
                    normals.append(DEFAULT_NORMAL.copy())
                else:
                    normals.append((mean_normal / mean_norm).astype(float))

            except Exception as e:
                logger.warning(f"Failed to compute normals for geometry: {e}")
                normals.append(DEFAULT_NORMAL.copy())

        return np.vstack(normals) if normals else np.empty((0, 3), float)


# ----- PV Configuration -----

class PVConfiguration3D(MultiBlockPASE):
    """
    3D PV plant configuration builder.

    Conventions
    -----------
    - Input angles (azimuth, tilt) are in **degrees**.
    - PyVista rotations use degrees internally.
    - Panels are named "PV_<ObjectID>".
    - Azimuth rotates around +Z axis (``rotate_z(-az_deg, ...)``).

    Tracking support
    ----------------
    Provides lazy tracking: you can attach tracker axes/pivots, set angles
    for a time ``t``, and build a merged ``PolyData`` snapshot for ray tracing
    without mutating the base geometry.
    """


    @staticmethod
    def _coerce_thickness(panel_thickness) -> float:
        """
        Normalize 'PanelThickness' to a thickness value in meters.
        Historical behavior:
        - False -> 0.0 (2D panels)
        - True  -> THICKNESS_TRUE_DEFAULT (3D panels with default thickness)
        Also accepts numeric values (float/int).
        """
        # Historical default when PanelThickness is boolean True (meters)
        THICKNESS_TRUE_DEFAULT = 0.10
        import numpy as _np  # local to avoid global dependency here
        if isinstance(panel_thickness, (bool, _np.bool_)) or type(panel_thickness) is bool:
            return float(THICKNESS_TRUE_DEFAULT) if panel_thickness else 0.0
        try:
            t = float(panel_thickness)
        except Exception as e:
            raise TypeError(f"PanelThickness must be bool or float, got {type(panel_thickness).__name__}") from e
        return 0.0 if t < 0 else t

    def __init__(self, **kwargs: Any):
        """
        Initialize an empty PV configuration.

        Parameters
        ----------
        **kwargs : Any
            Forwarded to ``MultiBlockPASE`` constructor.

        Attributes
        ----------
        object_id : int
            Next ObjectID to assign when creating panels.
        central_id : int
            Identifier that increments per generated "central" (grid).
        tracking_state : pandas.DataFrame or None
            Optional tracking state with MultiIndex ``(t, ObjectID) -> angles``.
        df : pandas.DataFrame
            Metadata table indexed by ``ObjectID`` (created in ``_init_dataframe``).
        """
        super().__init__(**kwargs)
        self.object_id: int = 0
        self.central_id: int = 0
        self._name_to_pos: Dict[str, int] = {}
        self._oid_to_pos: Dict[int, int] = {}
        self._poly_cache: Dict[Any, pyv.PolyData] = {}
        self.tracking_state: Optional[pd.DataFrame] = None  # MultiIndex (t, ObjectID) -> angles
        self._init_dataframe()

    # ---- Metadata table ----
    def _init_dataframe(self) -> None:
        """
        Initialize the metadata ``DataFrame`` with standard PV columns.

        Notes
        -----
        The resulting ``self.df`` is indexed by ``ObjectID`` and contains:
        ``Central, Block_X, Block_Y, Module_X, Module_Y, Type, Center, Bounds,
        Area, Azimuth_deg, Tilt_deg, HingePoint, HingeAxis, SecondAxis``.
        """
        self.df = pd.DataFrame(
            columns=[
                "Central",
                "Block_X",
                "Block_Y",
                "Module_X",
                "Module_Y",
                "Type",
                "Center",
                "Bounds",
                "Area",
                "Azimuth_deg",
                "Tilt_deg",
                # Tracking geometry (optional)
                "HingePoint",
                "HingeAxis",
                "SecondAxis",
            ]
        )
        self.df.index.name = "ObjectID"

    # ---- Indexing helpers ----
    def reindex(self) -> None:
        """
        Rebuild internal name and ObjectID position maps.

        Notes
        -----
        Scans all blocks to populate ``_name_to_pos`` and ``_oid_to_pos`` using:
        - block names (``self.get_block_name(i)``), and
        - ``ObjectID`` in block ``field_data``, if present.
        """
        self._name_to_pos.clear()
        self._oid_to_pos.clear()
        for i in range(self.n_blocks):
            name = self.get_block_name(i)
            if name:
                self._name_to_pos[name] = i
            blk = self[i]
            try:
                oid = int(np.asarray(blk.field_data.get("ObjectID"))[0])
                self._oid_to_pos[oid] = i
            except Exception:
                pass

    def add_custom_polydata(self, geometry: pyv.PolyData, info: Dict[str, Any],name = "Custom") -> int:
        """
        Add a custom PolyData to the in-memory registry and append a row to the single DataFrame `self.df`.

        - The `info` dict MUST contain at least the 'Type' key.
        - Other fields must match the existing columns (see df.columns).
          Extra keys are cleanly ignored to preserve a common schema.

        Parameters
        ----------
        geometry : pv.PolyData
            Geometry to register (no transformation applied here). If it is not already a
            PolyData, a best-effort conversion is attempted via
            `extract_surface().triangulate().cast_to_polydata()`.
        info : dict
            Metadata to inject into the DataFrame. Supported fields:
              - Central, Block_X, Block_Y, Module_X, Module_Y, Type (required),
                Azimuth_deg, Tilt_deg, HingePoint, HingeAxis, SecondAxis.

        Returns
        -------
        int
            Newly assigned object_id.
        """

        # Validation/conversion PolyData
        if not isinstance(geometry, pyv.PolyData):
            try:
                geometry = geometry.extract_surface().triangulate()
            except Exception as e:
                raise TypeError("`geometry` is not a pv.PolyData and could not be converted.") from e

        # Dict validation
        if "Type" not in info or not info["Type"]:
            raise ValueError("The dict `info` must contain at minimum the 'Type' key.")

        # New object_id
        oid = self.object_id
        # Deep copy to avoid source modification
        mesh = geometry.copy(deep=True)

        # Computation of the properties
        try:
            cx, cy, cz = map(float, mesh.center)
        except Exception:
            cx = cy = cz = np.nan
        try:
            xmin, xmax, ymin, ymax, zmin, zmax = mesh.bounds
        except Exception:
            xmin = xmax = ymin = ymax = zmin = zmax = np.nan
        area = float(mesh.area) if hasattr(mesh, "area") else np.nan

        # field_data for robust mapping

        mesh.field_data["ObjectID"] = np.array([oid], dtype=np.int64)
        mesh.field_data["Type"] = np.array([1], dtype=np.int32)  # 1 = PV

        pos = len(self)
        self.append(mesh, name=name + str(oid))
        self._name_to_pos[name] = pos
        self._oid_to_pos[oid] = pos
        # Building the dataframe data
        row = {
            "Central": info.get("Central", np.nan),
            "Block_X": info.get("Block_X", np.nan),
            "Block_Y": info.get("Block_Y", np.nan),
            "Module_X": info.get("Module_X", np.nan),
            "Module_Y": info.get("Module_Y", np.nan),
            "Type": info["Type"],
            "Center": (cx, cy, cz),
            "Bounds": (xmin, xmax, ymin, ymax, zmin, zmax),
            "Area": area,
            "Azimuth_deg": info.get("Azimuth_deg", np.nan),
            "Tilt_deg": info.get("Tilt_deg", np.nan),
            # Tracking geometry (optionnel)
            "HingePoint": info.get("HingePoint", np.nan),
            "HingeAxis": info.get("HingeAxis", np.nan),
            "SecondAxis": info.get("SecondAxis", np.nan),
        }

        # Update of the df
        s = pd.Series(row, index=self.df.columns, name=oid)
        self.df.loc[oid] = s
        self.object_id += 1

        return oid

    def get_position_by_oid(self, oid: int) -> Optional[int]:
        """
        Return block position for a given ``ObjectID``.

        Parameters
        ----------
        oid : int
            The panel ObjectID.

        Returns
        -------
        int or None
            The block index in the multiblock, or ``None`` if not found.

        Notes
        -----
        Falls back to scanning all blocks if cached maps miss.
        """
        pos = self._oid_to_pos.get(int(oid))
        if pos is not None:
            return pos
        name = f"PV_{oid}"
        pos = self._name_to_pos.get(name)
        if pos is not None:
            return pos
        # fallback scan
        for i in range(self.n_blocks):
            blk = self[i]
            try:
                if int(np.asarray(blk.field_data.get("ObjectID"))[0]) == oid:
                    return i
            except Exception:
                pass
        return None

    def get_block_by_oid(self, oid: int) -> Optional[pyv.PolyData]:
        """
        Get the ``PolyData`` block associated with an ``ObjectID``.

        Parameters
        ----------
        oid : int
            The panel ObjectID.

        Returns
        -------
        pyvista.PolyData or None
            The corresponding block, or ``None`` if absent.
        """
        pos = self.get_position_by_oid(oid)
        return None if pos is None else self[pos]

    def remove_by_oid(self, oid: int) -> bool:
        """
        Remove a block by ``ObjectID`` and update indices & metadata.

        Parameters
        ----------
        oid : int
            The panel ObjectID to remove.

        Returns
        -------
        bool
            ``True`` if a block was removed, ``False`` otherwise.

        Side Effects
        ------------
        - Rebuilds the multiblock without the removed item.
        - Drops the row from ``self.df`` if present.
        - Calls ``reindex()`` at the end.
        """
        pos = self.get_position_by_oid(oid)
        if pos is None:
            return False
        blocks = []
        names = []
        for i in range(self.n_blocks):
            if i == pos:
                continue
            blocks.append(self[i])
            names.append(self.get_block_name(i))
        self.clear()
        for b, n in zip(blocks, names):
            self.append(b, name=n)
        if oid in self.df.index:
            self.df = self.df.drop(index=oid)
        self.reindex()
        return True

    def assert_consistency(self) -> None:
        """
        Validate consistency between geometry blocks and metadata.

        Raises
        ------
        RuntimeError
            If some ``ObjectID`` in ``self.df`` is missing in blocks, or
            if there are blocks named ``PV_*`` without a matching row in ``self.df``.
        """
        missing = []
        for oid in self.df.index:
            if self.get_position_by_oid(int(oid)) is None:
                missing.append(int(oid))
        orphans = []
        for i in range(self.n_blocks):
            b = self[i]
            name = self.get_block_name(i) or ""
            if name.startswith("PV_"):
                try:
                    oid = int(np.asarray(b.field_data.get("ObjectID"))[0])
                except Exception:
                    oid = None
                if oid is None or oid not in self.df.index:
                    orphans.append((i, name, oid))
        if missing or orphans:
            raise RuntimeError(
                f"Inconsistency: missing_in_blocks={missing}, orphan_blocks={orphans}"
            )

    def visualize_simple(self) -> None:
        """Minimal viewer for all PV centrals."""
        try:
            geom_panels = self.polydata_by_property({'Type': ['PV']}, extract_surface=True)
            geom_struct = self.polydata_by_property({'Type': ['Structure block']}, extract_surface=True)
            geom_diffus = self.polydata_by_property({'Type': ['Diffuser']}, extract_surface=True)
        except Exception as _e:
            logging.getLogger(__name__).warning("Visualization skipped (geometry build failed): %s", _e)
            return

        if not isinstance(geom_panels, pyv.PolyData) or geom_panels.n_points == 0:
            logging.getLogger(__name__).info("Nothing to visualize: empty geometry.")
            return

        pl = pyv.Plotter()
        pl.add_mesh(geom_panels, color='black')

        if geom_struct.n_cells > 0:  # Only add geom_struct if the mesh is not empty
            if geom_struct.user_dict['Material'].lower() == 'metal':
                struct_color = 'grey'
            elif geom_struct.user_dict['Material'].lower() == 'wood':
                struct_color = 'brown'
            else:
                msg = (f'Unrecognized Material value in structure configuration '
                       f'input file : {geom_struct.user_dict["Material"]}')
                raise ValueError(msg)

            pl.add_mesh(geom_struct, color=struct_color)

        x_min, x_max, y_min, y_max = compute_ground_extent(geom_panels)
        ground = np.array([[x_min, y_max, 0],
                           [x_max, y_max, 0],
                           [x_min, y_min, 0],
                           [x_max, y_min, 0]])

        if geom_diffus.n_cells > 0:
            pl.add_mesh(geom_diffus, color='skyblue')

        ground_m = np.hstack([[3, 0, 1, 2],
                              [3, 1, 2, 3], ])

        grnd = pyv.PolyData(ground, ground_m)
        pl.add_mesh(grnd, color='green', opacity=0.5)

        labels = dict(zlabel='Z (ZENITH)', xlabel='X (EAST)',
                      ylabel='Y (NORTH)')
        pl.add_axes(**labels)

        light = pyv.Light()
        light.set_direction_angle(30, 45)

        pl.show_grid()

        pl.show()

    # ---- Block centers ----
    def update_block_centers(self, source: str = "Center") -> None:
        """Compute and attach the geometric center of each PV block.

        Parameters
        ----------
        source : {"HingePoint", "Center"}
            Column containing per-panel 3D points to average. Falls back to the
            other if the requested one is missing.

        Effects
        -------
        Adds/overwrites a column ``BlockCenter`` in ``self.df`` with 3-tuples.
        The same center is replicated on all rows belonging to that block.

        Raises
        ------
        KeyError
            If neither 'HingePoint' nor 'Center' exist in ``self.df``.
        ValueError
            If the selected column does not contain 3D coordinates.
        """
        if self.df is None or self.df.empty:
            return

        # Select source column with fallback
        cols = list(self.df.columns)
        src = source if source in cols else ("HingePoint" if "HingePoint" in cols else ("Center" if "Center" in cols else None))
        if src is None:
            raise KeyError("Neither 'HingePoint' nor 'Center' present in df; cannot compute block centers.")

        # Required grouping keys to define a block
        for key in ("Central", "Block_X", "Block_Y"):
            if key not in cols:
                raise KeyError(f"Missing required column '{key}' to identify blocks.")

        # Convert to numeric 3D arrays
        def _to_vec(v):
            arr = np.asarray(v, dtype=float)
            if arr.shape != (3,) and not (arr.ndim == 1 and arr.size == 3):
                raise ValueError(f"df['{src}'] must contain 3D coordinates; got shape {arr.shape}.")
            return arr.reshape(3,)

        tmp = self.df.copy()
        tmp["_vec"] = tmp[src].apply(_to_vec)

        centers = (
            tmp.groupby(["Central", "Block_X", "Block_Y"])['_vec']
               .apply(lambda a: np.vstack(a).mean(axis=0))
               .rename("_BlockCenter")
               .reset_index()
        )

        self.df = self.df.merge(centers, on=["Central", "Block_X", "Block_Y"], how="left")
        self.df["BlockCenter"] = self.df["_BlockCenter"].apply(lambda v: tuple(map(float, v)))
        self.df.drop(columns=["_BlockCenter"], inplace=True)

    def get_block_centers_dict(self) -> Dict[int, Tuple[float, float, float]]:
        """Return a mapping {ObjectID: BlockCenter}.

        Returns
        -------
        dict
            Keys are row indices (ObjectID), values are (x, y, z) tuples.

        Raises
        ------
        KeyError
            If 'BlockCenter' is not present. Call :meth:`update_block_centers`.
        """
        if "BlockCenter" not in self.df.columns:
            self.update_block_centers()

        # Ensure tuples
        return {int(oid): (float(v[0]), float(v[1]), float(v[2])) for oid, v in self.df["BlockCenter"].items()}


    # ---- Defaults ----
    @staticmethod
    def _apply_defaults(pv_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply default configuration values for optional keys.

        Parameters
        ----------
        pv_config : dict
            Input configuration.

        Returns
        -------
        dict
            A shallow copy of ``pv_config`` with defaults applied:
            - ``PanelThickness`` defaults to ``DEFAULT_PANEL_THICKNESS``.
            - ``MeshConfig`` defaults to ``False``.
        """
        config = dict(pv_config)
        config.setdefault("PanelThickness", DEFAULT_PANEL_THICKNESS)
        config.setdefault("MeshConfig", False)
        config.setdefault("RotationAxisNumber", 0)
        config.setdefault("CentralAzimut", 0)
        struct_type = (config.get("StructureType") or "")
        hinge_default = "top" if struct_type.lower() == "agrivoltaic fence" else "center"
        config.setdefault("Hinge", hinge_default)
        return config

    # ---- Panel primitives ----
    @staticmethod
    def _create_panel_2d(height: float, width: float, z_position: float = 0.0) -> pyv.PolyData:
        """
        Create a triangulated 2D rectangular panel as ``PolyData``.

        Parameters
        ----------
        height : float
            Panel height (X dimension).
        width : float
            Panel width (Y dimension).
        z_position : float, default=0.0
            Z coordinate of the rectangle plane.

        Returns
        -------
        pyvista.PolyData
            Triangulated rectangle mesh.
        """
        rect = pyv.Rectangle(
            [
                [-width / 2, height / 2, z_position],
                [width / 2, height / 2, z_position],
                [-width / 2, -height / 2, z_position],
            ]
        )
        return rect.triangulate()

    @staticmethod
    def _create_panel_3d(height: float, width: float, thickness: float) -> pyv.PolyData:
        """
        Create a triangulated 3D box panel as ``PolyData``.

        Parameters
        ----------
        height : float
            Panel height (X dimension).
        width : float
            Panel width (Y dimension).
        thickness : float
            Panel thickness (Z dimension).

        Returns
        -------
        pyvista.PolyData
            Triangulated box mesh centered at the origin.
        """
        box = pyv.Box(
            bounds=(
                -width / 2,
                width / 2,
                -height / 2,
                height / 2,
                -thickness / 2,
                thickness / 2,
            )
        )
        return box.triangulate()

    # ---- Generation ----
    def _build_panels_for_central(
        self, pv_config: Dict[str, Any]
    ) -> Tuple[List[Tuple[int, pyv.PolyData]], Dict[int, Dict[str, Any]], Dict[str, Any]]:
        """
        Shared panel-creation core for ``create_regular_central`` and
        ``create_tilted_central``.

        Returns
        -------
        pieces : list of (oid, PolyData)
        df_rows : dict mapping oid → row dict
        config : processed config dict (with defaults applied)
        """
        config = self._apply_defaults(pv_config)

        required = [
            "PanelDimensionX", "PanelDimensionY", "PanelThickness",
            "RepetitionDistanceOfPanelsX", "RepetitionDistanceOfPanelsY",
            "NumberOfPanelsX", "NumberOfPanelsY",
            "RepetitionDistanceOfPVBlocksX", "RepetitionDistanceOfPVBlocksY",
            "NumberOfPVBlocksX", "NumberOfPVBlocksY",
            "Height", "CentralAzimut", "TiltY", "Hinge",
        ]
        missing = [k for k in required if k not in config]
        if missing:
            raise ValueError(f"Missing required parameters: {', '.join(missing)}")

        panel_height       = float(config["PanelDimensionX"])
        panel_width        = float(config["PanelDimensionY"])
        thickness          = self._coerce_thickness(config["PanelThickness"])
        panel_spacing_x    = float(config["RepetitionDistanceOfPanelsX"])
        panel_spacing_y    = float(config["RepetitionDistanceOfPanelsY"])
        panels_per_block_x = int(config["NumberOfPanelsX"])
        panels_per_block_y = int(config["NumberOfPanelsY"])
        block_spacing_x    = float(config["RepetitionDistanceOfPVBlocksX"])
        block_spacing_y    = float(config["RepetitionDistanceOfPVBlocksY"])
        num_blocks_x       = int(config["NumberOfPVBlocksX"])
        num_blocks_y       = int(config["NumberOfPVBlocksY"])
        base_height        = float(config["Height"])
        azimuth_deg        = float(config["CentralAzimut"])
        tilt_deg           = float(config["TiltY"])
        hinge_style        = config["Hinge"]
        panel_offset       = compute_flush_panel_offset(config, thickness)

        if any(n < 1 for n in [panels_per_block_x, panels_per_block_y, num_blocks_x, num_blocks_y]):
            logger.warning(
                "Some parameters on number of panels/blocks of panels are "
                "set to 0; there will be no panels in the simulation."
            )
        if any(v <= 0 for v in
               [panel_width, panel_height, panel_spacing_x, panel_spacing_y,
                block_spacing_x, block_spacing_y]):
            raise ValueError("All dimension parameters must be > 0")

        if thickness > 0.0:
            base_panel = self._create_panel_3d(panel_width, panel_height, thickness)
        else:
            base_panel = self._create_panel_2d(panel_width, panel_height, 0.0)

        positions, block_centers, grid_indices = compute_panel_grid_positions(
            num_blocks_x, num_blocks_y,
            panels_per_block_x, panels_per_block_y,
            block_spacing_x, block_spacing_y,
            panel_spacing_x, panel_spacing_y,
            base_height,
        )

        base_area = float(panel_width * panel_height)

        pieces: List[Tuple[int, pyv.PolyData]] = []
        df_rows: Dict[int, Dict[str, Any]] = {}

        for idx in range(positions.shape[0]):
            bx, by, mx, my   = map(int,   grid_indices[idx])
            offx, offy, offz = map(float, positions[idx])
            cx, cy, cz       = map(float, block_centers[idx])

            if hinge_style.lower() == "center":
                panel = base_panel.copy()
                points = np.asarray(base_panel.points, dtype=float) + np.array([offx, offy, offz + panel_offset])
                points = rotate_points(points, "y", tilt_deg, (cx, cy, cz))
                panel.points = rotate_points(points, "z", -azimuth_deg, (0.0, 0.0, 0.0))
            elif hinge_style.lower() == "top":
                panel = base_panel.copy()
                points = np.asarray(base_panel.points, dtype=float) + np.array([offx, offy, offz + panel_offset])
                points = rotate_points(points, "y", 90, (cx, cy, cz))
                center = (points.min(axis=0) + points.max(axis=0)) / 2
                hinge_point = center + np.array([0.0, 0.0, panel_height / 2])
                points = rotate_points(points, "y", 90 - tilt_deg, hinge_point)
                panel.points = rotate_points(points, "z", -azimuth_deg, (0.0, 0.0, 0.0))
            else:
                raise ValueError(f"Unknown Hinge style '{hinge_style}'. Expected 'center' or 'top'.")

            oid = self.object_id
            try:
                panel.field_data["ObjectID"] = np.array([oid], dtype=np.int64)
                panel.field_data["Type"]     = np.array([1],   dtype=np.int32)
            except Exception:
                pass

            if hinge_style.lower() == "top":
                hinge_point = (float(panel.center[0]), float(panel.center[1]),
                               float(panel.center[2]) + panel_height / 2)
            else:
                hinge_point = (float(panel.center[0]), float(panel.center[1]),
                               float(panel.center[2]))

            hinge_axis  = tuple(rotate_about_z((1.0, 0.0, 0.0), -azimuth_deg))
            second_axis = tuple(rotate_about_z((0.0, 1.0, 0.0), -azimuth_deg))

            pieces.append((oid, panel))
            df_rows[oid] = {
                "Central":    self.central_id,
                "Block_X":    bx,
                "Block_Y":    by,
                "Module_X":   mx,
                "Module_Y":   my,
                "Type":       "PV",
                "Center":     tuple(map(float, panel.center)),
                "Bounds":     tuple(map(float, panel.bounds)),
                "Area":       base_area,
                "Azimuth_deg": azimuth_deg,
                "Tilt_deg":   tilt_deg,
                "HingePoint": hinge_point,
                "HingeAxis":  hinge_axis,
                "SecondAxis": second_axis,
            }
            self.object_id += 1

        return pieces, df_rows, config

    def create_regular_central(self, pv_config: Dict[str, Any]) -> None:
        """
        Generate a regular grid (a "central") of PV panels, update geometry and metadata.

        Required Keys (angles in **degrees**)
        ---------------------------------
        PanelDimensionX, PanelDimensionY, PanelThickness,
        RepetitionDistanceOfPanelsX, RepetitionDistanceOfPanelsY,
        NumberOfPanelsX, NumberOfPanelsY,
        RepetitionDistanceOfPVBlocksX, RepetitionDistanceOfPVBlocksY,
        NumberOfPVBlocksX, NumberOfPVBlocksY,
        Height, CentralAzimut, TiltY

        Parameters
        ----------
        pv_config : dict
            Configuration dictionary. Missing optional keys are defaulted by
            ``_apply_defaults``.

        Raises
        ------
        ValueError
            If required parameters are missing, counts < 1, or dimensions <= 0.

        Notes
        -----
        - Angles are provided in **radians** but converted to **degrees** for PyVista.
        - Each created panel receives ``field_data``: ``ObjectID`` (int64) and ``Type=1`` (int32).
        - Appends the created panels to the multiblock, updates ``self.df``,
          and increments ``central_id`` and ``object_id`` accordingly.
        """
        pieces, df_rows, config = self._build_panels_for_central(pv_config)

        # Append to MultiBlock
        start_pos = self.n_blocks
        for k, (oid, panel) in enumerate(pieces):
            name = f"PV_{oid}"
            self.append(panel, name=name)
            self._name_to_pos[name] = start_pos + k
            self._oid_to_pos[oid] = start_pos + k

        # Update DataFrame
        if df_rows:
            df_new = pd.DataFrame.from_dict(df_rows, orient="index")
            df_new.index.name = "ObjectID"
            self.df = pd.concat([self.df, df_new])

        if pv_config.get('DiffusersBetweenPanels'):
            self.add_diffusers_to_central(pv_config)

        only_block_centers = compute_block_centers(
            int(config["NumberOfPVBlocksX"]),              int(config["NumberOfPVBlocksY"]),
            int(config["NumberOfPanelsX"]),                int(config["NumberOfPanelsY"]),
            float(config["RepetitionDistanceOfPVBlocksX"]), float(config["RepetitionDistanceOfPVBlocksY"]),
            float(config["RepetitionDistanceOfPanelsX"]),   float(config["RepetitionDistanceOfPanelsY"]),
            float(config["Height"]),
        )

        self.add_structure(config, only_block_centers)

        # Bump central ID
        self.central_id += 1

    def add_diffusers_to_central(self, pv_config):
        ndiff = 1 if pv_config['DiffusersAtRowEnds'] == True else -1
        diff_dimX, diff_dimY, diff_dimZ = (float(pv_config['DiffuserDimensionX']),
                                           float(pv_config['DiffuserDimensionY']),
                                           float(pv_config['DiffuserDimensionZ']))
        num_blocks_x, num_blocks_y = int(pv_config["NumberOfPVBlocksX"]), int(pv_config["NumberOfPVBlocksY"])
        panel_spacing_x = float(pv_config["RepetitionDistanceOfPanelsX"])  # pitch X
        panel_spacing_y = float(pv_config["RepetitionDistanceOfPanelsY"])  # pitch Y
        panels_per_block_x = int(pv_config["NumberOfPanelsX"])  # per block
        panels_per_block_y = int(pv_config["NumberOfPanelsY"] + ndiff)  # per block

        block_spacing_x = float(pv_config["RepetitionDistanceOfPVBlocksX"])  # block pitch X
        block_spacing_y = float(pv_config["RepetitionDistanceOfPVBlocksY"])  # block pitch Y
        base_height = float(pv_config["Height"])  # elevation
        azimuth_deg = float(pv_config["CentralAzimut"])  # degrees
        tilt_deg = float(pv_config["TiltY"])
        if diff_dimZ > 0.0:
            base_panel = self._create_panel_3d(diff_dimX, diff_dimY, diff_dimZ)
        else:
            base_panel = self._create_panel_2d(diff_dimX, diff_dimY, 0.0)

        positions, block_centers, grid_indices = compute_panel_grid_positions(
            num_blocks_x, num_blocks_y,
            panels_per_block_x, panels_per_block_y,
            block_spacing_x, block_spacing_y,
            panel_spacing_x, panel_spacing_y,
            base_height,
        )
        base_area = diff_dimX*diff_dimY
        N = positions.shape[0] # number of diffusers in the central

        for idx in range(N):
            bx, by, mx, my = map(int, grid_indices[idx])
            offx, offy, offz = map(float, positions[idx])
            cx, cy, cz = map(float, block_centers[idx])

            diffuser = base_panel.copy()
            points = np.asarray(base_panel.points, dtype=float) + np.array([offx, offy, offz])
            points = rotate_points(points, "y", tilt_deg, (cx, cy, cz))
            diffuser.points = rotate_points(points, "z", -azimuth_deg, (0.0, 0.0, 0.0))

            oid = self.object_id
            name = f"Diffuser_{oid}"

            # field_data for robust mapping
            try:
                diffuser.field_data["ObjectID"] = np.array([oid], dtype=np.int64)
                diffuser.field_data["Type"] = np.array([2], dtype=np.int32)  # 1 = PV, 2 = Diffuser
            except Exception:
                pass

            info = {
                "Central": self.central_id,
                "Block_X": bx,
                "Block_Y": by,
                "Module_X": mx,
                "Module_Y": my,
                "Type": "Diffuser",
                "Center": tuple(map(float, diffuser.center)),
                "Bounds": tuple(map(float, diffuser.bounds)),
                "Area": base_area,  # area preserved under rigid transforms
                "Azimuth_deg": azimuth_deg,
                "Tilt_deg": tilt_deg,
                # Default tracking geometry: per-panel center pivot, X then Y axes
                "HingePoint": (float(diffuser.center[0]), float(diffuser.center[1]), float(diffuser.center[2])),
                "HingeAxis": (1.0, 0.0, 0.0),
                "SecondAxis": (0.0, 1.0, 0.0),
            }
            self.add_custom_polydata(diffuser, info, name)

    def add_structure(self, config, block_centers):

        # Instantiate the base block (based on structure type)
        base_struct = build_structure(config)
        if base_struct is None:
            logger.info('No StructureType defined; skipping structure geometry for central %s',
                        self.central_id)
            return

        # How many blocks ?
        num_blocks = len(block_centers)
        num_blocks_x = config['NumberOfPVBlocksX']
        num_blocks_y = config['NumberOfPVBlocksY']

        # Loop over blocks
        block_counter = 0
        for i in range(num_blocks_x):
            for j in range(num_blocks_y):
                # copy and translate the base structure
                struct = base_struct.copy()
                translate_geometry(struct, [block_centers[block_counter, 0], block_centers[block_counter, 1], 0])
                rotate_geometry(struct, "z", -config['CentralAzimut'])
                info_dict = {'Type': 'Structure block',
                             'Central': self.central_id,
                             'Block_X': i,
                             'Block_Y': j,
                             'Azimuth_deg': config['CentralAzimut'],
                             }

                # add to the multiblock instance
                self.add_custom_polydata(geometry=struct,
                                         info=info_dict)

                block_counter += 1

    # ---- Query helpers ----
    def polydata_by_central(self, central_ids: Union[Iterable[int], int], *, extract_surface: bool = True) -> pyv.PolyData:
        """
        Merge and return the geometry for one or more central IDs.

        Parameters
        ----------
        central_ids : Iterable[int] or int
            Central identifier(s) to include.
        extract_surface : bool, default=True
            If ``True``, extract surfaces before merging (useful for ray tracing).

        Returns
        -------
        pyvista.PolyData
            Merged dataset of all panels matching the given central(s).

        Examples
        --------
        >>> pd0 = cfg.polydata_by_central(0)
        >>> pd01 = cfg.polydata_by_central([0, 1])
        """
        if isinstance(central_ids, int):
            centrals = {central_ids}
        else:
            centrals = set(int(c) for c in central_ids)
        idx = self.df.index[self.df["Central"].isin(list(centrals))]
        dsets: List[pyv.PolyData] = []
        for oid in idx:
            blk = self.get_block_by_oid(int(oid))
            if isinstance(blk, pyv.PolyData):
                blk.cell_data['Type'] = np.full(blk.n_cells, self.df['Type'][oid])
                dsets.append(blk)
        return merge_polydata(dsets, extract_surface=extract_surface)

    def polydata_by_property(self, property_dict: Dict[str, Iterable[Any]], *,
                             extract_surface: bool = True) -> pyv.PolyData:
        """
        Merge and return geometry filtered by multiple properties (AND semantics).

        Parameters
        ----------
        property_dict : dict[str, Iterable[Any]]
            Mapping of column name -> allowed values. All conditions must be met.
        extract_surface : bool, default=True
            If ``True``, extract surfaces before merging.

        Returns
        -------
        pyvista.PolyData
            Merged dataset of all panels matching the property filter.

        Raises
        ------
        KeyError
            If a property name is not a column in ``self.df``.

        Examples
        --------
        Select all PV panels in block X ∈ {0, 1}:

        >>> pd = cfg.polydata_by_property({"Block_X": [0, 1], "Type": ["PV"]})
        """
        if not property_dict:
            return pyv.PolyData()
        mask = pd.Series(True, index=self.df.index)
        for prop, values in property_dict.items():
            if prop not in self.df.columns:
                valid = ", ".join(self.df.columns)
                raise KeyError(f"Unknown property: {prop}. Valid: {valid}")
            mask &= self.df[prop].isin(list(values))
        idx = self.df.index[mask]
        dsets: List[pyv.PolyData] = []
        for oid in idx:
            blk = self.get_block_by_oid(int(oid))
            if isinstance(blk, pyv.PolyData):
                dsets.append(blk)
        return merge_polydata(dsets, extract_surface=extract_surface)



    # --- Convenience: all centrals merged ---
    def polydata_all_centrals(self, *, extract_surface: bool = True) -> pyv.PolyData:
        """Merge the geometry of all existing centrals into a single PolyData."""
        if self.df.empty:
            return pyv.PolyData()
        centrals = sorted(set(int(c) for c in self.df["Central"].unique()))
        return self.polydata_by_central(centrals, extract_surface=extract_surface)

# --------- Backward-compatible alias for PASE 1.x ----------
class PV_Configuration_3D(PVConfiguration3D):


    """Backward-compatible alias with legacy signature (params_dict, solar_vector=None, visualization=False)."""
    def __init__( self,
                  params_dict: Optional[Dict[str, Any]] = None,
                  sun_vector: Optional[np.ndarray] = None,
                  visualization: bool = False,
                  **kwargs: Any) -> None:

        super().__init__(**kwargs)
        params_dict = self._apply_defaults(params_dict)

        if params_dict is not None:
            if params_dict["RotationAxisNumber"] == 1:
                params_dict["TiltY"] = 0
            self.create_regular_central(params_dict)

            if params_dict['RotationAxisNumber'] > 0:
                self.PV_central_PD_list = []
                self.get_tiltY_along_time(sun_vector,
                                          params_dict['CentralAzimut'],
                                          (params_dict['PanelDimensionX']*
                                           params_dict['NumberOfPanelsX']/
                                           params_dict['RepetitionDistanceOfPVBlocksX']))

                for tilt in self.tiltY_along_time:
                    params_dict["TiltY"] = tilt
                    PV_central = self.create_tilted_central(params_dict)  # degrees
                    self.PV_central_PD_list.append(PV_central)
        if visualization:
            self.visualize_simple()

    @property
    def PV_central_PD(self) -> pyv.PolyData:
        """
        Merged PolyData of all centrals (as expected by Ray_casting_scene).
        """
        if hasattr(self,"PV_central_PD_list"):
            return self.PV_central_PD_list
        else:
            return self.polydata_all_centrals(extract_surface=True)

    @property
    def PV_central_MB(self) -> 'MultiBlockPASE':
        """MultiBlock subset of PV* blocks, for legacy compatibility."""
        return self.get_polydata_by_flag("PV")

    def create_tilted_central(self, pv_config: Dict[str, Any]) -> pyv.PolyData:
        """
        Generate a regular grid (a "central") of PV panels, update geometry and metadata.

        Required Keys (angles in **degrees**)
        ---------------------------------
        PanelDimensionX, PanelDimensionY, PanelThickness,
        RepetitionDistanceOfPanelsX, RepetitionDistanceOfPanelsY,
        NumberOfPanelsX, NumberOfPanelsY,
        RepetitionDistanceOfPVBlocksX, RepetitionDistanceOfPVBlocksY,
        NumberOfPVBlocksX, NumberOfPVBlocksY,
        Height, CentralAzimut, TiltY

        Parameters
        ----------
        pv_config : dict
            Configuration dictionary. Missing optional keys are defaulted by
            ``_apply_defaults``.

        Raises
        ------
        ValueError
            If required parameters are missing, counts < 1, or dimensions <= 0.

        Notes
        -----
        - Angles are provided in **radians** but converted to **degrees** for PyVista.
        - Each created panel receives ``field_data``: ``ObjectID`` (int64) and ``Type=1`` (int32).
        - Returns the merged panels as a single PolyData (does not update
          the multiblock, df, or central_id — use ``create_regular_central``
          for full integration).
        """
        pieces, _, _ = self._build_panels_for_central(pv_config)
        if not pieces:
            return pyv.PolyData()
        central = pieces[0][1]
        for _, panel in pieces[1:]:
            central += panel
        return central

    def rotation_1st_axis(self, tilt_deg, azimuth_deg, centers=None,
                          return_multiblock=False):
        """
        Rotate each block around Y by `tilt_deg` (degrees).

        - If `centers` is None, uses each panel's geometric center.
        - If `return_multiblock` is False (default), returns a
            merged PolyData (combine()).
          Otherwise returns the rotated MultiBlock.
        """
        # deep copy to avoid mutating the original
        temp = pyv.MultiBlock()
        for i in range(len(self)):
            temp.append(self[i].copy(deep=True) if isinstance(self[i], pyv.PolyData) else self[i])

        centers = self.get_block_centers_dict()
        # rotate block-by-block
        for i in range(0,len(temp)):
            panel = temp[i]
            if not isinstance(panel, pyv.PolyData) or panel.n_points == 0:
                continue
            c = centers[panel.field_data["ObjectID"][0]]

            rotate_geometry(panel, "z", azimuth_deg)
            rotate_geometry(panel, "y", tilt_deg, c)
            rotate_geometry(panel, "z", -azimuth_deg)

        return temp if return_multiblock else merge_polydata(temp)

    def get_tiltY_along_time(self, sun_vect, azimut, GCR_x):

        sun_vect_central_coord = self.get_sun_vect_in_central_coord(sun_vect, azimut)
        true_tracking_angle = self.get_true_tracking_angle(sun_vect_central_coord)
        backT_corr_angle = self.get_backT_corr_angle(true_tracking_angle, GCR_x)
        tiltY_corrected = self.get_corrected_tracking_angle(true_tracking_angle,
                                                             backT_corr_angle)
        tiltY_limited = self.get_limitated_angle(tiltY_corrected)
        self.tiltY_along_time = tiltY_limited*180/np.pi

    def get_sun_vect_in_central_coord(self, sun_vect, azimut):
        # Do not take into account the slope of the area and the slope of the
        # rotation axis (see the previous framework to complete)
        sun_vect_CC = np.zeros((len(sun_vect[:,0]),3))

        sun_vect_CC[:,0] = sun_vect[:,0]*np.cos(azimut)\
            - sun_vect[:,1]*np.sin(azimut)

        sun_vect_CC[:,1] = sun_vect[:,0]*np.sin(azimut)\
            + sun_vect[:,1]*np.cos(azimut)

        sun_vect_CC[:,2] = sun_vect[:,2]

        return sun_vect_CC


    def get_true_tracking_angle(self, sun_v_central_coord):

        true_tracking_angle = np.arctan2(sun_v_central_coord[:,0],
                                         sun_v_central_coord[:,2])

        return true_tracking_angle

    def get_backT_corr_angle(self, true_angle, GCR_x):

        value = np.abs(np.cos(true_angle)/GCR_x)

        backT_corr_angle = np.zeros((len(true_angle)))
        backT_corr_angle[value>=1] = 0
        backT_corr_angle[value<1] = (-np.sign(true_angle[value<1])
                                     *np.arccos((np.abs(np.cos(true_angle[value<1])))/
                                                         GCR_x))

        return backT_corr_angle

    def get_corrected_tracking_angle(self, true_T_angle, backT_corr_angle):

        corrected_tiltY = true_T_angle + backT_corr_angle

        return corrected_tiltY

    def get_limitated_angle(self, tiltY):

        ind = np.where(tiltY>np.pi/3)
        tiltY[ind] = np.pi/3
        ind = np.where(tiltY<-np.pi/3)
        tiltY[ind] = -np.pi/3

        return tiltY
