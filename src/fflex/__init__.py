# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""FFlex - fit edge loops onto an editable imaginary curve.

Works in screen space as well as in global, local and 3D cursor axes and can
distribute the vertices with equal spacing along the curve.

3D View > Sidebar (N) > FFlex      hotkey: Ctrl+Shift+F
"""

# No ``bl_info`` here: this is an *extension*, its metadata lives in
# ``blender_manifest.toml`` next to this file (Blender 4.2+).
# The manifest ``id`` (``fflex``) equals this directory name, and Blender
# imports the package as ``bl_ext.<repository_id>.fflex`` - that is why
# everything below uses ``__package__`` instead of a hard-coded name.

import importlib
import sys

import bpy

from . import compat, core, i18n, ops_flex, ops_space, props, ui

# order matters: preferences/settings first, interface last
MODULES = (props, ops_flex, ops_space, ui)
SUBMODULES = ("compat", "core", "i18n", "props", "ops_flex", "ops_space", "ui")


def _stale_classes():
    """Classes of this add-on that are still registered in Blender."""
    for name in SUBMODULES:
        module = sys.modules.get("%s.%s" % (__name__, name))
        if module is None:
            continue
        for attribute in vars(module).values():
            if isinstance(attribute, type) and getattr(attribute, "is_registered", False):
                yield attribute


def _refresh_modules():
    """Read the files again after an in-place update (drag & drop).

    Blender keeps the previously imported module and its registered classes, so
    the sidebar panel would keep showing the old UI (and the old version) until
    Blender is restarted. Unregistering the stale classes and reloading the
    submodules fixes that for good.
    """
    for cls in list(_stale_classes()):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    for name in SUBMODULES:
        full = "%s.%s" % (__name__, name)
        module = sys.modules.get(full)
        if module is not None:
            try:
                importlib.reload(module)
            except Exception:
                pass


def register():
    _refresh_modules()
    i18n.register(__name__)
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
    i18n.unregister(__name__)


if __name__ == "__main__":  # pragma: no cover
    register()
