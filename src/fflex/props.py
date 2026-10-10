# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""Settings (scene) and preferences (add-on) for FFlex."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, PropertyGroup

from . import compat, core

PKG = __package__ or "FFlex"

# --------------------------------------------------------------------------- #
# shared enum item tables
# --------------------------------------------------------------------------- #

SPACE_ITEMS = (
    (core.SPACE_SCREEN, "Screen (View)", "The imaginary curve is edited and fitted in screen space"),
    (core.SPACE_GLOBAL, "Global", "Work in global axes - pick the axis below"),
    (core.SPACE_LOCAL, "Local", "Work in the active object's local axes"),
    (core.SPACE_CURSOR, "3D Cursor", "Work in the 3D cursor's axes"),
)

AXIS_ITEMS = (
    ("Z", "Z", "Frame axis Z of the chosen space"),
    ("X", "X", "Frame axis X of the chosen space"),
    ("Y", "Y", "Frame axis Y of the chosen space"),
)

PIVOT_ITEMS = (
    (core.PIVOT_CENTROID, "Loop Centroid", "The frame passes through the centre of every loop"),
    (core.PIVOT_ORIGIN, "Object Origin", "The frame passes through the object origin"),
    (core.PIVOT_CURSOR, "3D Cursor", "The frame passes through the 3D cursor"),
    (core.PIVOT_WORLD, "World Origin", "The frame passes through the world origin"),
)

MEASURE_ITEMS = (
    (core.MEASURE_CHAIN, "Along Chain (3D)", "Equal 3D distance between the vertices along their own loop"),
    (core.MEASURE_PLANE, "In Axis Plane", "Equal distance measured inside the work frame plane"),
)

INTERP_SHAPE_ITEMS = (
    ("CUBIC", "Cubic (smooth)", "Catmull-Rom through the neighbouring curves: the surface "
                                "leaves and enters every curve with the same slope, so the "
                                "transition is smooth all the way"),
    ("QUADRATIC", "Quadratic", "Smooth (ease in / ease out) blend inside one gap only"),
    ("LINEAR", "Linear", "Straight ramp between the curves - every loop keeps its own share"),
    ("AUTO", "Auto", "Cubic for wide gaps, linear for narrow ones"),
)


# --------------------------------------------------------------------------- #
# scene settings
# --------------------------------------------------------------------------- #

def remember_frame(settings, context):
    """Remember space / axis / pivot in the preferences (they outlive the .blend)."""
    prefs = get_prefs(context)
    if prefs is None:
        return
    prefs.last_space = settings.space_mode
    prefs.last_axis = settings.axis
    prefs.last_pivot = settings.pivot


class FFlexSettings(PropertyGroup):
    """Everything the sidebar panel edits (saved with the .blend file)."""

    # ---- work space ------------------------------------------------------ #
    space_mode: EnumProperty(
        name="Space",
        description="Where the imaginary curve lives: screen space or one of the axis spaces",
        items=SPACE_ITEMS,
        default=core.SPACE_SCREEN,
        update=remember_frame,
    )
    axis: EnumProperty(
        name="Axis",
        description="Frame axis (the curve is edited in the plane perpendicular to it)",
        items=AXIS_ITEMS,
        default="Z",
        update=remember_frame,
    )
    pivot: EnumProperty(
        name="Pivot",
        description="Where the work frame plane passes through",
        items=PIVOT_ITEMS,
        default=core.PIVOT_CENTROID,
        update=remember_frame,
    )

    # ---- curve ----------------------------------------------------------- #
    points: IntProperty(
        name="Control Points",
        description="Number of control points of the imaginary curve",
        default=3, min=2, max=64,
    )
    auto_points: BoolProperty(
        name="Auto Points",
        description="Guess the number of control points so that the imaginary curve really "
                    "follows the selected loop instead of cutting corners across it "
                    "(the count is written back into Control Points)",
        default=True,
    )
    hug_tolerance: FloatProperty(
        name="Fit Tolerance",
        description="How far the imaginary curve may drift from the loop, as a share of the "
                    "loop length (0.02 = 2%)",
        default=0.02, min=0.002, max=0.25, precision=3, subtype="FACTOR",
    )
    roundness: FloatProperty(
        name="Roundness",
        description="1.0 keeps sharp corners, 2.0 rounds the curve off",
        default=1.0, min=1.0, max=2.0, precision=2, step=10,
    )
    segments: IntProperty(
        name="Curve Quality",
        description="Samples per control point span",
        default=12, min=3, max=48,
    )
    strength: FloatProperty(
        name="Strength",
        description="Blend between the original shape (0) and the fitted curve (1)",
        default=1.0, min=0.0, max=1.0, precision=2, step=5,
    )
    keep_offset: BoolProperty(
        name="Keep Offset",
        description="Off (default, same behaviour as Mio3 Flex): the vertices snap onto the "
                    "imaginary curve and follow its shape. On: the original shape is kept and "
                    "only travels together with the curve",
        default=False,
    )
    flatten: BoolProperty(
        name="Project to Plane",
        description="Fit the loop inside the work frame plane (flattens it onto the axis plane)",
        default=False,
    )
    use_density: BoolProperty(
        name="Vertex Density",
        description="Place control points where the mesh is dense instead of evenly",
        default=False,
    )
    even_spacing: BoolProperty(
        name="Even Spacing",
        description="Distribute the vertices with equal arc length along the curve "
                    "(LoopTools 'Space' analogue)",
        default=False,
    )
    equal_gaps: BoolProperty(
        name="Equal Gaps",
        description="Relax the spacing until the straight line gaps between the points are "
                    "exactly equal (corners get cut slightly). Off = the distance measured "
                    "along the curve / loop is equal",
        default=True,
    )
    split_ribbon: BoolProperty(
        name="Split Ribbons",
        description="A closed loop made of two long sides joined by two short ends (the "
                    "boundary of a ribbon) is split into two separate curves, so both rails "
                    "can be shaped on their own (K in the interactive tool)",
        default=True,
    )
    pinch_ratio: FloatProperty(
        name="Pinch Threshold",
        description="An edge this many times shorter than its neighbours counts as the end "
                    "of a ribbon",
        default=0.4, min=0.05, max=1.0,
    )
    corner_angle: FloatProperty(
        name="Corner Angle",
        description="Fallback for Split Ribbons: a closed loop with exactly two sharper "
                    "turns than this is split at them",
        default=45.0, min=5.0, max=170.0,
    )
    mirror: BoolProperty(
        name="Mirror",
        description="Mirror the deformation on the object's local axis picked below "
                    "(Alt + X/Y/Z in the interactive tool)",
        default=False,
    )
    mirror_axis: EnumProperty(
        name="Mirror Axis",
        description="Local axis of the symmetry",
        items=AXIS_ITEMS,
        default="X",
    )
    interpolate_loops: BoolProperty(
        name="Interpolate Between Curves",
        description="Also move the edge loops that lie between two fitted loops, blending "
                    "them from both curves (I in the interactive tool); loops outside the "
                    "fitted band are left alone",
        default=False,
    )
    interp_shape: EnumProperty(
        name="Blend Shape",
        description="How the loops between two curves are mixed. Cubic is smooth through the "
                    "curves, linear keeps an even ramp. Shift+I in the interactive tool "
                    "changes the gaps of the selected curves only",
        items=INTERP_SHAPE_ITEMS,
        default="CUBIC",
    )
    interp_tension: FloatProperty(
        name="Tension",
        description="How strongly the blend eases in and out between the curves: "
                    "0 gives a straight ramp (linear), 1 the smooth step. In the "
                    "interactive tool hold E and slide the cursor right / left",
        default=1.0, min=0.0, max=1.0,
    )
    interp_smooth: IntProperty(
        name="Transition Smoothing",
        description="Relaxation passes over the shift field. This is what evens the shift "
                    "out along every loop, so a straight loop stays straight instead of "
                    "copying the bend of a neighbouring curve (0 turns it off)",
        default=4, min=0, max=8,
    )
    interp_sheet: BoolProperty(
        name="Smooth Sheet Only",
        description="Keep the interpolation on one smooth sheet: the walk does not cross "
                    "sharp creases, so the rim of a thickness and the wall behind it stay "
                    "untouched (C in the interactive tool)",
        default=True,
    )
    interp_angle: FloatProperty(
        name="Crease Angle",
        description="How sharp an edge has to be to stop the walk (degrees)",
        default=40.0, min=0.0, max=180.0,
    )
    interp_facing: FloatProperty(
        name="Facing Limit",
        description="How far a loop may face away from its curve and still be dragged "
                    "along. The rim of a thickness faces sideways and the wall behind it "
                    "faces backwards, so both stay untouched (degrees)",
        default=70.0, min=0.0, max=180.0,
    )
    show_original: BoolProperty(
        name="Show Original",
        description="Draw the original loop while editing",
        default=True,
    )
    iterations: IntProperty(
        name="Iterations",
        description="How many times the instant fit is applied",
        default=1, min=1, max=32,
    )

    # ---- standalone even spacing ----------------------------------------- #
    space_measure: EnumProperty(
        name="Measure",
        description="How distances are measured for the even spacing operator",
        items=MEASURE_ITEMS,
        default=core.MEASURE_CHAIN,
    )
    space_extend: BoolProperty(
        name="Extend to Loop",
        description="Grow the selected vertices to the full edge loop before spacing",
        default=False,
    )


# --------------------------------------------------------------------------- #
# add-on preferences (viewport look and defaults)
# --------------------------------------------------------------------------- #

# keys offered for the add-on shortcut (the ones that make sense as a single press)
_KEY_NAMES = (
    [chr(code) for code in range(ord("A"), ord("Z") + 1)]
    + [str(number) for number in range(10)]
    + ["F%d" % number for number in range(1, 13)]
    + ["SPACE", "TAB", "RET", "ESC", "BACK_SPACE", "DEL", "INSERT", "HOME", "END",
       "PAGE_UP", "PAGE_DOWN", "UP_ARROW", "DOWN_ARROW", "LEFT_ARROW", "RIGHT_ARROW",
       "MINUS", "EQUAL", "LEFT_BRACKET", "RIGHT_BRACKET", "SEMI_COLON", "COMMA",
       "PERIOD", "SLASH", "BACK_SLASH", "QUOTE", "GRAVE"]
    + ["NUMPAD_%d" % number for number in range(10)]
)
KEYMAP_ITEMS = tuple((name, name, "") for name in _KEY_NAMES)

DEFAULT_KEY = "F"
DEFAULT_CTRL = True
DEFAULT_SHIFT = True
DEFAULT_ALT = False


def keymap_conflicts(context, prefs):
    """Names of the 3D view shortcuts that already use this key combination."""
    if context is None:
        return []
    try:
        keyconfig = context.window_manager.keyconfigs.user
        keymap = keyconfig.keymaps.get("3D View")
    except Exception:
        return []
    if keymap is None:
        return []
    names = []
    for item in keymap.keymap_items:
        if item.idname == "fflex.flex":
            continue
        if item.type != prefs.keymap_key:
            continue
        if bool(item.ctrl) != bool(prefs.keymap_ctrl):
            continue
        if bool(item.shift) != bool(prefs.keymap_shift):
            continue
        if bool(item.alt) != bool(prefs.keymap_alt):
            continue
        label = item.name or item.idname
        if label not in names:
            names.append(label)
    return names


def keymap_changed(self, context):
    """Rebuild the key map item as soon as the preference changes."""
    try:
        bpy.ops.fflex.reload_keymap()
    except Exception:  # pragma: no cover - during registration / headless
        pass


class FFlexPreferences(AddonPreferences):
    bl_idname = PKG

    # ---- shortcut ------------------------------------------------------- #
    keymap_enabled: BoolProperty(
        name="Add-on Shortcut",
        description="Register the shortcut below for starting FFlex. Turn it off if you "
                    "prefer to keep every shortcut to yourself",
        default=True,
        update=keymap_changed,
    )
    keymap_key: EnumProperty(
        name="Key",
        description="Key that starts FFlex in the 3D view",
        items=KEYMAP_ITEMS,
        default=DEFAULT_KEY,
        update=keymap_changed,
    )
    keymap_ctrl: BoolProperty(name="Ctrl", default=DEFAULT_CTRL, update=keymap_changed)
    keymap_shift: BoolProperty(name="Shift", default=DEFAULT_SHIFT, update=keymap_changed)
    keymap_alt: BoolProperty(name="Alt", default=DEFAULT_ALT, update=keymap_changed)
    # what the tool was set to last time, so the next run starts with the same
    # space / axis / pivot instead of the defaults (kept in the preferences, which
    # survive a new file and a Blender restart)
    last_space: StringProperty(name="Last Space", default="", options={"HIDDEN"})
    last_axis: StringProperty(name="Last Axis", default="", options={"HIDDEN"})
    last_pivot: StringProperty(name="Last Pivot", default="", options={"HIDDEN"})

    col_curve: FloatVectorProperty(
        name="Curve", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.25, 0.75, 1.0, 1.0),
    )
    col_point: FloatVectorProperty(
        name="Point", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.9, 0.9, 0.9, 1.0),
    )
    col_point_selected: FloatVectorProperty(
        name="Point Selected", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.25, 0.75, 1.0, 1.0),
    )
    col_point_active: FloatVectorProperty(
        name="Point Active", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(1.0, 0.65, 0.15, 1.0),
    )
    col_original: FloatVectorProperty(
        name="Original", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.55, 0.55, 0.55, 1.0),
    )

    point_size: FloatProperty(name="Point Size", default=7.0, min=1.0, max=32.0)
    point_size_selected: FloatProperty(name="Selected Point Size", default=9.0, min=1.0, max=32.0)
    point_size_active: FloatProperty(name="Active Point Size", default=11.0, min=1.0, max=32.0)
    hit_radius: IntProperty(name="Pick Radius (px)", default=14, min=4, max=64)

    use_density: BoolProperty(
        name="Vertex Density By Default",
        description="Default value of the Vertex Density option",
        default=False,
    )

    # ---- on screen help ---------------------------------------------- #
    hud_corner: EnumProperty(
        name="Help Position",
        description="Where the key hints are drawn in the 3D View",
        items=(
            ("BOTTOM_LEFT", "Bottom Left", "Lower left corner (keeps clear of the Blender header)"),
            ("BOTTOM_RIGHT", "Bottom Right", ""),
            ("TOP_LEFT", "Top Left", ""),
            ("TOP_RIGHT", "Top Right", ""),
        ),
        default="BOTTOM_LEFT",
    )
    hud_size: IntProperty(
        name="Help Font Size",
        description="Font size of the on screen help",
        default=15, min=9, max=32,
    )
    hud_lang: EnumProperty(
        name="Help Language",
        description="Language of the on screen help",
        items=(
            ("RU", "Русский", "Russian hints"),
            ("EN", "English", "English hints"),
            ("AUTO", "Blender Language", "Follow the Blender interface language"),
        ),
        default="RU",
    )
    hud_show_keys: BoolProperty(
        name="Show Key List",
        description="Draw the key reference under the status lines",
        default=True,
    )
    right_click_action: EnumProperty(
        name="Right Click",
        description="What a right click does while the curve is being edited",
        items=(
            ("CONFIRM", "Apply", "Apply the deformation and leave the tool"),
            ("RESET", "Reset Curve", "Throw away the deformation and restart the curve"),
            ("NOTHING", "Nothing", "Ignore right clicks"),
        ),
        default="CONFIRM",
    )
    left_click_empty: EnumProperty(
        name="Left Click On Empty",
        description="What a plain left click on empty space does (dragging still makes a "
                    "selection box)",
        items=(
            ("DESELECT", "Clear Selection", "Only drop the control point selection"),
            ("CONFIRM", "Apply", "Apply and leave the tool (the old behaviour)"),
        ),
        default="DESELECT",
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Viewport")
        box = col.box()
        box.prop(self, "col_curve")
        box.prop(self, "col_original")
        box = col.box()
        box.prop(self, "col_point")
        box.prop(self, "col_point_selected")
        box.prop(self, "col_point_active")
        box = col.box()
        box.prop(self, "point_size")
        box.prop(self, "point_size_selected")
        box.prop(self, "point_size_active")
        box.prop(self, "hit_radius")
        col = layout.column(align=True)
        col.label(text="Behaviour")
        box = col.box()
        box.prop(self, "right_click_action")
        box.prop(self, "left_click_empty")
        box.prop(self, "use_density")
        col = layout.column(align=True)
        col.label(text="Shortcut")
        box = col.box()
        box.prop(self, "keymap_enabled")
        row = box.row(align=True)
        row.enabled = self.keymap_enabled
        row.prop(self, "keymap_ctrl", toggle=True)
        row.prop(self, "keymap_shift", toggle=True)
        row.prop(self, "keymap_alt", toggle=True)
        row.prop(self, "keymap_key", text="")
        if self.keymap_enabled:
            conflicts = keymap_conflicts(context, self)
            if conflicts:
                warn = box.column(align=True)
                warn.scale_y = 0.85
                warn.label(text="Already used by: %s" % ", ".join(conflicts[:2]),
                           icon="ERROR")
                if len(conflicts) > 2:
                    warn.label(text="...and %d more" % (len(conflicts) - 2))
            elif not (self.keymap_ctrl or self.keymap_shift or self.keymap_alt):
                warn = box.column(align=True)
                warn.scale_y = 0.85
                warn.label(text="A bare key is unlikely to be free", icon="INFO")
        box.operator("fflex.reset_keymap", icon="LOOP_BACK")
        col = layout.column(align=True)
        col.label(text="On Screen Help")
        box = col.box()
        box.prop(self, "hud_corner")
        box.prop(self, "hud_size")
        box.prop(self, "hud_lang")
        box.prop(self, "hud_show_keys")


# --------------------------------------------------------------------------- #
# accessors
# --------------------------------------------------------------------------- #

def get_settings(context):
    scene = getattr(context, "scene", None)
    settings = getattr(scene, "fflex", None) if scene else None
    return settings


def get_prefs(context):
    try:
        return context.preferences.addons[PKG].preferences
    except Exception:  # pragma: no cover - add-on disabled / not registered
        return None


def settings_or_defaults(context):
    """Never returns None - keeps the operators usable without a scene."""
    return get_settings(context)


CLASSES = (FFlexSettings, FFlexPreferences)


def register():
    compat.register_classes(CLASSES)
    if not hasattr(bpy.types.Scene, "fflex"):
        bpy.types.Scene.fflex = PointerProperty(type=FFlexSettings)


def unregister():
    if hasattr(bpy.types.Scene, "fflex"):
        del bpy.types.Scene.fflex
    compat.unregister_classes(CLASSES)
