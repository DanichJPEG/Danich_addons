# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""Small version shims (Blender 4.2 ... 5.x)."""

import bpy

# Blender 5.0 renamed the 3D View space type from VIEW3D to VIEW_3D
VIEW3D_TYPE = "VIEW3D" if bpy.app.version < (5, 0, 0) else "VIEW_3D"

# GPU points/lines built-ins changed in 4.5
IS_4_5 = bpy.app.version >= (4, 5, 0)

# Draw handler space identifier (kept as "WINDOW" in every supported version)
DRAW_SPACE = "WINDOW"


def is_view3d_area(area):
    return area is not None and getattr(area, "type", None) == VIEW3D_TYPE


# --------------------------------------------------------------------------- #
# class registration helpers
#
# Blender calls ``register()`` / ``unregister()`` more than once in real life:
# a "Reload Scripts" (F8), an update installed from a repository, or a add-on
# disabled twice in a row.  A bare ``bpy.utils.unregister_class()`` on a class
# that is already gone raises ``RuntimeError`` in the *middle* of the cleanup,
# so everything after it (handlers, menus, the Scene property) would stay
# behind.  These wrappers make both directions idempotent.
#
# ``cls.is_registered`` is the only reliable probe: ``hasattr(bpy.types, name)``
# is True for operators/panels after registration but stays False for
# ``PropertyGroup`` and ``AddonPreferences`` subclasses (checked on 5.2).
# --------------------------------------------------------------------------- #

def register_classes(classes):
    for cls in classes:
        if getattr(cls, "is_registered", False):
            continue          # already registered - do not stack a second copy
        bpy.utils.register_class(cls)


def unregister_classes(classes):
    for cls in reversed(classes):
        if not getattr(cls, "is_registered", False):
            continue          # already unregistered - nothing to undo
        try:
            bpy.utils.unregister_class(cls)
        except Exception:     # pragma: no cover - never abort the cleanup
            pass
