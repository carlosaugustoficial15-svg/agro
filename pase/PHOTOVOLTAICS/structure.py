from abc import ABC, abstractmethod
import logging
import math
import numpy as np
import pyvista as pyv
from pase.pase_math import rotate_geometry, translate_geometry

pyv.global_theme.allow_empty_mesh = True

logger = logging.getLogger(__name__)

def build_structure(config_dict):
    """
    Factory that selects the appropriate PV structure from the configuration.
    """
    struct_type = (config_dict.get('StructureType')
                   or config_dict.get('Structype'))
    if not struct_type:
        return None

    struct_type = struct_type.lower()

    if struct_type == 'agrivoltaic fence':
        return AgrivoltaicFence(config_dict).build_structure()
    if struct_type == 'pv table':
        return PVTable(config_dict).build_structure()
    if struct_type == 'hsats':
        return HSATS(config_dict).build_structure()

    raise ValueError(f"Unsupported StructureType '{struct_type}'")


def compute_flush_panel_offset(config: dict, thickness: float) -> float:
    """Compute the panel z-offset that places the panel back face flush on the purlin.

    For center-hinge structures (PV Table, HSATS), the panel must be offset by the
    rafter characteristic half-dimension, the full purlin dimension (two half-thicknesses
    spanning the purlin cross-section), and half the panel thickness so the back face
    is coincident with the purlin top face:

        panel_offset = dim_rafter + 2 * dim_purlin + thickness / 2

    Each dimension is determined by the cross-section shape, following the same
    logic as ``PVStructure.get_characteristic_dim``.

    Returns 0.0 when no structure type is defined or for Agrivoltaic Fence, where
    panel positioning uses a top-hinge style and the formula does not apply.

    Parameters
    ----------
    config : dict
        Aggregated input parameters, including ``StructureType``, ``RafterShape``,
        ``PurlinShape`` and their associated dimension keys.
    thickness : float
        Panel thickness in meters (0.0 for 2D panels).

    Returns
    -------
    float
        Panel offset in meters along the panel normal.
    """
    struct_type = (config.get('StructureType') or '').lower()
    if struct_type in ('', 'agrivoltaic fence'):
        return 0.0

    def _char_dim(part: str) -> float:
        shape = (config.get(f'{part}Shape') or '').lower()
        if shape == 'cylinder':
            return float(config.get(f'{part}Radius', 0.0))
        elif shape == 'rectangle':
            return float(config.get(f'{part}Height', 0.0)) / 2.0
        elif shape == 'square':
            return float(config.get(f'{part}Side', 0.0)) / 2.0
        return 0.0

    return _char_dim('Rafter') + 2.0 * _char_dim('Purlin') + thickness / 2.0


def load_params(data: dict, required: list, optional: dict = None) -> dict:
    """Validate required keys and merge optional defaults into data.

    Parameters
    ----------
    data : dict
        Raw parameter dictionary (e.g. from YAML inputs).
    required : list[str]
        Keys that must be present in *data*. Raises ``ValueError`` if any
        are missing.
    optional : dict, optional
        Mapping of key → default value for parameters that may be absent
        from *data*.  Defaults in *optional* are overridden by values
        present in *data*.

    Returns
    -------
    dict
        Merged dictionary with all optional defaults filled in.
    """
    if optional is None:
        optional = {}
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"Missing required structure parameters: {missing}")
    return {**optional, **data}


class PVStructurePart(ABC):
    """Abstract interface for PV structures (panels, poles, trackers, etc.)."""

    def __init__(self, shape_type, length, **kwargs):
        """Store profile geometry then build the associated polydata mesh."""
        self.shape_type = shape_type  # {circle, square, rectangle}

        self.length = length

        self.parse_kwargs(kwargs)

        self.make_polydata()

    def parse_kwargs(self, kwargs):
        """Extract optional geometric dimensions and ground positioning."""
        keys = kwargs.keys()

        if 'radius' in keys:
            self.radius = kwargs['radius']

        if 'side' in keys:
            self.side = kwargs['side']

        if 'width' in keys:
            self.width = kwargs['width']
            self.height = kwargs['height']

        if 'positioning' in keys:
            self.pole_ground_positioning = kwargs['positioning']
        else:
            self.pole_ground_positioning = 0

    def make_polydata(self):
        """
        Create a vertical polydata of the requested shape; subclasses rotate it
        """
        if self.length < 1e-6:
            self.polydata = pyv.PolyData()
        else:
            if self.shape_type.lower() in ['circle', 'cylinder']:
                # Build the cylinder as a triangle mesh. VTK's CylinderSource
                # crashes with the bundled Windows runtime on some machines.
                segments = 32
                angles = np.linspace(0, 2 * np.pi, segments, endpoint=False)
                radius = float(self.radius)
                half = float(self.length) / 2
                ring_x, ring_y = radius * np.cos(angles), radius * np.sin(angles)
                points = np.column_stack((
                    np.concatenate((ring_x, ring_x, [0.0, 0.0])),
                    np.concatenate((ring_y, ring_y, [0.0, 0.0])),
                    np.concatenate((np.full(segments, -half), np.full(segments, half), [-half, half])),
                ))
                bottom_center, top_center = 2 * segments, 2 * segments + 1
                faces = []
                for idx in range(segments):
                    nxt = (idx + 1) % segments
                    bottom_i, bottom_n = idx, nxt
                    top_i, top_n = segments + idx, segments + nxt
                    faces.extend((3, bottom_i, bottom_n, top_n, 3, bottom_i, top_n, top_i))
                    faces.extend((3, top_center, top_i, top_n, 3, bottom_center, bottom_n, bottom_i))
                self.polydata = pyv.PolyData(points, np.asarray(faces, dtype=np.int64))
            elif self.shape_type.lower() == 'square':
                half_side, half_length = float(self.side) / 2, float(self.length) / 2
                self.polydata = pyv.Box(bounds=(-half_side, half_side, -half_side, half_side, -half_length, half_length)).triangulate()
            elif self.shape_type.lower() == 'rectangle':
                half_width, half_height, half_length = float(self.width) / 2, float(self.height) / 2, float(self.length) / 2
                self.polydata = pyv.Box(bounds=(-half_width, half_width, -half_height, half_height, -half_length, half_length)).triangulate()


class Pole(PVStructurePart):
    """Vertical post used as the main support for the PV structure."""

    def __init__(self, shape_type, length, **kwargs):
        """
        Create a vertical pole of shape shape_type and given length in meters.
        Everything is handled by the parent class PVStructurePart.

        :param shape_type: shape of the profile, choices :
                           {'circle', 'cylinder', 'square', 'rectangle'}
        :param length: length in meters [m]
        :param kwargs: contains parameters of the profile : radius if it's a
                       cylinder, width and height if rectangle, side if square
        """
        super().__init__(shape_type, length, **kwargs)

        self.orientation = 'vertical'
        translate_geometry(self.polydata, (0, 0, length/2 + self.pole_ground_positioning))


class Purlin(PVStructurePart):
    """
    Horizontal purlins linking posts and supporting the panels.
    """

    def __init__(self, shape_type, length, panel_tilt_y, **kwargs):
        super().__init__(shape_type, length, **kwargs)

        # Rotate to make horizontal along y
        self.orientation = 'horizontal_y'
        rotate_geometry(self.polydata, "x", 90)

        # Tilt the purlin
        self.tilt = panel_tilt_y  # [°]
        rotate_geometry(self.polydata, "y", self.tilt)


class Rafter(PVStructurePart):
    """Rafters oriented along the X-axis to carry purlins or panels."""

    def __init__(self, shape_type, length, panel_tilt_y, **kwargs):
        super().__init__(shape_type, length, **kwargs)

        # Rotate to make horizontal along x
        self.orientation = 'horizontal_x'
        rotate_geometry(self.polydata, "z", 90)
        rotate_geometry(self.polydata, "y", 90)

        # Tilt the rafter
        self.tilt = panel_tilt_y  # [°]
        rotate_geometry(self.polydata, "y", self.tilt)


class HorizontalBar(PVStructurePart):
    """Simple horizontal bar, used for fence-like structures."""

    def __init__(self, shape_type, length, **kwargs):
        super().__init__(shape_type, length, **kwargs)

        # Rotate to make horizontal along y
        self.orientation = 'horizontal_y'
        rotate_geometry(self.polydata, "x", 90)


class Diagonal(PVStructurePart):
    """Diagonal bracing connecting two posts to stiffen the group."""

    def __init__(self, shape_type, length, **kwargs):
        super().__init__(shape_type, length, **kwargs)

        self.orientation = 'horizontal_x'

# --- Structures ---

_BASE_REQUIRED: list = [
    'NumberOfStructureGroups', 'RepetitionDistanceGroupYMode',
    'PoleShape', 'PoleRadius', 'PoleGroundPositioning',
    'PurlinShape', 'PurlinRadius',
    'Material',
    'PanelDimensionX', 'PanelDimensionY',
    'RepetitionDistanceOfPanelsX', 'RepetitionDistanceOfPanelsY',
    'NumberOfPanelsX', 'NumberOfPanelsY',
    'RepetitionDistanceOfPVBlocksX', 'RepetitionDistanceOfPVBlocksY',
    'NumberOfPVBlocksX', 'NumberOfPVBlocksY',
    'Height',
]


class PVStructure(ABC):
    """Abstract interface describing the common parameters of PV structures."""
    def __init__(self, PV_i):
        """Load common geometric, spacing, and material parameters for the PV layout."""
        params = load_params(PV_i, self.REQUIRED, self.OPTIONAL)

        self.number_of_structure_groups = params['NumberOfStructureGroups']
        self.vertical_spacing           = params['RepetitionDistanceOfPanelsX']

        self.pole_shape              = params['PoleShape']
        self.pole_width              = params['PoleWidth']
        self.pole_height             = params['PoleHeight']
        self.pole_side               = params['PoleSide']
        self.pole_radius             = params['PoleRadius']
        self.pole_ground_positioning = params['PoleGroundPositioning']

        self.purlin_shape      = params['PurlinShape']
        self.purlin_width      = params['PurlinWidth']
        self.purlin_height     = params['PurlinHeight']
        self.purlin_side       = params['PurlinSide']
        self.purlin_radius     = params['PurlinRadius']
        self.numbers_of_purlin = params['NumberOfPurlins']

        self.rafter_shape      = params['RafterShape']
        self.rafter_width      = params['RafterWidth']
        self.rafter_height     = params['RafterHeight']
        self.rafter_side       = params['RafterSide']
        self.rafter_length     = params['RafterLength']
        self.rafter_radius     = params['RafterRadius']
        self.numbers_of_rafter = params['NumberOfRafters']

        self.diagonal_shape  = params['DiagonalShape']
        self.diagonal_width  = params['DiagonalWidth']
        self.diagonal_height = params['DiagonalHeight']
        self.diagonal_side   = params['DiagonalSide']
        self.diagonal_radius = params['DiagonalRadius']

        self.material = params['Material']

        self.panel_height = float(params['PanelDimensionX'])  # X
        self.panel_width  = float(params['PanelDimensionY'])  # Y

        self.panel_spacing_x    = float(params['RepetitionDistanceOfPanelsX'])  # pitch X
        self.panel_spacing_y    = float(params['RepetitionDistanceOfPanelsY'])  # pitch Y
        self.panels_per_block_x = int(params['NumberOfPanelsX'])   # per block
        self.panels_per_block_y = int(params['NumberOfPanelsY'])   # per block

        self.block_spacing_x = float(params['RepetitionDistanceOfPVBlocksX'])  # block pitch X
        self.block_spacing_y = float(params['RepetitionDistanceOfPVBlocksY'])  # block pitch Y
        self.num_blocks_x    = int(params['NumberOfPVBlocksX'])  # blocks
        self.num_blocks_y    = int(params['NumberOfPVBlocksY'])  # blocks

        self.base_height      = float(params['Height'])
        self.pole_spacing     = float(params['PoleSpacingX'])
        self.tilt             = float(params['TiltY'])
        self.diagonal_epsilon           = float(params['DiagonalEpsilon'])
        self.diagonal_ground_guard  = float(params['DiagonalGroundGuard'])

        self.repetition_distance_group_Y_mode = params['RepetitionDistanceGroupYMode']

        if self.repetition_distance_group_Y_mode.lower() == 'auto':
            # Auto compute repetition_distance_group_Y based on panel layout
            self.repetition_distance_group_Y = (
                self.panels_per_block_y * self.panel_spacing_y
                / self.number_of_structure_groups
            )
        elif self.repetition_distance_group_Y_mode.lower() == 'manual':
            if params['RepetitionDistanceGroupY'] is None:
                raise ValueError(
                    "RepetitionDistanceGroupY is required when "
                    "RepetitionDistanceGroupYMode is 'manual'."
                )
            # Read repetition_distance_group_Y as a parameter
            self.repetition_distance_group_Y = params['RepetitionDistanceGroupY']

        self.purlin_length = self.repetition_distance_group_Y


    def make_elementary_group(self):
        """
        Abstract method overridden in inherited classes.
        """
        pass

    def build_structure(self):
        """
        Abstract method overridden in inherited classes.
        """
        pass
    
    def get_characteristic_dim(self, part_type):
        """
        Get the characteristic dimension of a part of the structure, used to place
        parts on top of each other without clipping.

        :param part_type: type of part (should be "purlin" or "rafter")
        :type part_type: string
        :return: radius (if the part is a cylinder), half height (if the part has a
            rectangular section), half side (if the part has a square section)
        :rtype: float
        """
        shape = getattr(self, f"{part_type}_shape").lower()
        
        mapping = {
            'cylinder': getattr(self, f"{part_type}_radius"),
            'rectangle': getattr(self, f"{part_type}_height")/2,
            'square': getattr(self, f"{part_type}_side")/2
        }

        return mapping.get(shape, 0)

    def make_structure_part_group(self, part_group, nb_part, span):
        """
        Create and position a group of purlins or rafters across a span.
        part_group define if you want generate a purlin or rafter group.
        """

        # Short-circuit when there is nothing to build.
        if nb_part <= 0:
            return pyv.PolyData()

        # check if you want a purlin group or a rafter group
        if part_group == "purlin":
            # Compute evenly spaced X offsets centered on the span.
            if nb_part == 1:
                offsets_x = [0.0]
            else:
                start = -span / 2.0
                step = span / (nb_part - 1)
                offsets_x = [start + i * step for i in range(nb_part)]

            # Instantiate and place each purlin and translate it with offset value.
            purlin_group = []
            for offx in offsets_x:
                p = Purlin(self.purlin_shape,
                           length=self.purlin_length,
                           panel_tilt_y=0,
                           side=self.purlin_side,
                           radius=self.purlin_radius,
                           width=self.purlin_width,
                           height=self.purlin_height,
                           positioning=self.pole_ground_positioning)

                dim_purlin = self.get_characteristic_dim("purlin")
                if self.rafter_length == 0:
                    dim_rafter = 0
                else:
                    dim_rafter = self.get_characteristic_dim("rafter")
                translate_geometry(p.polydata, (offx, 0, self.base_height + dim_purlin + dim_rafter))
                purlin_group.append(p.polydata)

            # Merge meshes into a single polydata group for apply transform to the group. 
            # Need to precise the group height who's defined by the base_height.
            combined = purlin_group[0].copy()
            for mesh in purlin_group[1:]:
                combined = combined + mesh

            # Apply the table tilt around the new center of the group and the purlin offset.
            rotate_geometry(combined, "y", self.tilt, (0, 0, self.base_height))
        # Same idea for the rafter
        elif part_group == "rafter":
            if nb_part == 1:
                offsets_y = [0.0] # instead of x axis the rafter need a translate offset on y axis
            else:
                start = -span / 2.0
                step = span / (nb_part - 1)
                offsets_y = [start + i * step for i in range(nb_part)]

            rafter_group = []
            for offy in offsets_y:
                p = Rafter(self.rafter_shape, length=self.rafter_length,
                           panel_tilt_y=0, side=self.rafter_side,
                           radius=self.rafter_radius, width=self.rafter_width,
                           height=self.rafter_height,
                           positioning=self.pole_ground_positioning)
                translate_geometry(p.polydata, (0, offy, self.base_height))
                rafter_group.append(p.polydata)

            combined = rafter_group[0].copy()
            for mesh in rafter_group[1:]:
                combined = combined + mesh

            rotate_geometry(combined, "y", self.tilt, (0, 0, self.base_height))
        
        return combined
    
    def make_diagonal(self):
        """
        Build a diagonal brace connecting the two poles with the correct slope.
        """

        left_pole_height = self.base_height + self.height_offset
        right_pole_height = self.base_height - self.height_offset

        if left_pole_height <= right_pole_height:
            high_x = -self.half_span
            high_z = left_pole_height
            low_x = self.half_span
        else:
            high_x = self.half_span
            high_z = right_pole_height
            low_x = -self.half_span

        low_z = min(self.diagonal_ground_guard, high_z - self.diagonal_epsilon)

        vertical_span = high_z - low_z
        horizontal_span = abs(high_x - low_x)
        diagonal_length = math.hypot(horizontal_span, vertical_span)

        diagonal = Diagonal(self.diagonal_shape,
                            length=diagonal_length,
                            width=self.diagonal_width,
                            height=self.diagonal_height,
                            radius=self.diagonal_radius,
                            side=self.diagonal_side,
                            positioning=self.pole_ground_positioning)

        angle = math.degrees(math.atan2(horizontal_span, vertical_span))
        if high_x < low_x:
            angle = -angle

        rotate_geometry(diagonal.polydata, "y", angle)

        center = ((high_x + low_x) / 2.0,
                  -self.purlin_length / 2.0,
                  (high_z + low_z) / 2.0)
        translate_geometry(diagonal.polydata, center)

        return diagonal.polydata


class AgrivoltaicFence(PVStructure):
    """Agrivoltaic fence structure composed of posts and horizontal bars."""

    REQUIRED: list = _BASE_REQUIRED
    OPTIONAL: dict = {
        'PoleWidth': 0.0, 'PoleHeight': 0.0, 'PoleSide': 0.0,
        'PurlinWidth': 0.0, 'PurlinHeight': 0.0, 'PurlinSide': 0.0,
        'NumberOfPurlins': 0,
        # Rafter params unused by fences
        'RafterShape': 'cylinder',
        'RafterWidth': 0.0, 'RafterHeight': 0.0, 'RafterSide': 0.0,
        'RafterLength': 0.0, 'RafterRadius': 0.0, 'NumberOfRafters': 0,
        # Diagonal params unused by fences
        'DiagonalShape': 'cylinder',
        'DiagonalWidth': 0.0, 'DiagonalHeight': 0.0, 'DiagonalSide': 0.0,
        'DiagonalRadius': 0.0, 'DiagonalEpsilon': 1e-6, 'DiagonalGroundGuard': 0.0,
        # Tilt / span not relevant for vertical fence
        'PoleSpacingX': 0.0, 'TiltY': 0.0,
        'RepetitionDistanceGroupY': None,
    }

    def __init__(self, PV_i, **kwargs):
        """Initialize agrivoltaic fence parameters from aggregated PV inputs."""
        super().__init__(PV_i, **kwargs)

        # Derive horizontal bar positions from Height (center of panel group),
        # analogous to how PVTable derives pole heights from tilt geometry.
        panel_span_x = (self.panels_per_block_x - 1) * self.panel_spacing_x + self.panel_height
        self.top_bar_height = self.base_height + panel_span_x / 2
        # self.bottom_bar_offset = panel_span_x/2
        self.bottom_bar_offset = panel_span_x - self.panel_height

        if self.top_bar_height - panel_span_x < self.pole_ground_positioning:
            raise ValueError(
                f"Invalid AgrivoltaicFence configuration: The panel group bottom "
                f"({self.top_bar_height - panel_span_x:.2f} m) is at or below ground level "
                f"({self.pole_ground_positioning:.2f} m). "
                f"Increase 'Height' or reduce 'NumberOfPanelsX' / "
                f"'RepetitionDistanceOfPanelsX'."
            )

        panel_span_y = ((self.panels_per_block_y - 1) * self.panel_spacing_y
                        + self.panel_width)
        structure_span_y = self.number_of_structure_groups * self.repetition_distance_group_Y
        if panel_span_y > structure_span_y:
            raise ValueError(
                f"Invalid AgrivoltaicFence configuration: The total panel width in Y "
                f"({panel_span_y:.2f}m) exceeds the structural span in Y "
                f"({structure_span_y:.2f}m). "
                f"Please change the panels configuration on Y axis or adjust "
                f"'NumberOfStructureGroups' or 'RepetitionDistanceGroupY'."
            )

    def make_elementary_group(self) -> pyv.PolyData:
        """Create a fence group by combining one post and two horizontal bars."""

        pole = Pole(self.pole_shape,
                    length=self.top_bar_height - self.pole_ground_positioning,
                    width=self.pole_width,
                    height=self.pole_height,
                    side=self.pole_side,
                    radius=self.pole_radius,
                    positioning=self.pole_ground_positioning)
        translate_geometry(pole.polydata, (0, -self.purlin_length/2, 0))

        horizontal_bar_top = HorizontalBar(self.purlin_shape,
                                           length=self.purlin_length,
                                           side=self.purlin_side,
                                           width=self.purlin_width,
                                           height=self.purlin_height,
                                           radius=self.purlin_radius)

        _fine_positioning_offset = self.get_characteristic_dim("purlin")  # [m] vertical offset to avoid clipping between horizontal bars and PV modules
        translate_geometry(horizontal_bar_top.polydata, (0, 0, self.top_bar_height))

        # 2nd horizontal bar is a copy of horizontal_bar_top,
        # translated downwards to the middle of the panel group (in the group's X axis)
        horizontal_bar_bottom = (horizontal_bar_top.polydata
                                 .copy().
                                 translate((0, 0, -self.bottom_bar_offset),
                                           inplace=True))

        # Fine positioning of both horizontal bars
        translate_geometry(horizontal_bar_top.polydata, (0, 0, _fine_positioning_offset))
        translate_geometry(horizontal_bar_bottom, (0, 0, _fine_positioning_offset))


        combined = (pole.polydata
                    + horizontal_bar_top.polydata
                    + horizontal_bar_bottom).triangulate()

        return combined

    def build_structure(self) -> pyv.MultiBlock:
        """
        Assemble all fence groups along Y and add a terminal post.
        """
        half_span = (self.number_of_structure_groups - 1) * self.repetition_distance_group_Y / 2
        group_y_offsets = [
            -half_span + idx * self.repetition_distance_group_Y
            for idx in range(self.number_of_structure_groups)
        ]

        blocks = pyv.MultiBlock()

        for offy in group_y_offsets:
            g = self.make_elementary_group()
            translate_geometry(g, (0, offy, 0))
            blocks.append(g)

        end_pole = Pole(self.pole_shape,
                        length=self.top_bar_height - self.pole_ground_positioning,
                        width=self.pole_width,
                        height=self.pole_height,
                        side=self.pole_side,
                        radius=self.pole_radius,
                        positioning=self.pole_ground_positioning)

        translate_geometry(end_pole.polydata, (0, group_y_offsets[-1] + self.repetition_distance_group_Y / 2, 0.0))
        blocks.append(end_pole.polydata)
        combined_blocks = blocks.combine()
        combined_blocks.user_dict = {'Material': self.material}

        return combined_blocks

class PVTable(PVStructure):
    """
    Fixed tilted table with posts, rafters, and diagonal bracing.
    """

    REQUIRED: list = _BASE_REQUIRED + ['PoleSpacingX', 'TiltY', 'DiagonalEpsilon', 'DiagonalGroundGuard']
    OPTIONAL: dict = {
        'PoleWidth': 0.0, 'PoleHeight': 0.0, 'PoleSide': 0.0,
        'PurlinWidth': 0.0, 'PurlinHeight': 0.0, 'PurlinSide': 0.0,
        'NumberOfPurlins': 2,
        'RafterShape': 'cylinder',
        'RafterWidth': 0.0, 'RafterHeight': 0.0, 'RafterSide': 0.0,
        'RafterLength': 0.0, 'RafterRadius': 0.0, 'NumberOfRafters': 0,
        'DiagonalShape': 'cylinder',
        'DiagonalWidth': 0.0, 'DiagonalHeight': 0.0, 'DiagonalSide': 0.0,
        'DiagonalRadius': 0.0,
        'RepetitionDistanceGroupY': None,
    }

    def __init__(self, PV_i, **kwargs):
        """
        Derive span, tilt, and minimum rafter length for the table geometry.
        """
        super().__init__(PV_i, **kwargs)

        self.tilt_rad = math.radians(self.tilt)
        self.half_span = self.pole_spacing/2

        self.required_length = 2 * self.half_span / math.cos(self.tilt_rad) 
        self.rafter_length = max(self.rafter_length, 
                                 self.required_length)

        panel_span_x = ((self.panels_per_block_x - 1) * self.panel_spacing_x
                        + self.panel_height)
        if panel_span_x > self.rafter_length + 2 * self.panel_height:
            raise ValueError(
                f"Invalid PVTable configuration: The total panel height in X "
                f"({panel_span_x:.2f}m) exceeds the rafter length "
                f"({self.rafter_length:.2f}m). "
                f"Please change the panels configuration on X axis or increase the rafter length by increasing 'PoleSpacingX' or 'RafterLength'."
            )

        panel_span_y = ((self.panels_per_block_y - 1) * self.panel_spacing_y
                        + self.panel_width)
        structure_span_y = self.number_of_structure_groups * self.purlin_length
        if panel_span_y > structure_span_y:
            raise ValueError(
                f"Invalid PVTable configuration: The total panel width in Y "
                f"({panel_span_y:.2f}m) exceeds the structural span in Y "
                f"({structure_span_y:.2f}m). "
                f"Please change the panels configuration on Y axis or adjust 'NumberOfStructureGroups' or 'RepetitionDistanceOfPanelsY'."
            )

        self.height_offset = self.half_span * math.tan(self.tilt_rad)

        if self.base_height - self.height_offset < 0:
            raise ValueError(f"Invalid PVTable configuration: The tilt angle ({self.tilt}°) "
                             f"is too high for the given base height ({self.base_height}m). "
                             f"This results in the structure extending {abs(self.base_height - self.height_offset):.2f}m "
                             "below ground level. Please increase 'Height' or 'PoleSpacingX' or decrease 'TiltY'.")

    def make_elementary_group(self) -> pyv.PolyData:
        """Build one table group with poles, rafters, diagonals, and purlins."""
        pole_and_rafter_group = self.make_start_and_end_block()
        purlin_group = self.make_structure_part_group("purlin",
                                            self.numbers_of_purlin,
                                            self.rafter_length)

        combined = pole_and_rafter_group + purlin_group

        return combined

    def make_start_and_end_block(self) -> pyv.PolyData:
        """
            PR is for Pole and rafter
        """
        pole = Pole(self.pole_shape,
                    length=(self.base_height + self.height_offset - self.pole_ground_positioning),
                    width=self.pole_width,
                    height=self.pole_height,
                    side=self.pole_side,
                    radius=self.pole_radius,
                    positioning=self.pole_ground_positioning)
        translate_geometry(pole.polydata, (-self.half_span, -self.purlin_length/2, 0))

        pole_2 = Pole(self.pole_shape,
                      length=(self.base_height - self.height_offset - self.pole_ground_positioning),
                      width=self.pole_width,
                      height=self.pole_height,
                      side=self.pole_side,
                      radius=self.pole_radius,
                      positioning=self.pole_ground_positioning)
        translate_geometry(pole_2.polydata, (self.half_span, -self.purlin_length/2, 0))
        
        rafter = Rafter(self.rafter_shape, length=self.rafter_length,
                        panel_tilt_y=self.tilt, radius=self.rafter_radius,
                        side=self.rafter_side, width=self.rafter_width,
                        height=self.rafter_height,
                        positioning=self.pole_ground_positioning)
        translate_geometry(rafter.polydata, (0, -self.purlin_length/2, self.base_height))
        
        diagonal = self.make_diagonal()

        combine = (pole.polydata
                   + pole_2.polydata
                   + rafter.polydata
                   + diagonal)

        return combine

    def build_structure(self) -> pyv.MultiBlock :
        """Replicate groups across the Y grid and cap with an end group."""

        # group positions centered around 0, spaced by repetition_distance_group_Y
        half_span = (self.number_of_structure_groups - 1) * self.repetition_distance_group_Y / 2
        group_y_offsets = [
            -half_span + idx * self.repetition_distance_group_Y
            for idx in range(self.number_of_structure_groups)
        ]

        blocks = pyv.MultiBlock()

        for offy in group_y_offsets:
            g = self.make_elementary_group()
            translate_geometry(g, (0, offy, 0))
            blocks.append(g)

        end_pole = self.make_start_and_end_block()
        translate_geometry(end_pole, (0, offy + self.repetition_distance_group_Y, 0.0))
        
        blocks.append(end_pole)

        combined_blocks = blocks.combine()
        combined_blocks.user_dict = {'Material': self.material}

        return combined_blocks


class HSATS(PVStructure):
    """
    HSATS: horizontal single-axis tracker managing purlins and tilted rafters.
    """

    REQUIRED: list = _BASE_REQUIRED + ['PoleSpacingX', 'TiltY', 'NumberOfRafters']
    OPTIONAL: dict = {
        'PoleWidth': 0.0, 'PoleHeight': 0.0, 'PoleSide': 0.0,
        'PurlinWidth': 0.0, 'PurlinHeight': 0.0, 'PurlinSide': 0.0,
        'NumberOfPurlins': 2,
        'RafterShape': 'cylinder',
        'RafterWidth': 0.0, 'RafterHeight': 0.0, 'RafterSide': 0.0,
        'RafterLength': 0.0, 'RafterRadius': 0.0,
        # Diagonal not used by trackers
        'DiagonalShape': 'cylinder',
        'DiagonalWidth': 0.0, 'DiagonalHeight': 0.0, 'DiagonalSide': 0.0,
        'DiagonalRadius': 0.0, 'DiagonalEpsilon': 1e-6, 'DiagonalGroundGuard': 0.0,
        'RepetitionDistanceGroupY': None,
        'HsatsOverhangTolFactor': 0.1,
    }

    def __init__(self, PV_i, **kwargs):
        """Initialize HSATS with common PV inputs."""
        super().__init__(PV_i, **kwargs)

        panel_span_x = ((self.panels_per_block_x - 1) * self.panel_spacing_x
                        + self.panel_height)
        if panel_span_x > self.rafter_length + 2 *(1 + self.OPTIONAL['HsatsOverhangTolFactor'])*self.panel_height:
            raise ValueError(
                f"Invalid HSATS configuration: The total panel height in X "
                f"({panel_span_x:.2f}m) far exceeds the rafter length "
                f"({self.rafter_length:.2f}m) and produces invalid overhang length. "
                f"Please change the panels configuration on X axis or increase "
                f"the rafter length by increasing 'RafterLength'. The total panel span along X must be at most equal to "
                f"the rafter length + 2 x panel_height. "
            )

        panel_span_y = ((self.panels_per_block_y - 1) * self.panel_spacing_y
                        + self.panel_width)
        structure_span_y = self.number_of_structure_groups * self.repetition_distance_group_Y
        if panel_span_y > structure_span_y:
            raise ValueError(
                f"Invalid HSATS configuration: The total panel width in Y "
                f"({panel_span_y:.2f}m) exceeds the structural span in Y "
                f"({structure_span_y:.2f}m). "
                f"Please change the panels configuration on Y axis or adjust "
                f"'NumberOfStructureGroups' or 'RepetitionDistanceGroupY'."
            )

    def make_elementary_group(self) -> pyv.PolyData:
        """
        Create the tracker group with central post plus purlin and rafter groups.
        """

        pole = Pole(self.pole_shape,
                    length=self.base_height - self.pole_ground_positioning,
                    width=self.pole_width,
                    height=self.pole_height,
                    side=self.pole_side,
                    radius=self.pole_radius,
                    positioning=self.pole_ground_positioning)

        purlin_group = self.make_structure_part_group("purlin", 
                                                      self.numbers_of_purlin,
                                                      self.rafter_length)
        
        rafter_group = self.make_structure_part_group("rafter",
                                                      self.numbers_of_rafter,
                                                      self.purlin_length)

        combine = (pole.polydata + purlin_group + rafter_group)

        return combine
    
    def build_structure(self):
        """Return the assembled HSATS group (single-axis tracker)."""
        half_span = (self.number_of_structure_groups - 1) * self.repetition_distance_group_Y / 2
        group_y_offsets = [
            -half_span + idx * self.repetition_distance_group_Y
            for idx in range(self.number_of_structure_groups)
        ]

        blocks = pyv.MultiBlock()

        for offy in group_y_offsets:
            group = self.make_elementary_group()
            translate_geometry(group, (0.0, offy, 0.0))
            blocks.append(group)

        combined = blocks.combine()
        combined.user_dict = {'Material': self.material}

        return combined


if __name__ == "__main__":
    from pase.DATA_MANAGEMENT.yaml_inputs_provider import (YAML_Inputs_provider,
                                                           Inputs_aggregator)
    import os

    struct_type = "pv_table"  # "HSATS" or "pv_table" or "agrivoltaic_fence"
    panel_orientation = "landscape"  # "landscape" or "portrait"
    display_style = "nice"  # "lean", "nice" or "technical"
    with_axes = True

    if struct_type == "HSATS":
        av_file = "Example4_HSATS.yaml"
        struct_file = "HSATS.yaml"
    elif struct_type.lower() == "pv_table":
        av_file = "Example5_PVTable.yaml"
        struct_file = "PV_table.yaml"
    elif struct_type.lower() == "agrivoltaic_fence":
        av_file = "Example3_AV_agrivoltaic_fence.yaml"
        struct_file = "agrivoltaic_fence.yaml"

    if panel_orientation == "landscape":
        pv_file = "Example1_PV_Module_landscape.yaml"
    elif panel_orientation == "portrait":
        pv_file = "Example1_PV_Module.yaml"


    AV_1 = YAML_Inputs_provider(
        file=av_file,
        subpath="AV_CENTRAL",
        parentdir=2
    ).inputs

    PV_module_1 = YAML_Inputs_provider(
        file=pv_file,
        subpath=os.path.join("HARDWARE", "PV_MODULES"),
        parentdir=2
    ).inputs

    Structure = YAML_Inputs_provider(
        file=struct_file,
        subpath=os.path.join("HARDWARE", "STRUCTURES"),
        parentdir=2
    ).inputs

    PV_params_dict = Inputs_aggregator([AV_1,
                                        PV_module_1,
                                        Structure]).aggregated_inputs

    if struct_type == "HSATS":
        blocks = HSATS(PV_params_dict).build_structure()
    elif struct_type == "pv_table":
        blocks = PVTable(PV_params_dict).build_structure()
    elif struct_type == "agrivoltaic_fence":
        blocks = AgrivoltaicFence(PV_params_dict).build_structure()

    if display_style == "technical":
        show_edges = True
    else:
        show_edges = False

    pl = pyv.Plotter()
    pl.add_mesh(blocks, show_edges=show_edges)
    if display_style == "technical":
        pl.show_axes()
        pl.show_grid(color='gray')
    elif display_style == "nice":
        x_half_span = 5
        y_half_span = 10
        ground = np.array([[-x_half_span, y_half_span, 0],
                           [x_half_span, y_half_span, 0],
                           [-x_half_span, -y_half_span, 0],
                           [x_half_span, -y_half_span, 0]])

        ground_m = np.hstack([[3, 0, 1, 2],
                              [3, 1, 2, 3], ])

        grnd = pyv.PolyData(ground, ground_m)

        pl.add_mesh(grnd, color='green', opacity=0.5)

        light = pyv.Light(intensity=0.2,
                          position=(10, 10, 10))
        # light.set_direction_angle(30, 45)
        pl.add_light(light)

    if display_style != "technical" and with_axes:
        pl.show_axes()

    pl.show()
