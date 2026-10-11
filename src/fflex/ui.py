# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""Interface: sidebar panel (N-panel), context menus and key map."""

import bpy
from bpy.types import Operator, Panel

from .compat import VIEW3D_TYPE, register_classes, unregister_classes
from . import ops_flex, props
from .props import get_settings

_km_items = []


def T(text):
    """Translate an interface string (used for labels drawn by hand)."""
    try:
        return bpy.app.translations.pgettext_iface(text)
    except Exception:  # pragma: no cover
        return text


class FFLEX_PT_panel(Panel):
    bl_idname = "FFLEX_PT_panel"
    bl_label = "FFlex"
    bl_space_type = VIEW3D_TYPE
    bl_region_type = "UI"
    bl_category = "FFlex"

    def draw(self, context):
        layout = self.layout
        settings = get_settings(context)
        if settings is None:
            layout.label(text="FFlex: settings unavailable", icon="ERROR")
            return

        obj = context.active_object
        mesh_ok = obj is not None and obj.type == "MESH"

        # ---- actions ------------------------------------------------------ #
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.5
        row.enabled = mesh_ok
        row.operator("fflex.flex", icon="MOD_CURVE")
        row = col.row(align=True)
        row.enabled = mesh_ok
        row.operator("fflex.flex_apply", icon="CHECKMARK")
        row.prop(settings, "iterations", text="")

        if not mesh_ok:
            col.label(text="Select a mesh object", icon="INFO")
        elif obj.mode != "EDIT" and not any(v.select for v in obj.data.vertices):
            col.label(text="Select an edge loop (Edit Mode)", icon="INFO")

        # ---- work space --------------------------------------------------- #
        box = layout.box()
        box.label(text=T("Deform Space"), icon="AXIS_FRONT")
        box.prop(settings, "space_mode", text="")
        sub = box.row(align=True)
        sub.enabled = settings.space_mode != "SCREEN"
        sub.prop(settings, "axis", expand=True)
        box.prop(settings, "pivot", text="")

        # ---- curve -------------------------------------------------------- #
        box = layout.box()
        box.label(text=T("Imaginary Curve"), icon="MOD_CURVE")
        row = box.row(align=True)
        row.prop(settings, "points")
        row.enabled = not settings.auto_points
        col = box.column(align=True)
        col.prop(settings, "auto_points", toggle=True, icon="AUTO")
        sub = col.row(align=True)
        sub.enabled = settings.auto_points
        sub.prop(settings, "hug_tolerance")
        box.prop(settings, "roundness")
        box.prop(settings, "strength")
        col = box.column(align=True)
        col.prop(settings, "even_spacing", toggle=True, icon="ARROW_LEFTRIGHT")
        col.prop(settings, "equal_gaps", toggle=True)
        col.prop(settings, "split_ribbon", toggle=True, icon="MOD_EDGESPLIT")
        sub = col.row(align=True)
        sub.enabled = settings.split_ribbon
        sub.prop(settings, "pinch_ratio")
        col.prop(settings, "flatten", toggle=True, icon="MOD_SOLIDIFY")
        col.prop(settings, "keep_offset", toggle=True, icon="MOD_OFFSET")
        col = box.column(align=True)
        col.prop(settings, "interpolate_loops", toggle=True, icon="AUTOMERGE_ON")
        sub = col.column(align=True)
        sub.enabled = settings.interpolate_loops
        if getattr(ops_flex, "SHAPES_ENABLED", False):
            sub.prop(settings, "interp_shape", text="")
            sub.prop(settings, "interp_tension")
        sub.prop(settings, "interp_smooth")
        if getattr(ops_flex, "SHEET_ENABLED", False):
            row = sub.row(align=True)
            row.prop(settings, "interp_sheet", toggle=True)
            angle = row.row(align=True)
            angle.enabled = settings.interp_sheet
            angle.prop(settings, "interp_angle", text="")
        if getattr(ops_flex, "MIRROR_ENABLED", False):
            row = col.row(align=True)
            row.prop(settings, "mirror", toggle=True)
            sub = row.row(align=True)
            sub.enabled = settings.mirror
            sub.prop(settings, "mirror_axis", expand=True)
        col = box.column(align=True)
        col.prop(settings, "use_density", toggle=True)
        col.prop(settings, "show_original", toggle=True)
        box.prop(settings, "segments")

        # ---- even spacing ------------------------------------------------- #
        box = layout.box()
        box.label(text=T("Even Space"), icon="ARROW_LEFTRIGHT")
        box.prop(settings, "space_measure", text="")
        box.prop(settings, "equal_gaps", toggle=True)
        box.prop(settings, "space_extend", toggle=True)
        row = box.row(align=True)
        row.scale_y = 1.25
        row.enabled = mesh_ok
        row.operator("fflex.even_space", icon="ARROW_LEFTRIGHT")

        # ---- about -------------------------------------------------------- #
        box = layout.box()
        box.label(text="FFlex   1.5.1   -   DanichJpeg", icon="INFO")
        col = box.column(align=True)
        col.scale_y = 0.85
        col.label(text="Keys are shown in the viewport")
        col.label(text="while the tool is running (H hides them)")


def _menu_func(self, context):
    layout = self.layout
    layout.separator()
    layout.operator("fflex.flex", icon="MOD_CURVE")
    layout.operator("fflex.flex_apply", icon="CHECKMARK")
    layout.operator("fflex.even_space", icon="ARROW_LEFTRIGHT")


MENUS = (
    bpy.types.VIEW3D_MT_edit_mesh_vertices,
    bpy.types.VIEW3D_MT_edit_mesh_edges,
    bpy.types.VIEW3D_MT_object,
)


def _prefs():
    """The add-on preferences (None while the add-on is not enabled that way)."""
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except Exception:
        return None


def _shortcut():
    """The shortcut set in the preferences, with the old default as a fallback."""
    prefs = _prefs()
    if prefs is None:
        return True, "F", True, True, False
    return (bool(getattr(prefs, "keymap_enabled", True)),
            getattr(prefs, "keymap_key", "F"),
            bool(getattr(prefs, "keymap_ctrl", True)),
            bool(getattr(prefs, "keymap_shift", True)),
            bool(getattr(prefs, "keymap_alt", False)))


def shortcut_label():
    """A readable form of the current shortcut, e.g. ``Ctrl+Shift+F``."""
    enabled, key, ctrl, shift, alt = _shortcut()
    if not enabled:
        return ""
    parts = []
    if ctrl:
        parts.append("Ctrl")
    if shift:
        parts.append("Shift")
    if alt:
        parts.append("Alt")
    parts.append(key)
    return "+".join(parts)


class FFLEX_OT_reload_keymap(Operator):
    """Rebuild the shortcut from the preferences (used by their update callbacks)."""

    bl_idname = "fflex.reload_keymap"
    bl_label = "Reload FFlex Shortcut"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        unregister_keymap()
        register_keymap()
        return {"FINISHED"}


class FFLEX_OT_reset_keymap(Operator):
    """Put the shortcut back to the default (Ctrl + Shift + F)."""

    bl_idname = "fflex.reset_keymap"
    bl_label = "Reset Shortcut"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        prefs = _prefs()
        if prefs is None:
            return {"CANCELLED"}
        prefs.keymap_enabled = True
        prefs.keymap_ctrl = props.DEFAULT_CTRL
        prefs.keymap_shift = props.DEFAULT_SHIFT
        prefs.keymap_alt = props.DEFAULT_ALT
        prefs.keymap_key = props.DEFAULT_KEY
        unregister_keymap()
        register_keymap()
        self.report({"INFO"}, "FFlex: shortcut reset to %s" % shortcut_label())
        return {"FINISHED"}


def register_keymap():
    unregister_keymap()          # never stack a second copy of the shortcut
    wm = bpy.context.window_manager
    keyconfig = getattr(wm, "keyconfigs", None)
    addon = getattr(keyconfig, "addon", None) if keyconfig else None
    if addon is None:
        return
    enabled, key, ctrl, shift, alt = _shortcut()
    if not enabled:
        return
    km = addon.keymaps.new(name="3D View", space_type=VIEW3D_TYPE)
    kmi = km.keymap_items.new("fflex.flex", key, "PRESS", ctrl=ctrl, shift=shift, alt=alt)
    _km_items.append((km, kmi))


def unregister_keymap():
    for km, kmi in _km_items:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
        # An add-on key map that stays behind - even an empty one - is saved
        # into the user preferences, so drop it together with its last item.
        try:
            if not len(km.keymap_items):
                addon = getattr(bpy.context.window_manager.keyconfigs, "addon", None)
                if addon is not None:
                    addon.keymaps.remove(km)
        except Exception:
            pass
    _km_items.clear()


CLASSES = (FFLEX_PT_panel, FFLEX_OT_reload_keymap, FFLEX_OT_reset_keymap)


def register():
    # remove() first: register() runs again after a reload, and otherwise the
    # menu entries pile up one per reload.
    for menu in MENUS:
        try:
            menu.remove(_menu_func)
        except Exception:
            pass
    register_classes(CLASSES)
    for menu in MENUS:
        menu.append(_menu_func)
    register_keymap()


def unregister():
    unregister_keymap()
    for menu in MENUS:
        try:
            menu.remove(_menu_func)
        except Exception:
            pass
    unregister_classes(CLASSES)
