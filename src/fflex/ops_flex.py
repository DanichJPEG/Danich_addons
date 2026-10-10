# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""The interactive FFlex operators.

``fflex.flex``       - interactive curve editing (modal)
``fflex.flex_apply`` - one shot fit with the current settings

The imaginary curve always lives inside the *work plane*: the screen plane for
the Screen space, or the plane perpendicular to the selected global / local /
3D cursor axis. The first two in plane coordinates of every vertex follow that
curve, the coordinate along the frame axis is kept (or dropped when
*Project to Plane* is on).
"""

import blf
import bpy
import gpu
import math
import traceback
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator, SpaceView3D
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector, kdtree

from . import compat, core, i18n
from .compat import DRAW_SPACE, VIEW3D_TYPE
from .core import Curve, MeshSession
from .props import AXIS_ITEMS, INTERP_SHAPE_ITEMS, PIVOT_ITEMS, SPACE_ITEMS, get_prefs, get_settings

# events that belong to view navigation / other tools - they are passed through
PASS_THROUGH_KEYS = {
    "MIDDLEMOUSE", "NDOF_MOTION", "NDOF_BUTTON_FIT", "NDOF_BUTTON_MENU",
    "NDOF_BUTTON_PLUS", "NDOF_BUTTON_MINUS", "TRACKPADPAN", "TRACKPADZOOM",
    "MOUSEROTATE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
    "INBETWEEN_MOUSEMOVE", "APP", "OSKEY", "WINDOW_DEACTIVATE",
}


# --------------------------------------------------------------------------- #
# viewport helpers
# --------------------------------------------------------------------------- #

def redraw_view3d():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == VIEW3D_TYPE:
                area.tag_redraw()


_SHADERS = {}

# The symmetry (Mirror) did not behave as expected, so it is switched off in this
# version. The implementation below is kept intact - flip this flag to bring it
# back together with the panel controls and the M / Alt+X/Y/Z keys.
MIRROR_ENABLED = False

# "Smooth Sheet Only" (keeping the interpolation off a thickness) also needs more
# work, so it is hidden for now: the code and the settings are kept for later.
SHEET_ENABLED = False

# The blend between two curves is linear in this version: the quadratic / cubic
# variants and their tension are hidden until the profile can be judged on a real
# model. Flip this flag to bring the shape switch, Shift+I and the E gesture back.
SHAPES_ENABLED = False

# diagnostics of the last HUD draw (region vs window pixel space)
_LAST_DRAW = {}

# module level, so every subclass / reload can clean up the draw handlers
_DRAW_HANDLES = []


def handlers_active():
    return bool(_DRAW_HANDLES)


def remove_draw_handlers():
    while _DRAW_HANDLES:
        handle, space = _DRAW_HANDLES.pop()
        try:
            SpaceView3D.draw_handler_remove(handle, space)
        except Exception:
            pass


def _builtin(names):
    for name in names:
        try:
            return name, gpu.shader.from_builtin(name)
        except Exception:
            continue
    return None, None


def get_shader_set():
    """Lazily created draw shaders; ``None`` when the GPU API is unavailable."""
    if _SHADERS.get("failed"):
        return None
    if not _SHADERS:
        try:
            poly_name, poly = _builtin(("POLYLINE_UNIFORM_COLOR", "UNIFORM_COLOR"))
            point_name, point = _builtin(("POINT_UNIFORM_COLOR", "UNIFORM_COLOR"))
            solid_name, solid = _builtin(("UNIFORM_COLOR", "POLYLINE_UNIFORM_COLOR"))
        except Exception:
            poly = point = solid = None
            poly_name = point_name = solid_name = None
        if poly is None or point is None or solid is None:
            _SHADERS["failed"] = True
            return None
        _SHADERS.update({
            "poly": poly, "point": point, "solid": solid,
            "poly_name": poly_name, "point_name": point_name, "solid_name": solid_name,
        })
    return _SHADERS


def disable_overlay():
    _SHADERS["failed"] = True


def _line_width(shaders, shader, width):
    if shaders["poly_name"] == "POLYLINE_UNIFORM_COLOR":
        try:
            shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
            shader.uniform_float("lineWidth", width)
            return
        except Exception:
            pass
    try:
        gpu.state.line_width_set(width)
    except Exception:
        pass


def _draw_lines(shaders, points, color, width, loop=False):
    if len(points) < 2:
        return
    batch = batch_for_shader(shaders["poly"], "LINE_LOOP" if loop else "LINE_STRIP", {"pos": points})
    shaders["poly"].bind()
    _line_width(shaders, shaders["poly"], width)
    shaders["poly"].uniform_float("color", color)
    batch.draw(shaders["poly"])


def _draw_rect(shaders, x1, y1, x2, y2, color):
    verts = [(x1, y1), (x2, y1), (x2, y2), (x1, y1), (x2, y2), (x1, y2)]
    batch = batch_for_shader(shaders["solid"], "TRIS", {"pos": verts})
    shaders["solid"].bind()
    shaders["solid"].uniform_float("color", color)
    batch.draw(shaders["solid"])


def _draw_rect_outline(shaders, x1, y1, x2, y2, color):
    verts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    batch = batch_for_shader(shaders["poly"], "LINE_LOOP", {"pos": verts})
    shaders["poly"].bind()
    _line_width(shaders, shaders["poly"], 1.0)
    shaders["poly"].uniform_float("color", color)
    batch.draw(shaders["poly"])


# --------------------------------------------------------------------------- #
# base operator (shared by the modal and the instant version)
# --------------------------------------------------------------------------- #

class FallbackPrefs:
    """Used when the add-on preferences are not reachable (scripted runs)."""

    col_curve = (0.25, 0.75, 1.0, 1.0)
    col_point = (0.9, 0.9, 0.9, 1.0)
    col_point_selected = (0.25, 0.75, 1.0, 1.0)
    col_point_active = (1.0, 0.65, 0.15, 1.0)
    col_original = (0.55, 0.55, 0.55, 1.0)
    point_size = 7.0
    point_size_selected = 9.0
    point_size_active = 11.0
    hit_radius = 14
    use_density = False
    right_click_action = "CONFIRM"
    left_click_empty = "DESELECT"
    hud_corner = "BOTTOM_LEFT"
    hud_size = 15
    hud_lang = "RU"
    hud_show_keys = True
    mirror = False
    mirror_axis = "X"
    interpolate_loops = False
    interp_shape = "CUBIC"
    interp_tension = 1.0
    interp_smooth = 4
    interp_sheet = True
    interp_angle = 40.0
    interp_facing = 70.0


class FFLEX_OT_flex_base(Operator):
    bl_options = {"REGISTER", "UNDO"}

    use_panel_settings: BoolProperty(default=True, options={"HIDDEN"})
    space_mode: EnumProperty(name="Space", items=SPACE_ITEMS, default=core.SPACE_SCREEN)
    axis: EnumProperty(name="Axis", items=AXIS_ITEMS, default="Z")
    pivot: EnumProperty(name="Pivot", items=PIVOT_ITEMS, default=core.PIVOT_CENTROID)
    points: IntProperty(name="Control Points", default=3, min=2, max=64)
    auto_points: BoolProperty(name="Auto Points", default=True)
    hug_tolerance: FloatProperty(name="Fit Tolerance", default=0.02, min=0.002, max=0.25)
    roundness: FloatProperty(name="Roundness", default=1.0, min=1.0, max=2.0)
    segments: IntProperty(name="Curve Quality", default=12, min=3, max=48)
    strength: FloatProperty(name="Strength", default=1.0, min=0.0, max=1.0)
    keep_offset: BoolProperty(name="Keep Offset", default=False)
    flatten: BoolProperty(name="Project to Plane", default=False)
    use_density: BoolProperty(name="Vertex Density", default=False)
    even_spacing: BoolProperty(name="Even Spacing", default=False)
    equal_gaps: BoolProperty(name="Equal Gaps", default=True)
    split_ribbon: BoolProperty(name="Split Ribbons", default=True)
    pinch_ratio: FloatProperty(name="Pinch Threshold", default=0.4, min=0.05, max=1.0)
    corner_angle: FloatProperty(name="Corner Angle", default=45.0, min=5.0, max=170.0)
    mirror: BoolProperty(name="Mirror", default=False)
    mirror_axis: EnumProperty(name="Mirror Axis", items=AXIS_ITEMS, default="X")
    interpolate_loops: BoolProperty(name="Interpolate Between Curves", default=False)
    interp_shape: EnumProperty(name="Blend Shape", items=INTERP_SHAPE_ITEMS, default="CUBIC")
    interp_tension: FloatProperty(name="Tension", default=1.0, min=0.0, max=1.0)
    interp_smooth: IntProperty(name="Transition Smoothing", default=4, min=0, max=8)
    interp_sheet: BoolProperty(name="Smooth Sheet Only", default=True)
    interp_angle: FloatProperty(name="Crease Angle", default=40.0, min=0.0, max=180.0)
    interp_facing: FloatProperty(name="Facing Limit", default=70.0, min=0.0, max=180.0)
    show_original: BoolProperty(name="Show Original", default=True)
    iterations: IntProperty(name="Iterations", default=1, min=1, max=32)

    _handle_3d = None
    _handle_2d = None

    # ------------------------------------------------------------------ #
    # handlers
    # ------------------------------------------------------------------ #
    @classmethod
    def remove_handlers(cls):
        remove_draw_handlers()

    # ------------------------------------------------------------------ #
    # settings / setup
    # ------------------------------------------------------------------ #
    def sync_from_settings(self, context):
        settings = get_settings(context)
        if settings is None or not self.use_panel_settings:
            return
        prefs = get_prefs(context)

        def remembered(name, fallback):
            """The value used last time, so a fresh file starts where you left off."""
            value = getattr(prefs, name, "") if prefs is not None else ""
            return value if value else fallback

        self.space_mode = remembered("last_space", settings.space_mode)
        self.axis = remembered("last_axis", settings.axis)
        self.pivot = remembered("last_pivot", settings.pivot)
        # keep the panel in step with what the tool is really going to use
        settings.space_mode = self.space_mode
        settings.axis = self.axis
        settings.pivot = self.pivot
        self.points = settings.points
        self.auto_points = settings.auto_points
        self.hug_tolerance = settings.hug_tolerance
        self.roundness = settings.roundness
        self.segments = settings.segments
        self.strength = settings.strength
        self.keep_offset = settings.keep_offset
        self.flatten = settings.flatten
        self.use_density = settings.use_density
        self.even_spacing = settings.even_spacing
        self.equal_gaps = settings.equal_gaps
        self.split_ribbon = settings.split_ribbon
        self.pinch_ratio = settings.pinch_ratio
        self.corner_angle = settings.corner_angle
        self.mirror = settings.mirror if MIRROR_ENABLED else False
        self.mirror_axis = settings.mirror_axis
        self.interpolate_loops = settings.interpolate_loops
        self.interp_shape = settings.interp_shape
        self.interp_tension = getattr(settings, "interp_tension", 1.0)
        self.interp_smooth = settings.interp_smooth
        self.interp_sheet = settings.interp_sheet if SHEET_ENABLED else False
        self.interp_angle = settings.interp_angle
        self.interp_facing = settings.interp_facing
        self.show_original = settings.show_original
        prefs = get_prefs(context)
        if prefs is not None:
            self.use_density = self.use_density or prefs.use_density

    def remember_frame(self, context):
        """Store space / axis / pivot so the next run starts with the same setup.

        They go into the scene settings (so the panel shows them) *and* into the
        add-on preferences, which is what survives a new file or a restart.
        """
        settings = get_settings(context)
        if settings is not None:
            settings.space_mode = self.space_mode
            settings.axis = self.axis
            settings.pivot = self.pivot
        prefs = get_prefs(context)
        if prefs is not None:
            prefs.last_space = self.space_mode
            prefs.last_axis = self.axis
            prefs.last_pivot = self.pivot

    def setup(self, context):
        """Collect the mesh selection and build one curve per edge loop."""
        self.obj = context.active_object
        self.error = None
        if self.obj is None or self.obj.type != "MESH":
            self.error = "FFlex works on mesh objects"
            return False

        self.session = MeshSession(self.obj)
        self.matrix_world = self.obj.matrix_world.copy()
        self.matrix_inv = self.matrix_world.inverted()
        self.scene = context.scene
        self.cursor_location = (context.scene.cursor.location.copy()
                                if getattr(context, "scene", None) is not None else None)
        self.owned = set()             # control points the user moved himself
        self.mirrored_points = set()   # control points written by the symmetry
        self.mirror_written = {}       # vertices written by the symmetry -> rest position
        self.mirror_pivot_local = Vector((0.0, 0.0, 0.0))
        self.gap_shapes = {}           # per gap blend shape overrides
        self.gap_pairs = []
        self.gap_spans = {}

        raw = core.build_chains(self.session.bmesh())
        raw = self.split_chains(raw)
        chains = []
        for indices, closed in raw:
            if len(indices) < 2:
                continue
            chains.append(self.make_chain(context, indices, closed))

        if not chains:
            self.error = "Select an edge loop first (at least 2 connected vertices, 3+ recommended)"
            self.session.free()
            self.session = None
            return False

        self.chains = chains
        self.sel = set()
        self.active = (-1, -1)
        self.build_mirror_maps()
        self.auto_points_for(context)
        self.refresh_params()
        # the sheet classification is taken once, while the mesh is still untouched:
        # re-planning in the middle of a drag must not change which loops take part
        self.sheet_ok = None
        self.interp_rest = {}
        if len(self.chains) > 1 and self.interpolate_loops:
            self.sheet_ok = self.sheet_vertices(self.session.bmesh())
        self.build_interpolation()
        return True

    def split_chains(self, raw):
        """Turn the boundary of a ribbon (two rails + two short ends) into rails."""
        if not self.split_ribbon:
            return raw
        bm = self.session.bmesh()
        out = []
        for indices, closed in raw:
            parts = self.chain_split_parts(indices, closed, bm)
            if parts is not None:
                out.extend((part, False) for part in parts)
                continue
            out.append((indices, closed))
        return out

    def chain_split_parts(self, indices, closed, bm):
        """Two index lists when a closed chain should become two rails, else None."""
        if not closed or len(indices) < 6:
            return None
        points = [self.matrix_world @ bm.verts[i].co for i in indices]
        cuts = core.ribbon_cut_edges(points, True, self.pinch_ratio)
        if len(cuts) != 2:
            corners = core.split_closed_at_corners(points, self.corner_angle)
            cuts = [(c - 1) % len(indices) for c in corners]
        if len(cuts) != 2:
            return None
        part_a, part_b = core.split_closed_chain(indices, cuts[0], cuts[1])
        if len(part_a) < 3 or len(part_b) < 3:
            return None
        return part_a, part_b

    def make_chain(self, context, indices, closed, world=None):
        """One edge loop -> its own imaginary curve.

        The curve is a 3D spline through the loop's own points (that is what
        makes it hug an already curved loop). *Project to Plane* is the only
        thing that pulls it into the work plane.
        """
        bm = self.session.bmesh()
        if world is None:
            world = [self.matrix_world @ bm.verts[i].co for i in indices]
        anchor = Vector((0.0, 0.0, 0.0))
        for w in world:
            anchor += w
        anchor /= max(1, len(world))

        frame = core.make_frame(context, self.obj, self.space_mode, self.axis, self.pivot, anchor)
        source = [frame.project(w) for w in world] if self.flatten else list(world)

        count = max(3, self.points) if closed else max(2, self.points)
        ctrl = core.derive_control_points(source, count, closed, self.use_density)

        chain = {
            "indices": list(indices),
            "closed": bool(closed),
            "world": world,
            "source": source,
            "anchor": anchor,
            "frame": frame,
            "curve": Curve(ctrl, closed, self.roundness, self.segments),
            "ctrl0": [p.copy() for p in ctrl],
            "params": [],
            "residual": [],
        }
        return chain

    def auto_points_for(self, context):
        """Raise the control point count until the curve follows the selection."""
        if not self.auto_points or not self.chains:
            return
        needed = 3
        for chain in self.chains:
            needed = max(needed, core.auto_control_count(
                chain["source"], chain["closed"], self.roundness, self.segments,
                self.hug_tolerance, minimum=2, maximum=16, use_density=self.use_density))
        if needed == self.points:
            return
        self.points = needed
        settings = get_settings(context)
        if settings is not None:
            settings.points = needed
        for chain in self.chains:
            ctrl = core.derive_control_points(chain["source"], needed, chain["closed"], self.use_density)
            chain["curve"] = Curve(ctrl, chain["closed"], self.roundness, self.segments)
            # the "original" copy follows the new control point count, otherwise F
            # would restore the wrong points
            chain["ctrl0"] = [p.copy() for p in ctrl]

    # ------------------------------------------------------------------ #
    # mirror (symmetry around the work pivot, like Mirror in Edit Mode)
    # ------------------------------------------------------------------ #
    def mirror_pivot_world(self):
        """The point the symmetry plane passes through (the tool's Pivot)."""
        if self.pivot == "OBJECT":
            return self.matrix_world @ Vector((0.0, 0.0, 0.0))
        if self.pivot == "CURSOR":
            return Vector(self.context_cursor())
        if self.pivot == "WORLD":
            return Vector((0.0, 0.0, 0.0))
        centre = Vector((0.0, 0.0, 0.0))
        for chain in self.chains:
            centre += chain["anchor"]
        return centre / max(1, len(self.chains))

    def context_cursor(self):
        cursor = getattr(self, "cursor_location", None)
        if cursor is not None:
            return cursor
        scene = getattr(self, "scene", None)
        if scene is not None and getattr(scene, "cursor", None) is not None:
            return scene.cursor.location.copy()
        return (0.0, 0.0, 0.0)

    def mirror_local_co(self, co):
        """Reflect a *local* coordinate across the mirror plane."""
        local = Vector(co)
        axis = core.AXIS_INDEX.get(self.mirror_axis, 0)
        pivot = getattr(self, "mirror_pivot_local", None)
        centre = pivot[axis] if pivot is not None else 0.0
        local[axis] = 2.0 * centre - local[axis]
        return local

    def mirror_world(self, co):
        return self.matrix_world @ self.mirror_local_co(self.matrix_inv @ Vector(co))

    def build_mirror_maps(self):
        """Rebuild the vertex and control point pairs of the symmetry.

        Everything is searched on the *rest* positions, so a rebuild in the middle
        of a drag (or after switching the axis) cannot compound the deformation.
        """
        self.mirror_map = {}
        self.ctrl_mirror = {}
        if not (MIRROR_ENABLED and self.mirror):
            return

        self.mirror_pivot_local = self.matrix_inv @ self.mirror_pivot_world()
        bm = self.session.bmesh()
        kd = kdtree.KDTree(len(bm.verts))
        for vert in bm.verts:
            kd.insert(vert.co, vert.index)
        kd.balance()

        for chain in self.chains:
            for i, index in enumerate(chain["indices"]):
                rest = self.matrix_inv @ chain["world"][i]
                found = kd.find(self.mirror_local_co(rest))
                if found[2] is not None and found[2] < 1e-4 and found[1] != index:
                    self.mirror_map[index] = found[1]

        # control points are paired across *all* chains, so dragging a handle of one
        # half moves the matching handle of the other half as well
        controls = []
        for chain_index, chain in enumerate(self.chains):
            chain_key = chain["indices"][0]
            rest_ctrl = chain.get("ctrl0", chain["curve"].ctrl)
            for point_index, point in enumerate(rest_ctrl):
                controls.append((chain_index, chain_key, point_index,
                                 self.matrix_inv @ point))
        for chain_index, chain_key, point_index, point in controls:
            mirrored = self.mirror_local_co(point)
            for other_chain, other_key, other_point, other in controls:
                if (other_chain, other_point) == (chain_index, point_index):
                    continue
                if (other - mirrored).length < 1e-4:
                    self.ctrl_mirror[(chain_key, point_index)] = (other_chain, other_point)
                    break

    def clear_mirrored_controls(self):
        """Undo the control points the symmetry wrote (the user's own edits stay)."""
        touched = set()
        for chain in self.chains:
            chain_key = chain["indices"][0]
            for (key, point_index) in list(getattr(self, "mirrored_points", ())):
                if key != chain_key or (key, point_index) in self.owned:
                    continue
                if point_index < len(chain["ctrl0"]):
                    chain["curve"].ctrl[point_index] = chain["ctrl0"][point_index].copy()
                    touched.add(self.chains.index(chain))
        for chain_index in touched:
            self.chains[chain_index]["curve"].rebuild()
        self.mirrored_points = set()

    def apply_mirror_to_controls(self):
        """Give every user edited control point its mirrored twin."""
        self.mirrored_points = set()
        if not (self.mirror and self.ctrl_mirror):
            return
        for (chain_key, point_index) in list(getattr(self, "owned", ())):
            target = self.ctrl_mirror.get((chain_key, point_index))
            if target is None:
                continue
            source_chain = None
            for chain in self.chains:
                if chain["indices"][0] == chain_key:
                    source_chain = chain
                    break
            partner_chain = self.chains[target[0]] if target[0] < len(self.chains) else None
            if source_chain is None or partner_chain is None:
                continue
            if point_index >= len(source_chain["curve"].ctrl) or target[1] >= len(partner_chain["curve"].ctrl):
                continue
            partner_chain["curve"].ctrl[target[1]] = self.mirror_world(
                source_chain["curve"].ctrl[point_index])
            self.mirrored_points.add((partner_chain["indices"][0], target[1]))
            partner_chain["curve"].rebuild()

    def clear_mirrored_vertices(self):
        """Put the vertices the old symmetry moved back to their rest place."""
        written = getattr(self, "mirror_written", None)
        if not written:
            return
        bm = self.session.bmesh()
        bm.verts.ensure_lookup_table()
        for index, co in written.items():
            if index < len(bm.verts):
                bm.verts[index].co = co.copy()
        self.mirror_written = {}

    def set_mirror(self, context, axis=None):
        """``axis=None`` toggles the symmetry, an axis switches to it.

        Pressing the axis that is already active turns the symmetry off again, and
        switching the axis first undoes what the previous axis had written.
        """
        if not MIRROR_ENABLED:
            self.mirror = False
            self.report({"INFO"}, i18n.hud(getattr(get_prefs(context), "hud_lang", "RU"),
                                           "mirror_off"))
            return False
        self.clear_mirrored_controls()
        self.clear_mirrored_vertices()
        if axis is None:
            self.mirror = not self.mirror
        elif self.mirror and self.mirror_axis == axis:
            self.mirror = False
        else:
            self.mirror = True
            self.mirror_axis = axis
        settings = get_settings(context)
        if settings is not None:
            settings.mirror = self.mirror
            settings.mirror_axis = self.mirror_axis
        self.build_mirror_maps()
        self.apply_mirror_to_controls()
        self.refresh_params()
        self.apply_deform()
        self.report({"INFO"}, "FFlex: mirror %s" % ("%s ON" % self.mirror_axis if self.mirror else "off"))
        redraw_view3d()

    def mirror_world_offset(self, co):
        """Mirrored world vector (the offset variant keeps the axis component)."""
        local = self.mirror_local_co(self.matrix_inv @ Vector(co))
        return self.matrix_world @ local

    # ------------------------------------------------------------------ #
    # geometry
    # ------------------------------------------------------------------ #
    def refresh_params(self):
        """Parameters of every vertex along its own loop.

        Like Mio3 Flex the parameter is the vertex' own share of the loop length,
        so a curved loop gives a curve that hugs it and the vertices follow that
        curve. *Even Spacing* replaces the shares with evenly distributed ones.
        """
        for chain in self.chains:
            source = chain["source"]
            count = len(source)
            if self.even_spacing:
                chain["params"] = core.even_curve_params(chain["curve"], count, chain["closed"],
                                                         self.equal_gaps)
            else:
                chain["params"] = core.length_params(source, chain["closed"])
            if self.keep_offset and not self.flatten:
                chain["residual"] = [
                    source[i] - chain["curve"].point_at_param(chain["params"][i])
                    for i in range(count)
                ]
            else:
                chain["residual"] = [Vector((0.0, 0.0, 0.0)) for _ in source]

    def topology_ok(self):
        """False when another tool / undo replaced the mesh under the operator."""
        count = len(self.session.bmesh().verts)
        return all(index < count for chain in self.chains for index in chain["indices"])

    def apply_deform(self, commit=True):
        if not self.topology_ok():
            raise RuntimeError("the mesh changed while FFlex was running")
        bm = self.session.bmesh()
        processed = set()
        for chain in self.chains:
            curve = chain["curve"]
            frame = chain["frame"]
            params = chain["params"]
            residual = chain["residual"]
            world = chain["world"]
            for i, index in enumerate(chain["indices"]):
                target = curve.point_at_param(params[i])
                if residual[i].length_squared:
                    target = target + residual[i]
                if self.flatten:
                    target = frame.project(target)
                if self.strength < 1.0:
                    target = world[i].lerp(target, self.strength)
                local = self.matrix_inv @ target
                if self.mirror:
                    # a vertex that sits on the mirror plane has to stay on it
                    axis = core.AXIS_INDEX.get(self.mirror_axis, 0)
                    if abs((self.matrix_inv @ world[i])[axis]) < 1e-5:
                        local[axis] = 0.0
                bm.verts[index].co = local
                processed.add(index)

        if self.mirror and self.mirror_map:
            written = getattr(self, "mirror_written", None)
            if written is None:
                written = self.mirror_written = {}
            for index, partner in self.mirror_map.items():
                if index in processed and partner not in processed:
                    if partner not in written:
                        written[partner] = bm.verts[partner].co.copy()   # rest position
                    bm.verts[partner].co = self.mirror_local_co(bm.verts[index].co)
                    processed.add(partner)

        self.apply_interpolation(bm, processed)

        if commit:
            self.session.commit()

    def restore_original(self, commit=True):
        if not self.topology_ok():
            return
        bm = self.session.bmesh()
        self.clear_mirrored_vertices()
        for chain in self.chains:
            for i, index in enumerate(chain["indices"]):
                bm.verts[index].co = self.matrix_inv @ chain["world"][i]
        if self.mirror and self.mirror_map:
            written = getattr(self, "mirror_written", None)
            if written is None:
                written = self.mirror_written = {}
            for index, partner in self.mirror_map.items():
                if partner not in written:
                    written[partner] = bm.verts[partner].co.copy()
                bm.verts[partner].co = self.mirror_local_co(bm.verts[index].co)
        if commit:
            self.session.commit()

    # ------------------------------------------------------------------ #
    # interpolation of the loops between two curves
    # ------------------------------------------------------------------ #
    def build_interpolation(self):
        """Plan how the loops *between* the fitted curves should follow them.

        Every pair of curves forms a band: a walk from one curve that is not allowed
        to pass through the other one. A loop reachable from *both* sides lies
        between them and gets a share ``t`` of the way from one curve to the other,
        plus the two curve vertices it corresponds to. Loops outside every band are
        left alone (no extrapolation), however far apart the curves are.
        """
        self.interp = []
        self.gap_spans = {}
        self.gap_pairs = []
        if not self.interpolate_loops or len(self.chains) < 2:
            return
        bm = self.session.bmesh()
        chain_of_vertex = {}
        for chain_index, chain in enumerate(self.chains):
            for index in chain["indices"]:
                chain_of_vertex[index] = chain_index

        limit = max(64, 4 * len(bm.verts) // max(1, len(self.chains)) + 32)
        hard = set()
        facing = None
        off_sheet = set()
        if SHEET_ENABLED and getattr(self, "interp_sheet", False):
            # keep the band on one smooth sheet: the rim of a thickness and the wall
            # behind it must not be dragged along
            hard = core.crease_edges(bm, math.radians(getattr(self, "interp_angle", 40.0)))
            bm.normal_update()
            facing = math.cos(math.radians(getattr(self, "interp_facing", 70.0)))
            chain_normals = []
            for chain in self.chains:
                total = Vector((0.0, 0.0, 0.0))
                for index in chain["indices"]:
                    total = total + bm.verts[index].normal
                chain_normals.append(total / max(1, len(chain["indices"])))
            if any(normal.length_squared <= 1e-12 for normal in chain_normals):
                facing = None           # a mesh without usable normals (edges only)
            else:
                # the walk may reach these vertices but never expand through them, so
                # it cannot sneak around the rim onto the wall behind it
                for vert in bm.verts:
                    if vert.normal.length_squared <= 1e-12:
                        continue
                    if all(vert.normal.dot(normal) < facing for normal in chain_normals):
                        off_sheet.add(vert.index)
        best = {}
        walks = {}
        for first in range(len(self.chains)):
            for second in range(first + 1, len(self.chains)):
                key = (first, second)
                self.gap_pairs.append(key)
                stops_first = list(self.chains[second]["indices"]) + sorted(off_sheet)
                stops_second = list(self.chains[first]["indices"]) + sorted(off_sheet)
                left, left_src = core.bfs_from_vertices(
                    bm, self.chains[first]["indices"], limit,
                    blocked=stops_first, hard_edges=hard)
                right, right_src = core.bfs_from_vertices(
                    bm, self.chains[second]["indices"], limit,
                    blocked=stops_second, hard_edges=hard)
                walks[key] = (left, left_src, right, right_src)
                widest = 0
                candidates = {}
                for index, distance_left in left.items():
                    if index in chain_of_vertex or index not in right:
                        continue
                    distance_right = right[index]
                    total = distance_left + distance_right
                    if total <= 0:
                        continue
                    if facing is not None:
                        # the vertex must face the same way as the curve it belongs to,
                        # otherwise it is on the thickness or on the far wall. A mesh
                        # without faces has no normals at all - then this test is skipped
                        normal = bm.verts[index].normal
                        source_normal = bm.verts[left_src[index]].normal
                        if normal.length_squared > 1e-12 and source_normal.length_squared > 1e-12:
                            if normal.dot(source_normal) < facing:
                                continue
                    candidates[index] = (total, distance_left / float(total),
                                         left_src[index], right_src[index])
                if not candidates:
                    self.gap_spans[key] = 0
                    continue
                for index, (total, share, source_left, source_right) in candidates.items():
                    widest = max(widest, total)
                    entry = (total, key, share, source_left, source_right)
                    previous = best.get(index)
                    if previous is None or entry[0] < previous[0]:
                        best[index] = entry
                self.gap_spans[key] = widest

        chain_slot = []
        for chain in self.chains:
            chain_slot.append({index: position for position, index in enumerate(chain["indices"])})
        # index -> (curve, position on it), used by the relaxation of the shift field
        self.chain_of_vertex = {}
        for chain_index, chain in enumerate(self.chains):
            for slot, index in enumerate(chain["indices"]):
                self.chain_of_vertex[index] = (chain_index, slot)
        nearest = {}
        for key, span in self.gap_spans.items():
            for chain_index in key:
                current = nearest.get(chain_index)
                if current is None or span < current:
                    nearest[chain_index] = span

        def counterpart(chain_index, vertex, avoid):
            """The corresponding vertex of the curve next to ``chain_index``.

            ``avoid`` is the curve of the current band; only a *neighbouring* curve
            counts (one whose band is no wider than the closest one), otherwise a
            curve on the far side of another one would be picked up. That is what
            makes the cubic blend continuous across the curves.
            """
            for key, data in walks.items():
                if chain_index not in key:
                    continue
                other = key[0] if key[1] == chain_index else key[1]
                if other == avoid:
                    continue
                if self.gap_spans.get(key, 0) > nearest.get(chain_index, 0):
                    continue        # another curve sits in between
                left, left_src, right, right_src = data
                # sources map a *reached* vertex to the vertex of the walked curve,
                # so the counterpart comes from the walk of the *other* curve
                sources = right_src if key[0] == chain_index else left_src
                if vertex in sources:
                    target = sources[vertex]
                    slot = chain_slot[other].get(target)
                    if slot is not None:
                        return (other, slot, target)
            return None

        # ---- plan -------------------------------------------------------- #
        # Every vertex between two curves gets a share of the way plus the two curve
        # vertices it corresponds to. The shifts are then relaxed over the band (see
        # apply_interpolation): that evens them out *along* every loop, which is what
        # keeps a loop's own shape, while the blend profile across the loops stays
        # exactly the chosen shape.
        ok = self.sheet_ok if (SHEET_ENABLED and getattr(self, "interp_sheet", False)) else None
        kept = 0
        for index, (_total, key, share, source_left, source_right) in best.items():
            if ok is not None and index not in ok:
                continue
            slot_left = chain_slot[key[0]].get(source_left)
            slot_right = chain_slot[key[1]].get(source_right)
            if slot_left is None or slot_right is None:
                continue
            knot_a = (key[0], slot_left, source_left)
            knot_b = (key[1], slot_right, source_right)
            before = counterpart(key[0], source_left, key[1]) or knot_a
            after = counterpart(key[1], source_right, key[0]) or knot_b
            self.interp.append((index, key, share, before, knot_a, knot_b, after,
                                self.rest_position(bm, index)))
            kept += 1
        self.build_interp_smoothing(bm)
        _LAST_DRAW["interp_rows"] = kept

    def build_interp_smoothing(self, bm):
        """Neighbour graph of the band, used to relax the shift field.

        Only *along the loops* is relaxed: a neighbour standing on the next loop (a
        different share of the way between the curves) gets little or no weight, so
        the blend profile from loop to loop keeps exactly the chosen shape while the
        shift is evened out along every loop - that is what keeps a loop's own shape.
        """
        self.interp_neighbors = []
        self.interp_pinned = []
        if not self.interp:
            return
        slots = {}
        shares = []
        for slot, entry in enumerate(self.interp):
            slots[entry[0]] = slot
            shares.append(entry[2])
        # the share step from one loop to the next, measured on the band itself
        steps = []
        for slot, entry in enumerate(self.interp):
            for edge in bm.verts[entry[0]].link_edges:
                target = slots.get(edge.other_vert(bm.verts[entry[0]]).index)
                if target is None or self.interp[target][1] != entry[1]:
                    continue
                steps.append(abs(shares[target] - shares[slot]))
        steps.sort()
        step = steps[int(len(steps) * 0.9)] if steps else 0.0
        tolerance = max(1e-9, step * 0.5)

        neighbors = []
        pinned = []
        for slot, entry in enumerate(self.interp):
            vert = bm.verts[entry[0]]
            around = []
            fixed = []
            for edge in vert.link_edges:
                other = edge.other_vert(vert).index
                target = slots.get(other)
                if target is None:
                    fixed.append(other)
                    continue
                weight = 1.0 - abs(shares[target] - shares[slot]) / tolerance
                if weight > 0.0:
                    around.append((target, weight))
            neighbors.append(tuple(around))
            pinned.append(tuple(fixed))
        self.interp_neighbors = neighbors
        self.interp_pinned = pinned

    def sheet_vertices(self, bm, chain_of_vertex=None):
        """Vertices that are between two curves *and* on the wanted sheet.

        The rim of a thickness and the wall behind it are left out: the walk may not
        cross sharp creases and may not pass through vertices facing the other way.
        """
        if chain_of_vertex is None:
            chain_of_vertex = {}
            for chain_index, chain in enumerate(self.chains):
                for index in chain["indices"]:
                    chain_of_vertex[index] = chain_index
        hard = core.crease_edges(bm, math.radians(getattr(self, "interp_angle", 40.0)))
        bm.normal_update()
        facing = math.cos(math.radians(getattr(self, "interp_facing", 70.0)))
        chain_normals = []
        for chain in self.chains:
            total = Vector((0.0, 0.0, 0.0))
            for index in chain["indices"]:
                total = total + bm.verts[index].normal
            chain_normals.append(total / max(1, len(chain["indices"])))
        if any(normal.length_squared <= 1e-12 for normal in chain_normals):
            return set()            # a mesh without usable normals (edges only)
        limit = max(64, 4 * len(bm.verts) // max(1, len(self.chains)) + 32)
        off_sheet = set()
        for vert in bm.verts:
            if vert.normal.length_squared <= 1e-12:
                continue
            if all(vert.normal.dot(normal) < facing for normal in chain_normals):
                off_sheet.add(vert.index)
        result = set()
        for first in range(len(self.chains)):
            for second in range(first + 1, len(self.chains)):
                left, left_src = core.bfs_from_vertices(
                    bm, self.chains[first]["indices"], limit,
                    blocked=list(self.chains[second]["indices"]) + sorted(off_sheet),
                    hard_edges=hard)
                right, _right_src = core.bfs_from_vertices(
                    bm, self.chains[second]["indices"], limit,
                    blocked=list(self.chains[first]["indices"]) + sorted(off_sheet),
                    hard_edges=hard)
                for index in left:
                    if index in chain_of_vertex or index not in right:
                        continue
                    normal = bm.verts[index].normal
                    source_normal = bm.verts[left_src[index]].normal
                    if normal.length_squared > 1e-12 and source_normal.length_squared > 1e-12:
                        if normal.dot(source_normal) < facing:
                            continue
                    result.add(index)
        return result

    def gap_shape(self, key):
        """Blend shape of one gap: ``CUBIC``, ``QUADRATIC`` or ``LINEAR``."""
        override = getattr(self, "gap_shapes", {}).get(tuple(key))
        if override:
            return override
        if not SHAPES_ENABLED:
            return "LINEAR"
        mode = getattr(self, "interp_shape", "CUBIC")
        if mode == "AUTO":
            return "CUBIC" if self.gap_spans.get(tuple(key), 0) >= 4 else "LINEAR"
        return mode

    @staticmethod
    def blend_weight(share, shape, tension=1.0):
        """Weight of the *second* curve at ``share`` of the way from the first.

        ``LINEAR`` is a straight ramp. ``QUADRATIC`` is a real quadratic S made of two
        parabolas (``2t^2`` and its mirror): it meets the middle at half way with the
        same slope from both sides, and it is visibly different from the ramp even
        when only one or two loops lie between the curves. ``Tension`` fades between
        the ramp (0) and that quadratic ease (1).
        """
        share = min(1.0, max(0.0, share))
        if shape == "QUADRATIC":
            if share <= 0.5:
                eased = 2.0 * share * share
            else:
                eased = 1.0 - 2.0 * (1.0 - share) * (1.0 - share)
            return share + tension * (eased - share)
        return share

    @staticmethod
    def catmull_rom(previous, first, second, following, share, tension=1.0):
        """Hermite / Catmull-Rom blend between ``first`` and ``second``.

        ``tension`` scales the tangents: 1 is the usual Catmull-Rom (C1 across the
        curves), 0 flattens them into a smooth step, more makes the transition
        stronger.
        """
        share = min(1.0, max(0.0, share))
        share2 = share * share
        share3 = share2 * share
        h00 = 2.0 * share3 - 3.0 * share2 + 1.0
        h10 = share3 - 2.0 * share2 + share
        h01 = -2.0 * share3 + 3.0 * share2
        h11 = share3 - share2
        half = 0.5 * tension
        tangent0 = (second - previous) * half
        tangent1 = (following - first) * half
        return first * h00 + tangent0 * h10 + second * h01 + tangent1 * h11

    def knot_offset(self, knot, bm):
        """Current displacement of one curve vertex (the knot of the spline)."""
        chain = self.chains[knot[0]]
        return (self.matrix_world @ bm.verts[knot[2]].co) - chain["world"][knot[1]]

    def apply_interpolation(self, bm, processed):
        """Move the in-between loops to an absolute blended position.

        The rest positions come from a single snapshot, so this is idempotent and
        toggling any option always rebuilds exactly the same mesh. The shifts are
        relaxed over the band (``Transition Smoothing``), which evens them out along
        every loop: a straight loop stays straight instead of copying the bend of a
        neighbouring curve, while the profile across the loops keeps its shape.
        """
        interp = getattr(self, "interp", None)
        if not interp:
            return
        matrix_dir = self.matrix_inv.to_3x3()
        verts = bm.verts
        shapes = {}
        offsets = []
        tension = float(getattr(self, "interp_tension", 1.0))
        for entry in interp:
            _index, key, share, before, knot_a, knot_b, after, _rest = entry
            shape = shapes.get(key)
            if shape is None:
                shape = shapes[key] = self.gap_shape(key)
            if shape == "CUBIC":
                offsets.append(type(self).catmull_rom(
                    self.knot_offset(before, bm), self.knot_offset(knot_a, bm),
                    self.knot_offset(knot_b, bm), self.knot_offset(after, bm),
                    share, tension))
            else:
                weight = type(self).blend_weight(share, shape, tension)
                offsets.append(self.knot_offset(knot_a, bm).lerp(
                    self.knot_offset(knot_b, bm), weight))

        passes = int(getattr(self, "interp_smooth", 0) or 0)
        neighbors = getattr(self, "interp_neighbors", ())
        pinned = getattr(self, "interp_pinned", ())
        if passes and len(neighbors) == len(offsets):
            for _pass in range(passes):
                relaxed = list(offsets)
                for slot, value in enumerate(offsets):
                    total = value * 2.0
                    count = 2.0
                    for other, weight in neighbors[slot]:
                        total = total + offsets[other] * weight
                        count += weight
                    for other in pinned[slot]:
                        total = total + self.neighbour_offset(other, bm)
                        count += 1.0
                    relaxed[slot] = total / count
                offsets = relaxed

        for slot, entry in enumerate(interp):
            index = entry[0]
            if index in processed:
                continue
            verts[index].co = entry[7] + (matrix_dir @ offsets[slot])
            processed.add(index)

    def neighbour_offset(self, index, bm):
        """Shift of a vertex outside the band: a curve vertex or nothing at all."""
        spot = getattr(self, "chain_of_vertex", {}).get(index)
        if spot is None:
            return Vector((0.0, 0.0, 0.0))
        chain_index, slot = spot
        return (self.matrix_world @ bm.verts[index].co) - self.chains[chain_index]["world"][slot]

    def set_gap_shape(self, context, shape=None, key=None):
        """Change the blend shape of the gaps touching the selected curves.

        Without a selection every gap is changed; ``shape=None`` cycles between
        linear and quadratic.
        """
        pairs = list(getattr(self, "gap_pairs", ()))
        if key is not None:
            pairs = [tuple(key)]
        elif self.sel:
            selected = {chain_index for chain_index, _point in self.sel}
            chosen = [pair for pair in pairs if pair[0] in selected or pair[1] in selected]
            pairs = chosen or pairs
        if not pairs:
            return False
        order = ("LINEAR", "QUADRATIC")
        for pair in pairs:
            current = shape or self.gap_shape(pair)
            if shape is None:
                nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
            else:
                nxt = shape
            self.gap_shapes[tuple(pair)] = nxt
        self.apply_deform()
        self.report({"INFO"}, "FFlex: %s" % ", ".join(
            "%d-%d %s" % (pair[0] + 1, pair[1] + 1, self.gap_shape(pair)) for pair in pairs))
        redraw_view3d()
        return True

    def set_interp_shape(self, context, value):
        """Panel switch: one shape for every gap (drops the per-gap overrides)."""
        self.interp_shape = value
        self.gap_shapes = {}
        settings = get_settings(context)
        if settings is not None:
            settings.interp_shape = value
        self.apply_deform()
        redraw_view3d()

    def set_interp_tension(self, context, value):
        """Tension of the blend: 0 is a straight ramp, 1 the smooth step."""
        self.interp_tension = max(0.0, min(1.0, float(value)))
        settings = get_settings(context)
        if settings is not None:
            settings.interp_tension = self.interp_tension
        self.apply_deform()
        redraw_view3d()

    def rest_position(self, bm, index):
        """The untouched position of a vertex, remembered from the first plan.

        Rebuilding the plan while the mesh is already deformed (toggling the sheet
        option, changing the shape) must not turn the current position into the new
        "original": otherwise switching the option back would not restore the mesh.
        """
        store = getattr(self, "interp_rest", None)
        if store is None:
            store = self.interp_rest = {}
        if index not in store:
            store[index] = bm.verts[index].co.copy()
        return store[index]

    def clear_interpolation(self, commit=True):
        """Put every vertex the interpolation has ever moved back to its rest place."""
        store = getattr(self, "interp_rest", None)
        if not store:
            self.interp = []
            return
        bm = self.session.bmesh()
        bm.verts.ensure_lookup_table()
        for index, rest in store.items():
            if index < len(bm.verts):
                bm.verts[index].co = rest.copy()
        self.interp = []
        if commit:
            self.session.commit()

    def set_interp_sheet(self, context, value):
        """Keep the interpolation on one smooth sheet (no thickness, no far wall)."""
        self.interp_sheet = bool(value)
        settings = get_settings(context)
        if settings is not None:
            settings.interp_sheet = self.interp_sheet
        if self.interpolate_loops:
            self.clear_interpolation(commit=False)
            self.build_interpolation()
        self.apply_deform()
        self.report({"INFO"}, "FFlex: %s / %d loop vertex(es)" % (
            i18n.hud(getattr(get_prefs(context), "hud_lang", "RU"),
                     "sheet_" + ("on" if self.interp_sheet else "off")),
            len(getattr(self, "interp", []))))
        redraw_view3d()

    def set_interp_smooth(self, context, value):
        """Number of relaxation passes applied to the transition field."""
        self.interp_smooth = max(0, min(8, int(value)))
        settings = get_settings(context)
        if settings is not None:
            settings.interp_smooth = self.interp_smooth
        self.apply_deform()
        self.report({"INFO"}, "FFlex: transition smoothing %d" % self.interp_smooth)
        redraw_view3d()

    def rebuild_control_points(self, context, from_original=True, count=None):
        self.push_undo()
        """Recreate the control points from the original shape or from the current curve."""
        if count is not None:
            self.points = max(2, min(64, int(count)))
        for chain in self.chains:
            if from_original:
                ctrl = core.derive_control_points(chain["source"], self.points, chain["closed"],
                                                  self.use_density)
                chain["curve"] = Curve(ctrl, chain["closed"], self.roundness, self.segments)
            else:
                chain["curve"].ctrl = chain["curve"].resample(self.points)
                chain["curve"].rebuild()
            # the "original" is always derived from the *source* geometry, so changing
            # the control point count (even from the current, already moved shape) does
            # not overwrite it - otherwise F could not bring a point back any more
            chain["ctrl0"] = [p.copy() for p in core.derive_control_points(
                chain["source"], len(chain["curve"].ctrl), chain["closed"], self.use_density)]
        self.build_mirror_maps()

    def reset_selected_points(self, context):
        self.push_undo()
        """``F``: put the selected control points back where they started."""
        if not self.sel:
            return False
        touched = set()
        for chain_index, point_index in self.sel:
            chain = self.chains[chain_index]
            if 0 <= point_index < len(chain["ctrl0"]):
                chain["curve"].ctrl[point_index] = chain["ctrl0"][point_index].copy()
                touched.add(chain_index)
        for chain_index in touched:
            self.chains[chain_index]["curve"].rebuild()
        self.refresh_params()
        self.apply_deform()
        return bool(touched)

    def set_roundness(self, context, value):
        self.roundness = max(1.0, min(2.0, value))
        for chain in self.chains:
            chain["curve"].roundness = self.roundness
            chain["curve"].rebuild()
        self.refresh_params()
        self.apply_deform()

    def set_strength(self, context, value):
        self.strength = max(0.0, min(1.0, value))
        self.apply_deform()

    def set_even_spacing(self, context, value):
        self.push_undo()
        self.even_spacing = bool(value)
        self.refresh_params()
        self.apply_deform()

    def reset_deform(self, context):
        """Throw the deformation away: the curve is rebuilt from the original loop."""
        self.rebuild_control_points(context, from_original=True)
        self.refresh_params()
        self.restore_original(commit=False)
        self.apply_deform()

    def rebuild_frames(self, context, transform=True):
        """Recompute every work frame (space / axis / pivot changed).

        The curve itself is 3D and stays where it is; only the plane used to drag
        the handles moves. With *Project to Plane* the curve has to follow the new
        plane, so it gets re-projected.
        """
        for chain in self.chains:
            new_frame = core.make_frame(context, self.obj, self.space_mode, self.axis, self.pivot,
                                        chain["anchor"])
            chain["frame"] = new_frame
            if transform and self.flatten:
                chain["source"] = [new_frame.project(w) for w in chain["world"]]
                chain["curve"].ctrl = [new_frame.project(p) for p in chain["curve"].ctrl]
                chain["curve"].rebuild()
                chain["ctrl0"] = [p.copy() for p in chain["curve"].ctrl]
        self.build_mirror_maps()
        self.refresh_params()
        self.apply_deform()

    def set_flatten(self, context, value):
        self.push_undo()
        self.flatten = bool(value)
        for chain in self.chains:
            frame = chain["frame"]
            chain["source"] = ([frame.project(w) for w in chain["world"]] if self.flatten
                               else list(chain["world"]))
        self.rebuild_control_points(context, from_original=True)
        self.refresh_params()
        self.apply_deform()

    def set_interpolate(self, context, value):
        self.interpolate_loops = bool(value)
        settings = get_settings(context)
        if settings is not None:
            settings.interpolate_loops = self.interpolate_loops
        if self.interpolate_loops:
            self.clear_interpolation()
            self.build_interpolation()
        else:
            self.clear_interpolation()
        self.apply_deform()
        self.report({"INFO"}, "FFlex: %d loop vertex(es) between the curves" % len(getattr(self, "interp", [])))

    def set_split_corners(self, context, value):
        """Toggle automatic ribbon splitting; the chains have to be collected again."""
        self.split_ribbon = bool(value)
        settings = get_settings(context)
        if settings is not None:
            settings.split_ribbon = self.split_ribbon
        if getattr(self, "session", None) is not None:
            self.session.free()
            self.session = None
        if not self.setup(context):
            self.report({"WARNING"}, self.error or "FFlex could not rebuild the curves")
            return
        self.apply_deform()
        self.report({"INFO"}, "FFlex: %d curve(s)" % len(self.chains))

    # ------------------------------------------------------------------ #
    # curve editing
    # ------------------------------------------------------------------ #
    def insert_point_at(self, chain, point):
        self.push_undo()
        """Insert a control point (a 3D position on the curve) at the right span."""
        curve = chain["curve"]
        point = Vector(point)
        best_i, best_d = 0, float("inf")
        for i, sample in enumerate(curve.samples):
            d = (sample - point).length_squared
            if d < best_d:
                best_d = d
                best_i = i
        seg = max(1, curve.segments)
        index = (best_i // seg) + 1
        if not curve.closed:
            index = min(index, len(curve.ctrl) - 1)
            index = max(1, index)
        curve.ctrl.insert(index, point.copy())
        # the "original" point of a fresh control point is where it was created,
        # and every index based bookkeeping has to move along with it
        rest = chain.get("ctrl0")
        if rest is not None and len(rest) == len(curve.ctrl) - 1:
            rest.insert(index, point.copy())
        else:
            chain["ctrl0"] = [p.copy() for p in curve.ctrl]
        self.shift_point_indices(chain, inserted=index)
        curve.rebuild()
        return index

    def shift_point_indices(self, chain, removed=None, inserted=None):
        """Keep the selection/ownership/copy indices in sync with a changed curve."""
        chain_key = chain["indices"][0]
        chain_index = self.chains.index(chain)

        def move(index):
            if removed is not None and index == removed:
                return None
            if removed is not None and index > removed:
                index -= 1
            if inserted is not None and index >= inserted:
                index += 1
            return index

        self.sel = {(ci, moved) for (ci, point) in self.sel
                    for moved in ([move(point)] if ci == chain_index else [point])
                    if moved is not None}
        if self.active[0] == chain_index:
            moved = move(self.active[1])
            self.active = (chain_index, moved) if moved is not None else (-1, -1)
        self.owned = {(key, moved) for (key, point) in getattr(self, "owned", ())
                      for moved in ([move(point)] if key == chain_key else [point])
                      if moved is not None}
        self.mirrored_points = {(key, moved) for (key, point) in getattr(self, "mirrored_points", ())
                                for moved in ([move(point)] if key == chain_key else [point])
                                if moved is not None}
        self.ctrl_mirror = {
            (key, moved): (target_chain, target_point)
            for (key, point), (target_chain, target_point) in getattr(self, "ctrl_mirror", {}).items()
            for moved in ([move(point)] if key == chain_key else [point])
            if moved is not None
        }
        move_store = getattr(self, "move", None)
        if move_store and chain_index < len(move_store.get("store", [])):
            store = move_store["store"][chain_index]
            if inserted is not None and inserted <= len(store):
                store.insert(inserted, store[min(inserted, len(store) - 1)].copy())
            elif removed is not None and 0 <= removed < len(store):
                store.pop(removed)

    def add_point(self, context, mouse):
        hit = self.pick_curve(context, mouse)
        if hit is None:
            return False
        chain_index, sample_index = hit
        chain = self.chains[chain_index]
        point = chain["curve"].samples[sample_index].copy()

        if self.mirror:
            mirrored = self.mirror_world(point)
            existing = any((p - mirrored).length < 1e-4 for p in chain["curve"].ctrl)
            index = self.insert_point_at(chain, point)
            self.build_mirror_maps()
            if not existing:
                partner = self.ctrl_mirror.get((chain["indices"][0], index))
                if partner is None:
                    self.insert_point_at(chain, mirrored)
        else:
            index = self.insert_point_at(chain, point)

        self.build_mirror_maps()
        self.sel = {(chain_index, index)}
        self.active = (chain_index, index)
        self.apply_deform()
        return True

    def delete_points(self, context, points):
        self.push_undo()
        grouped = {}
        for chain_index, point_index in points:
            grouped.setdefault(chain_index, set()).add(point_index)
            if self.mirror:
                partner = self.ctrl_mirror.get((self.chains[chain_index]["indices"][0], point_index))
                if partner is not None:
                    grouped[chain_index].add(partner)

        for chain_index, indices in grouped.items():
            chain = self.chains[chain_index]
            curve = chain["curve"]
            for point_index in sorted(indices, reverse=True):
                if not (0 <= point_index < len(curve.ctrl)):
                    continue
                keep = False
                if curve.closed:
                    keep = len(curve.ctrl) <= 3
                else:
                    keep = point_index in (0, len(curve.ctrl) - 1) or len(curve.ctrl) <= 2
                if keep:
                    continue
                curve.ctrl.pop(point_index)
                rest = chain.get("ctrl0")
                if rest is not None and len(rest) == len(curve.ctrl) + 1:
                    rest.pop(point_index)
                self.shift_point_indices(chain, removed=point_index)
            curve.rebuild()

        self.sel = set()
        self.active = (-1, -1)
        self.build_mirror_maps()
        self.apply_deform()

    # ------------------------------------------------------------------ #
    # picking
    # ------------------------------------------------------------------ #
    def point_screen(self, context, chain, point):
        return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, Vector(point))

    def pick_point(self, context, mouse):
        prefs = get_prefs(context)
        radius = prefs.hit_radius if prefs else 14
        best = (-1, -1)
        best_d = float(radius * radius)
        for chain_index, chain in enumerate(self.chains):
            for point_index, point in enumerate(chain["curve"].ctrl):
                screen = self.point_screen(context, chain, point)
                if screen is None:
                    continue
                d = (screen.x - mouse[0]) ** 2 + (screen.y - mouse[1]) ** 2
                if d < best_d:
                    best_d = d
                    best = (chain_index, point_index)
        return best

    def pick_curve(self, context, mouse):
        prefs = get_prefs(context)
        radius = prefs.hit_radius if prefs else 14
        region, rv3d = context.region, context.region_data
        best = None
        best_d = float(radius * radius)
        for chain_index, chain in enumerate(self.chains):
            samples = chain["curve"].samples
            for i in range(len(samples) - 1):
                p1 = view3d_utils.location_3d_to_region_2d(region, rv3d, samples[i])
                p2 = view3d_utils.location_3d_to_region_2d(region, rv3d, samples[i + 1])
                if p1 is None or p2 is None:
                    continue
                seg = p2 - p1
                length = seg.length
                if length < 1e-6:
                    continue
                direction = seg / length
                t = max(0.0, min(length, (Vector(mouse) - p1).dot(direction)))
                d = (Vector(mouse) - (p1 + direction * t)).length
                if d < best_d:
                    best_d = d
                    best = (chain_index, i)
        return best

    def select_rect(self, context, rect, mode="REPLACE"):
        """Select the handles inside a box: REPLACE, ADD (Shift) or SUBTRACT (Ctrl)."""
        x1, y1, x2, y2 = rect
        xmin, xmax = sorted((x1, x2))
        ymin, ymax = sorted((y1, y2))
        found = set()
        for chain_index, chain in enumerate(self.chains):
            for point_index, point in enumerate(chain["curve"].ctrl):
                screen = self.point_screen(context, chain, point)
                if screen is None:
                    continue
                if xmin <= screen.x <= xmax and ymin <= screen.y <= ymax:
                    found.add((chain_index, point_index))
        if mode == "ADD":
            self.sel |= found
        elif mode == "SUBTRACT":
            self.sel -= found
        else:
            self.sel = found
        if len(found) == 1 and mode != "SUBTRACT":
            self.active = next(iter(found))
        elif not found and mode == "REPLACE":
            self.active = (-1, -1)

    # ------------------------------------------------------------------ #
    # moving control points
    # ------------------------------------------------------------------ #
    def mouse_on_plane(self, context, plane_point, plane_normal, mouse):
        region, rv3d = context.region, context.region_data
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
        if origin is None or direction is None:
            return None
        denom = direction.dot(plane_normal)
        if abs(denom) < 1e-8:
            return None
        t = (Vector(plane_point) - origin).dot(plane_normal) / denom
        return origin + direction * t

    def begin_move(self, context, mouse, kind):
        self.push_undo()
        chain_index, point_index = self.active
        if chain_index < 0 or not self.sel:
            return False
        chain = self.chains[chain_index]
        reference = Vector(chain["curve"].ctrl[point_index])
        hit = self.mouse_on_plane(context, reference, chain["frame"].n, mouse)
        if hit is None:
            return False
        self.move = {
            "kind": kind,
            "reference": reference,
            "start": hit,
            "store": [[p.copy() for p in c["curve"].ctrl] for c in self.chains],
            "axis": None,
            "moved": False,
        }
        return True

    def update_move(self, context, mouse):
        move = getattr(self, "move", None)
        if not move:
            return
        chain_index, point_index = self.active
        if chain_index < 0:
            return
        chain = self.chains[chain_index]
        frame = chain["frame"]
        hit = self.mouse_on_plane(context, move["reference"], frame.n, mouse)
        if hit is None:
            return
        delta = hit - move["start"]
        if move["axis"] == "U":
            delta = frame.u * delta.dot(frame.u)
        elif move["axis"] == "V":
            delta = frame.v * delta.dot(frame.v)

        touched = set()
        new_positions = {}
        axis = core.AXIS_INDEX.get(self.mirror_axis, 0)
        pivot = getattr(self, "mirror_pivot_local", None)
        plane = pivot[axis] if pivot is not None else 0.0
        for sel_chain, sel_point in self.sel:
            new = Vector(move["store"][sel_chain][sel_point]) + delta
            if self.flatten:
                new = self.chains[sel_chain]["frame"].project(new)
            if self.mirror:
                local = self.matrix_inv @ new
                if abs(local[axis] - plane) < 1e-5:
                    local[axis] = plane          # a handle on the plane stays on it
                    new = self.matrix_world @ local
            new_positions[(sel_chain, sel_point)] = new
            touched.add(sel_chain)

        for (sel_chain, sel_point), position in new_positions.items():
            self.chains[sel_chain]["curve"].ctrl[sel_point] = position
            self.owned.add((self.chains[sel_chain]["indices"][0], sel_point))

        for sel_chain in touched:
            self.chains[sel_chain]["curve"].rebuild()

        self.apply_mirror_to_controls()
        for chain in self.chains:
            chain["curve"].rebuild()

        move["moved"] = True
        self.apply_deform()

    def cancel_move(self):
        move = getattr(self, "move", None)
        if move:
            for i, ctrl in enumerate(move["store"]):
                if i < len(self.chains):
                    self.chains[i]["curve"].ctrl = [p.copy() for p in ctrl]
                    self.chains[i]["curve"].rebuild()
        self.move = None
        self.apply_deform()

    def lock_axis(self, context, letter):
        move = getattr(self, "move", None)
        chain_index, _point_index = self.active
        if not move or chain_index < 0:
            return
        frame = self.chains[chain_index]["frame"]
        axes = core.space_axes(context, self.obj, core.SPACE_GLOBAL)
        world_axis = axes[core.AXIS_INDEX.get(letter, 2)]
        du = abs(world_axis.dot(frame.u))
        dv = abs(world_axis.dot(frame.v))
        dn = abs(world_axis.dot(frame.n))
        if dn >= max(du, dv):
            new_axis = None
        elif du >= dv:
            new_axis = "U"
        else:
            new_axis = "V"
        move["axis"] = None if move["axis"] == new_axis else new_axis

    # ------------------------------------------------------------------ #
    # overlay
    # ------------------------------------------------------------------ #
    def describe_space(self, hud=None):
        if self.space_mode == core.SPACE_SCREEN:
            return hud("space_screen") if hud else "Screen (View)"
        key = {"GLOBAL": "space_global", "LOCAL": "space_local",
               "CURSOR": "space_cursor"}.get(self.space_mode, "space_global")
        name = hud(key) if hud else self.space_mode.capitalize()
        return "%s %s" % (name, self.axis)

    def describe_pivot(self, hud=None):
        keys = {
            core.PIVOT_CENTROID: "pivot_centroid",
            core.PIVOT_ORIGIN: "pivot_origin",
            core.PIVOT_CURSOR: "pivot_cursor",
            core.PIVOT_WORLD: "pivot_world",
        }
        if hud:
            return hud(keys.get(self.pivot, "pivot_centroid"))
        return self.pivot.capitalize()

    def hud_rows(self, prefs):
        """Structured help: (kind, content) rows, rendered bottom left."""
        lang = getattr(prefs, "hud_lang", "RU")
        t = lambda key: i18n.hud(lang, key)  # noqa: E731
        on = t("on")
        off = t("off")
        state = lambda value: on if value else off  # noqa: E731

        status = [
            "%s: %s" % (t("space"), self.describe_space(t)),
            "%s: %s" % (t("pivot"), self.describe_pivot(t)),
            "%s: %d%s" % (t("points"), self.points, t("auto_suffix") if self.auto_points else ""),
            "%s: %d" % (t("curves"), len(self.chains)),
            "%s: %.2f" % (t("roundness"), self.roundness),
            "%s: %.2f" % (t("strength"), self.strength),
        ]
        detail = t("on") if self.interpolate_loops else off
        toggles = [
            "%s: %s" % (t("even_spacing"), state(self.even_spacing)),
            "%s: %s" % (t("equal_gaps"), state(self.equal_gaps)),
            "%s: %s" % (t("project"), state(self.flatten)),
        ]
        if MIRROR_ENABLED:
            toggles.append("%s: %s %s" % (t("mirror"), state(self.mirror),
                                          self.mirror_axis if self.mirror else ""))
        toggles.append("%s: %s" % (t("interpolate"), detail))

        mouse = [
            (t("k_lmb_empty"), t("a_deselect")),
            (t("k_drag"), t("a_bend")),
            (t("k_shift"), t("a_add_sel")),
            (t("k_ctrl"), t("a_sub_sel")),
            (t("k_rect"), t("a_rect")),
            (t("k_ctrl_curve"), t("a_point")),
            (t("k_rmb"), t("a_confirm")),
            (t("k_wheel"), t("a_count")),
            (t("k_smooth"), t("a_smooth")),
        ]
        keys = [
            ("G", t("a_grab")),
            (t("k_arrows_ud"), t("a_roundness")),
            (t("k_arrows_lr"), t("a_strength")),
            ("Space", t("a_even")),
            ("F", t("a_reset_points")),
            ("Shift+F", t("a_flatten")),
            ("I", t("a_interpolate")),
            ("1 2 3 4", t("a_space")),
            ("X Y Z", t("a_axis")),
            ("Shift+X/Y/Z", t("a_lock")),
            ("P", t("a_pivot")),
            ("R / H", t("a_rh")),
            ("Del", t("a_del")),
            ("Ctrl+Z", t("a_undo")),
            ("Enter / Esc", t("a_ok_cancel")),
        ]

        rows = [
            ("title", t("title"), t("author")),
            ("info", "    ".join(status[:3]), None),
            ("info", "    ".join(status[3:]), None),
            ("info", "    ".join(toggles[:2]), None),
            ("info", "    ".join(toggles[2:]), None),
            ("rule", None, None),
            ("cols", (t("mouse"), mouse), (t("keys"), keys)),
        ]
        if not getattr(prefs, "hud_show_keys", True):
            rows = rows[:5]
        return rows

    @staticmethod
    def _text_width(font, text, size):
        try:
            blf.size(font, size)
            return blf.dimensions(font, text)[0]
        except Exception:
            return len(text) * size * 0.55

    # -- 3D overlay helpers -------------------------------------------- #
    def handle_quad(self, world, size_px):
        """A screen facing square of ``size_px`` pixels around a 3D point."""
        region, rv3d = self.region, self.region_data
        if region is None or rv3d is None:
            return None
        screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
        if screen is None:
            return None
        right = view3d_utils.region_2d_to_location_3d(region, rv3d, (screen.x + size_px * 0.5, screen.y), world)
        up = view3d_utils.region_2d_to_location_3d(region, rv3d, (screen.x, screen.y + size_px * 0.5), world)
        if right is None or up is None:
            return None
        dx = right - world
        dy = up - world
        return [world - dx - dy, world + dx - dy, world + dx + dy, world - dx + dy]

    def draw_handles(self, shaders, positions, color, size_px):
        """Screen sized square handles (GL point sizes are unreliable)."""
        if not positions:
            return
        tris = []
        for world in positions:
            quad = self.handle_quad(world, size_px)
            if quad is None:
                continue
            tris.extend((quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]))
        if not tris:
            return
        batch = batch_for_shader(shaders["solid"], "TRIS", {"pos": tris})
        shaders["solid"].bind()
        shaders["solid"].uniform_float("color", color)
        batch.draw(shaders["solid"])

    def rect_corners_3d(self):
        """The selection box as 3D points (keeps it exactly under the mouse)."""
        rect = getattr(self, "rect", None)
        region, rv3d = self.region, self.region_data
        if not rect or region is None or rv3d is None:
            return None
        x1, y1, x2, y2 = rect
        depth = self.rect_depth
        if depth is None:
            return None
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        points = []
        for corner in corners:
            point = view3d_utils.region_2d_to_location_3d(region, rv3d, corner, depth)
            if point is None:
                return None
            points.append(point)
        return points

    def draw_selection_box(self, shaders):
        points = self.rect_corners_3d()
        if not points:
            return
        _draw_lines(shaders, points, (1.0, 1.0, 1.0, 0.95), 1.0)

    def hud_layout(self, prefs):
        """Lay the HUD out -> (lines, width, height); lines are draw items."""
        font = 0
        size = max(9, int(getattr(prefs, "hud_size", 15)))
        title_size = int(round(size * 1.3))
        line = int(round(size * 1.55))
        pad = int(round(size * 0.9))
        gap = int(round(size * 0.8))
        width_of = lambda text, sz: self._text_width(font, text, sz)  # noqa: E731

        lines = []
        for kind, a, b in self.hud_rows(prefs):
            if kind == "title":
                lines.append({"h": int(line * 1.5), "items": [
                    ("title", a, title_size, (1.0, 0.73, 0.30, 1.0), 0.0),
                    ("right", b, int(size * 0.9), (0.62, 0.66, 0.74, 1.0), 0.0),
                ]})
            elif kind == "info":
                lines.append({"h": line, "items": [("info", a, size, (0.86, 0.89, 0.95, 1.0), 0.0)]})
            elif kind == "rule":
                lines.append({"h": int(line * 0.8), "items": [], "rule": True})
            elif kind == "cols":
                (head_l, pairs_l), (head_r, pairs_r) = (a, b)
                key_w_l = max([width_of(k, size) for k, _v in pairs_l] + [width_of(head_l, size)])
                key_w_r = max([width_of(k, size) for k, _v in pairs_r] + [width_of(head_r, size)])
                value_w_l = max([width_of(v, size) for _k, v in pairs_l] + [0.0])
                value_w_r = max([width_of(v, size) for _k, v in pairs_r] + [0.0])
                col_l = key_w_l + gap + value_w_l
                col_r_start = col_l + int(size * 2.2)
                col_r = key_w_r + gap + value_w_r
                total_w = col_r_start + col_r

                lines.append({"h": int(line * 1.15), "items": [
                    ("head", head_l, size, (0.55, 0.78, 1.0, 1.0), 0.0),
                    ("head", head_r, size, (0.55, 0.78, 1.0, 1.0), col_r_start),
                ]})
                for i in range(max(len(pairs_l), len(pairs_r))):
                    items = []
                    if i < len(pairs_l):
                        key, value = pairs_l[i]
                        items.append(("key", key, size, (0.98, 0.80, 0.45, 1.0), 0.0))
                        items.append(("value", value, size, (0.88, 0.90, 0.95, 1.0), key_w_l + gap))
                    if i < len(pairs_r):
                        key, value = pairs_r[i]
                        items.append(("key", key, size, (0.98, 0.80, 0.45, 1.0), col_r_start))
                        items.append(("value", value, size, (0.88, 0.90, 0.95, 1.0),
                                      col_r_start + key_w_r + gap))
                    lines.append({"h": line, "items": items})

        def line_width(entry):
            left = 0.0
            right = 0.0
            for item in entry["items"]:
                if item[0] == "right":
                    right = max(right, width_of(item[1], item[2]))
                else:
                    left = max(left, item[4] + width_of(item[1], item[2]))
            if right:
                left = left + int(size * 3) + right
            return left

        content_w = max([line_width(entry) for entry in lines] + [0.0])
        width = int(content_w) + pad * 2
        height = sum(entry["h"] for entry in lines) + pad * 2
        return lines, width, height

    @staticmethod
    def hud_insets(context):
        """How far the *usable* viewport is inset inside the draw space.

        The POST_PIXEL/blf pixel space of a 3D View draw handler covers the whole
        area, and the native regions (tool bar, header, sidebar) are painted over
        its edges - so the help has to be pushed inside by their sizes.
        """
        left = right = top = 0
        area = getattr(context, "area", None)
        if area is None:
            return left, right, top
        region = getattr(context, "region", None)
        for other in area.regions:
            if other is region or (other.width <= 1 and other.height <= 1):
                continue
            if other.type in ("TOOLS", "TOOLBAR"):
                left = max(left, other.width)
            elif other.type == "UI":
                right = max(right, other.width)
            elif other.type in ("HEADER", "TOOL_HEADER", "ASSET_SHELF_HEADER"):
                top += other.height
        return left, right, top

    def draw_hud(self, context, prefs, shaders):
        font = 0
        lines, width, height = self.hud_layout(prefs)
        size = max(9, int(getattr(prefs, "hud_size", 15)))
        margin_x, margin_y = int(size * 1.7), int(size * 1.7)
        corner = getattr(prefs, "hud_corner", "BOTTOM_LEFT")
        area = getattr(context, "area", None)
        space_w = getattr(area, "width", 0) or 800
        space_h = getattr(area, "height", 0) or 600
        inset_l, inset_r, inset_t = self.hud_insets(context)
        x0 = (inset_l + margin_x if corner.endswith("LEFT")
              else space_w - inset_r - width - margin_x)
        y0 = (margin_y if corner.startswith("BOTTOM")
              else space_h - inset_t - height - margin_y)
        try:
            _LAST_DRAW.update({
                "insets": (inset_l, inset_r, inset_t),
                "area_size": (space_w, space_h),
                "box": (x0, y0, width, height),
            })
        except Exception as exc:  # pragma: no cover - diagnostics only
            _LAST_DRAW["error"] = str(exc)

        def draw_text(text, px, py, text_size, color, bold=False):
            blf.size(font, text_size)
            blf.color(font, color[0], color[1], color[2], color[3])
            blf.position(font, px, py, 0)
            blf.draw(font, text)
            if bold:  # fake bold: the same text half a pixel further right
                blf.position(font, px + 1, py, 0)
                blf.draw(font, text)

        gpu.state.blend_set("ALPHA")
        _draw_rect(shaders, x0, y0, x0 + width, y0 + height, (0.04, 0.045, 0.06, 0.80))
        _draw_rect(shaders, x0, y0, x0 + max(3, int(size * 0.22)), y0 + height, (1.0, 0.66, 0.18, 0.85))
        _draw_rect_outline(shaders, x0, y0, x0 + width, y0 + height, (1.0, 0.66, 0.18, 0.35))

        y = y0 + height - int(size * 0.9)
        for entry in lines:
            y -= entry["h"]
            text_y = y + (entry["h"] - size) * 0.5
            if entry.get("rule"):
                _draw_rect(shaders, x0 + int(size * 1.1), y + entry["h"] * 0.5,
                           x0 + width - int(size * 0.9), y + entry["h"] * 0.5 + 1,
                           (1.0, 0.66, 0.18, 0.30))
                continue
            for item in entry["items"]:
                kind, text = item[0], item[1]
                text_size, color, offset = item[2], item[3], item[4]
                if kind == "right":
                    px = x0 + width - int(size * 1.1) - self._text_width(font, text, text_size)
                    draw_text(text, px, y + (entry["h"] - text_size) * 0.5, text_size, color)
                else:
                    bold = kind in ("title", "head")
                    draw_text(text, x0 + int(size * 1.1) + offset, text_y, text_size, color, bold)
        gpu.state.blend_set("NONE")

    def draw_2d(self, context, prefs):
        shaders = get_shader_set()
        if shaders is None:
            return
        area = getattr(context, "area", None)
        if area is None or area != self.area:
            return
        try:
            if getattr(self, "hidden", False):
                return
            # while a selection box is dragged the help block would cover it
            if not getattr(self, "rect", None):
                self.draw_hud(context, prefs, shaders)
        except Exception as exc:
            _LAST_DRAW["error"] = "%s: %s" % (type(exc).__name__, exc)

    def draw_3d(self, context, prefs):
        shaders = get_shader_set()
        if shaders is None:
            return
        area = getattr(context, "area", None)
        if area is None or area != self.area:
            return
        # the whole overlay is drawn *through* the mesh: a curve that lies on the
        # surface would z-fight with it and the box would sink into the scene
        try:
            gpu.state.depth_test_set("NONE")
            _LAST_DRAW["depth"] = gpu.state.depth_test_get()
        except Exception:
            pass
        try:
            gpu.state.blend_set("ALPHA")
            for chain_index, chain in enumerate(self.chains):
                frame = chain["frame"]
                curve = chain["curve"]
                if self.show_original:
                    _draw_lines(shaders, chain["world"], prefs.col_original, 1.0,
                                loop=chain["closed"] and len(chain["world"]) > 2)

                _draw_lines(shaders, curve.samples, prefs.col_curve, 2.0)

                # work plane cross
                size = max(0.15, max((w - frame.origin).length for w in chain["world"]) * 1.4)
                cross = [frame.from_plane(-size, 0.0, 0.0), frame.from_plane(size, 0.0, 0.0)]
                _draw_lines(shaders, cross, (0.6, 0.6, 0.6, 0.55), 1.0)
                cross = [frame.from_plane(0.0, -size, 0.0), frame.from_plane(0.0, size, 0.0)]
                _draw_lines(shaders, cross, (0.6, 0.6, 0.6, 0.55), 1.0)

                default_points = []
                selected_points = []
                active_point = None
                for point_index, point in enumerate(curve.ctrl):
                    if (chain_index, point_index) == self.active:
                        active_point = point
                    elif (chain_index, point_index) in self.sel:
                        selected_points.append(point)
                    else:
                        default_points.append(point)

                self.draw_handles(shaders, default_points, prefs.col_point, prefs.point_size)
                self.draw_handles(shaders, selected_points, prefs.col_point_selected,
                                  prefs.point_size_selected)
                if active_point is not None:
                    self.draw_handles(shaders, [active_point], prefs.col_point_active,
                                      prefs.point_size_active)

            self.draw_selection_box(shaders)
            gpu.state.blend_set("NONE")
        except Exception:
            disable_overlay()
        finally:
            try:
                gpu.state.depth_test_set("LESS_EQUAL")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # start / finish
    # ------------------------------------------------------------------ #
    def add_handlers(self, context):
        cls = self.__class__
        cls.remove_handlers()
        prefs = get_prefs(context) or FallbackPrefs()
        self.area = context.area
        self.region = context.region
        self.region_data = context.region_data
        self.rect_depth = self.chains[0]["anchor"] if self.chains else None
        self.hidden = False
        self.rect = None
        self.move = None
        self.pending = None
        self.tension_track = None
        cls._handle_3d = SpaceView3D.draw_handler_add(self.draw_3d, (context, prefs), DRAW_SPACE, "POST_VIEW")
        cls._handle_2d = SpaceView3D.draw_handler_add(self.draw_2d, (context, prefs), DRAW_SPACE, "POST_PIXEL")
        _DRAW_HANDLES.append((cls._handle_3d, DRAW_SPACE))
        _DRAW_HANDLES.append((cls._handle_2d, DRAW_SPACE))

    def cleanup(self):
        self.__class__.remove_handlers()
        if getattr(self, "session", None) is not None:
            self.session.free()
            self.session = None
        self.move = None
        self.pending = None
        redraw_view3d()


# --------------------------------------------------------------------------- #
# interactive operators
# --------------------------------------------------------------------------- #

class FFLEX_OT_flex(FFLEX_OT_flex_base):
    bl_idname = "fflex.flex"
    bl_label = "Flow Flex"
    bl_description = ("Interactively fit the selected edge loops onto an editable imaginary curve "
                      "(screen space or global / local / 3D cursor axes)"
                      "\nShift+Wheel / Alt+Wheel: control points, Space: even spacing")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, event):
        if context.area is None or context.area.type != VIEW3D_TYPE:
            self.report({"WARNING"}, "Open the 3D View first")
            return {"CANCELLED"}

        self.sync_from_settings(context)
        self.configure()
        if not self.setup(context):
            self.report({"WARNING"}, self.error or "FFlex could not start")
            return {"CANCELLED"}

        self.apply_deform()
        self.add_handlers(context)
        context.window_manager.modal_handler_add(self)
        redraw_view3d()
        return {"RUNNING_MODAL"}

    def configure(self):
        """Called before setup - overridden by the instant version."""
        self.iterations = 1

    # ------------------------------------------------------------------ #
    def modal(self, context, event):
        """Safety net: a bad event must never leave the tool half alive."""
        try:
            return self.modal_step(context, event)
        except Exception:
            traceback.print_exc()
            try:
                self.report({"ERROR"}, "FFlex: unexpected error (see the console)")
            except Exception:
                pass
            return self.cancel(context)

    def modal_step(self, context, event):
        if context.area is None or context.area.type != VIEW3D_TYPE:
            return {"RUNNING_MODAL"}

        mouse = (event.mouse_region_x, event.mouse_region_y)

        # hold E and slide the cursor right / left to set the tension of the blend
        if SHAPES_ENABLED and event.type == "E" and event.value == "PRESS":
            self.tension_track = {"x": event.mouse_region_x,
                                  "value": float(getattr(self, "interp_tension", 1.0))}
            self.report({"INFO"}, "FFlex: tension %.2f" % self.interp_tension)
            redraw_view3d()
            return {"RUNNING_MODAL"}
        if event.type == "E" and event.value == "RELEASE":
            self.tension_track = None
            return {"RUNNING_MODAL"}
        track = getattr(self, "tension_track", None) if SHAPES_ENABLED else None
        if track is not None and event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            span = 320.0
            value = track["value"] + (event.mouse_region_x - track["x"]) / span
            self.set_interp_tension(context, max(0.0, min(1.0, value)))
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE":
            if self.pending and self.pending["kind"] == "rect":
                self.rect = (self.pending["mouse"][0], self.pending["mouse"][1], mouse[0], mouse[1])
                redraw_view3d()
            elif self.move:
                self.update_move(context, mouse)
                redraw_view3d()
            elif self.pending and self.pending["kind"] == "click":
                # a click only becomes a drag once the mouse really moves
                dx = mouse[0] - self.pending["mouse"][0]
                dy = mouse[1] - self.pending["mouse"][1]
                if dx * dx + dy * dy > 9.0:
                    self.pending["kind"] = "rect"
                    self.rect = (self.pending["mouse"][0], self.pending["mouse"][1], mouse[0], mouse[1])
                    redraw_view3d()
            elif self.pending and self.pending["kind"] == "drag":
                dx = mouse[0] - self.pending["mouse"][0]
                dy = mouse[1] - self.pending["mouse"][1]
                if dx * dx + dy * dy > 9.0:
                    if self.begin_move(context, self.pending["mouse"], "drag"):
                        self.update_move(context, mouse)
                        redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if event.value == "PRESS":
                if event.alt:
                    return {"PASS_THROUGH"}
                hit = self.pick_point(context, mouse)

                if hit[0] >= 0:
                    if event.shift:
                        self.sel.add(hit)
                        self.active = hit
                    elif event.ctrl:
                        self.sel.discard(hit)
                        if self.active == hit:
                            self.active = (-1, -1)
                    else:
                        if hit not in self.sel:
                            self.sel = {hit}
                        self.active = hit
                        self.pending = {"kind": "drag", "mouse": mouse}
                    redraw_view3d()
                    return {"RUNNING_MODAL"}

                if self.move:
                    self.move = None
                    return {"RUNNING_MODAL"}
                # empty space (or a spot on the curve): wait and see whether this is a
                # click - then Ctrl adds a control point - or a dragged selection box
                mode = "ADD" if event.shift else ("SUBTRACT" if event.ctrl else "REPLACE")
                self.pending = {"kind": "click", "mouse": mouse, "mode": mode}
                self.rect = None
                return {"RUNNING_MODAL"}

            if event.value == "RELEASE":
                pending = self.pending
                self.pending = None
                if pending and pending["kind"] in ("rect", "click"):
                    self.rect = None
                    mode = pending.get("mode", "REPLACE")
                    if pending["kind"] == "rect":
                        # the press already turned into a drag: select the box
                        self.select_rect(context, (pending["mouse"][0], pending["mouse"][1],
                                                   mouse[0], mouse[1]), mode)
                    elif mode == "SUBTRACT":
                        # Ctrl + click on the curve (no handle hit) adds a control point
                        if self.pick_point(context, pending["mouse"])[0] < 0:
                            self.add_point(context, pending["mouse"])
                    elif mode == "REPLACE":
                        # a plain click on empty space: drop the selection (never
                        # apply - a stray click must not end the tool)
                        self.sel = set()
                        self.active = (-1, -1)
                        prefs = get_prefs(context)
                        if prefs is not None and getattr(prefs, "left_click_empty", "DESELECT") == "CONFIRM":
                            return self.finish(context)
                self.move = None
                redraw_view3d()
                return {"RUNNING_MODAL"}

        if event.value != "PRESS":
            return {"RUNNING_MODAL"}

        # ---- keys -------------------------------------------------------- #
        if event.type == "G":
            if self.sel:
                if self.active[0] < 0:
                    self.active = next(iter(self.sel))
                self.begin_move(context, mouse, "grab")
            return {"RUNNING_MODAL"}

        if event.type in ("RET", "NUMPAD_ENTER", "TAB"):
            return self.finish(context)

        if event.type == "ESC":
            return self.cancel(context)

        if event.ctrl and event.type == "Z":
            # undo the last thing done to the curves, without leaving the tool
            if self.move:
                self.cancel_move()
            else:
                self.undo(context)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "RIGHTMOUSE":
            if event.alt:
                return {"PASS_THROUGH"}
            if self.move:
                # a right click in the middle of a drag only drops that drag
                self.cancel_move()
                redraw_view3d()
                return {"RUNNING_MODAL"}
            prefs = get_prefs(context)
            action = getattr(prefs, "right_click_action", "CONFIRM") if prefs else "CONFIRM"
            if action == "RESET":
                self.reset_deform(context)
                redraw_view3d()
                return {"RUNNING_MODAL"}
            if action == "NOTHING":
                return {"RUNNING_MODAL"}
            return self.finish(context)

        if event.type == "DEL":
            if self.sel:
                self.delete_points(context, list(self.sel))
                redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type in ("UP_ARROW", "DOWN_ARROW"):
            self.set_roundness(context, self.roundness + (0.1 if event.type == "UP_ARROW" else -0.1))
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type in ("LEFT_ARROW", "RIGHT_ARROW"):
            self.set_strength(context, self.strength + (0.05 if event.type == "RIGHT_ARROW" else -0.05))
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "SPACE":
            self.set_even_spacing(context, not self.even_spacing)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "I":
            if event.shift:
                if SHAPES_ENABLED:
                    self.set_gap_shape(context)  # the selected gaps
            else:
                self.set_interpolate(context, not self.interpolate_loops)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "C" and SHEET_ENABLED:
            self.set_interp_sheet(context, not self.interp_sheet)
            return {"RUNNING_MODAL"}

        if event.type == "F":
            if event.shift:
                self.set_flatten(context, not self.flatten)
            else:
                self.reset_selected_points(context)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type in ("ONE", "TWO", "THREE", "FOUR") and event.value == "PRESS":
            mapping = {"ONE": core.SPACE_SCREEN, "TWO": core.SPACE_GLOBAL,
                       "THREE": core.SPACE_LOCAL, "FOUR": core.SPACE_CURSOR}
            self.space_mode = mapping[event.type]
            self.remember_frame(context)
            self.rebuild_frames(context)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type in ("X", "Y", "Z"):
            if event.alt:
                # Alt + axis switches the symmetry (the same axis turns it off)
                self.set_mirror(context, event.type)
                return {"RUNNING_MODAL"}
            if event.shift and self.move:
                self.lock_axis(context, event.type)
                return {"RUNNING_MODAL"}
            self.axis = event.type
            self.remember_frame(context)
            self.rebuild_frames(context)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "P":
            order = [core.PIVOT_CENTROID, core.PIVOT_ORIGIN, core.PIVOT_CURSOR, core.PIVOT_WORLD]
            index = order.index(self.pivot) if self.pivot in order else 0
            self.pivot = order[(index + 1) % len(order)]
            self.remember_frame(context)
            self.rebuild_frames(context)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "M":
            self.set_mirror(context)
            return {"RUNNING_MODAL"}

        if event.type == "H":
            self.hidden = not self.hidden
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "V":
            if self.space_mode == core.SPACE_SCREEN:
                self.rebuild_frames(context)
                redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type == "R":
            self.reset_deform(context)
            redraw_view3d()
            return {"RUNNING_MODAL"}

        if event.type in ("WHEELUPMOUSE", "WHEELDOWNMOUSE"):
            step = 1 if event.type == "WHEELUPMOUSE" else -1
            if event.alt:
                # Alt+wheel: transition smoothing (0 ... 8). This used to rebuild the
                # curve from the original loop, which threw away a half finished bend.
                self.set_interp_smooth(context, self.interp_smooth + step)
                return {"RUNNING_MODAL"}
            if event.shift:
                # Shift+wheel: how many control points the curve has
                self.rebuild_control_points(context, from_original=False,
                                            count=self.points + step)
                self.refresh_params()
                self.apply_deform()
                redraw_view3d()
                return {"RUNNING_MODAL"}

        if event.type in PASS_THROUGH_KEYS:
            return {"PASS_THROUGH"}

        return {"RUNNING_MODAL"}

    # ------------------------------------------------------------------ #
    def snapshot(self):
        """A compact copy of everything this tool can change about the curves."""
        return {
            "ctrl": [[p.copy() for p in chain["curve"].ctrl] for chain in self.chains],
            "ctrl0": [[p.copy() for p in chain["ctrl0"]] for chain in self.chains],
            "source": [chain["source"] for chain in self.chains],
            "points": self.points,
            "roundness": self.roundness,
            "strength": self.strength,
            "even_spacing": self.even_spacing,
            "flatten": self.flatten,
            "sel": set(self.sel),
            "active": tuple(self.active),
            "gap_shapes": dict(getattr(self, "gap_shapes", {})),
        }

    def push_undo(self):
        """Remember the state before one action (Ctrl+Z steps back through them)."""
        stack = getattr(self, "undo_stack", None)
        if stack is None:
            stack = self.undo_stack = []
        stack.append(self.snapshot())
        if len(stack) > 64:
            del stack[0]

    def undo(self, context):
        """Step back one action on the curves; the tool keeps running."""
        stack = getattr(self, "undo_stack", None)
        if not stack:
            self.report({"INFO"}, "FFlex: nothing to undo")
            return False
        state = stack.pop()
        for position, chain in enumerate(self.chains):
            ctrl = state["ctrl"][position]
            if len(ctrl) != len(chain["curve"].ctrl):
                continue
            chain["curve"].ctrl = [p.copy() for p in ctrl]
            chain["curve"].rebuild()
            original = state["ctrl0"][position]
            if len(original) == len(chain["curve"].ctrl):
                chain["ctrl0"] = [p.copy() for p in original]
            else:
                chain["ctrl0"] = [p.copy() for p in core.derive_control_points(
                    chain["source"], len(chain["curve"].ctrl), chain["closed"],
                    self.use_density)]
        self.points = state["points"]
        self.roundness = state["roundness"]
        self.strength = state["strength"]
        self.even_spacing = state["even_spacing"]
        self.flatten = state["flatten"]
        self.sel = set(state["sel"])
        self.active = tuple(state["active"])
        self.gap_shapes = dict(state.get("gap_shapes", {}))
        self.apply_deform()
        self.report({"INFO"}, "FFlex: undo")
        return True

    def finish(self, context):
        self.cleanup()
        self.report({"INFO"}, "FFlex: applied")
        return {"FINISHED"}

    def cancel(self, context):
        # every step is isolated: if one of them fails the others must still run,
        # otherwise leaving the tool would leave the mesh half restored
        steps = (("cancel_move", self.cancel_move),
                 ("clear_interpolation", lambda: self.clear_interpolation(commit=False)),
                 ("restore_original", self.restore_original),
                 ("cleanup", self.cleanup))
        for name, step in steps:
            try:
                step()
            except Exception:
                traceback.print_exc()
        self.interp_rest = {}
        self.undo_stack = []
        self.report({"INFO"}, "FFlex: cancelled")
        return {"CANCELLED"}


class FFLEX_OT_flex_apply(FFLEX_OT_flex_base):
    bl_idname = "fflex.flex_apply"
    bl_label = "Flow Flex (Apply)"
    bl_description = ("Fit the selected edge loops onto an imaginary curve in one go "
                      "(iterations repeat the fit - good for smoothing)")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        self.sync_from_settings(context)
        iterations = max(1, int(self.iterations))
        done = 0
        for _ in range(iterations):
            if not self.setup(context):
                if done == 0:
                    self.report({"WARNING"}, self.error or "FFlex could not run")
                    return {"CANCELLED"}
                break
            self.apply_deform()
            self.cleanup()
            done += 1
        redraw_view3d()
        self.report({"INFO"}, "FFlex: fitted %d loop(s)" % len(self.chains))
        return {"FINISHED"}


CLASSES = (FFLEX_OT_flex, FFLEX_OT_flex_apply)


def register():
    compat.register_classes(CLASSES)


def unregister():
    FFLEX_OT_flex_base.remove_handlers()
    compat.unregister_classes(CLASSES)
