"""Verify FFlex installed *from the repository*: registration, operator, cleanup.

The bundled harness (blender-addon-dev) does not see classes of a split
package - FFlex defines them in ``ui.py``/``props.py``/``ops_*.py`` rather than
in the package ``__init__.py`` - so this script checks the installed extension
directly, including the leftovers that a plain ``unregister()`` leaves behind.

Prerequisites (a scratch Blender profile, so the real one is untouched):

    python -m http.server 8124 --directory .\\docs
    set BLENDER_USER_CONFIG=<scratch>\\cfg
    set BLENDER_USER_SCRIPTS=<scratch>\\scripts
    set BLENDER_USER_EXTENSIONS=<scratch>\\ext
    blender --background --online-mode --command extension repo-add danichjpeg ^
        --url http://127.0.0.1:8124/index.json --clear-all
    blender --background --online-mode --command extension sync
    blender --background --online-mode --command extension install -e fflex
    blender --background --online-mode --python tests\\check_from_repo.py

Exit code 0 = everything passed.
"""

import math

import addon_utils
import bpy
from mathutils import Vector

MODULE = "bl_ext.danichjpeg.fflex"

# (label, attribute name inside its submodule).  ``is_registered`` is the only
# reliable "is this class live" probe: ``hasattr(bpy.types, name)`` is False for
# PropertyGroup/AddonPreferences even while they are registered.
CLASS_PATHS = (
    ("props", "FFlexSettings"),
    ("props", "FFlexPreferences"),
    ("ui", "FFLEX_PT_panel"),
    ("ui", "FFLEX_OT_reload_keymap"),
    ("ui", "FFLEX_OT_reset_keymap"),
    ("ops_flex", "FFLEX_OT_flex"),
    ("ops_flex", "FFLEX_OT_flex_apply"),
    ("ops_space", "FFLEX_OT_even_space"),
)
OPERATORS = ("flex", "flex_apply", "even_space", "reload_keymap", "reset_keymap")

_failures = []


def check(label, ok, detail=""):
    print("%-52s %s%s" % (label, "OK" if ok else "FAIL", ("  [%s]" % detail) if detail else ""))
    if not ok:
        _failures.append(label)


def package():
    return __import__(MODULE, fromlist=sorted({mod for mod, _ in CLASS_PATHS}))


def class_objects():
    pkg = package()
    return [("%s.%s" % (mod, name), getattr(getattr(pkg, mod), name))
            for mod, name in CLASS_PATHS]


def live_classes():
    return [label for label, cls in class_objects() if getattr(cls, "is_registered", False)]


def state():
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    keymap_items = []
    keymap_names = []
    if keyconfig is not None:
        for km in keyconfig.keymaps:
            keymap_names.append(km.name)
            for kmi in km.keymap_items:
                if kmi.idname.startswith("fflex."):
                    keymap_items.append((km.name, kmi.idname))
    return {
        "classes": live_classes(),
        "scene_prop": hasattr(bpy.context.scene, "fflex"),
        "keymap_items": keymap_items,
        "keymap_names": keymap_names,
        "handlers": len(bpy.app.handlers.load_post),
    }


# --------------------------------------------------------------------------- #
# 1. enable
# --------------------------------------------------------------------------- #

module = addon_utils.enable(MODULE, default_set=True, persistent=True)
check("extension enabled", module is not None and module.__name__ == MODULE, MODULE)

state_on = state()
missing = sorted({"%s.%s" % p for p in CLASS_PATHS} - set(state_on["classes"]))
check("all %d classes registered" % len(CLASS_PATHS), not missing, "missing %s" % missing)
for name in OPERATORS:
    check("bpy.ops.fflex.%s available" % name, hasattr(bpy.ops.fflex, name))
check("Scene.fflex settings property", state_on["scene_prop"])
check("Ctrl+Shift+F bound to fflex.flex",
      ("3D View", "fflex.flex") in state_on["keymap_items"], "%s" % (state_on["keymap_items"],))
check("add-on preferences object", type(
    bpy.context.preferences.addons[MODULE].preferences).__name__ == "FFlexPreferences")

# --------------------------------------------------------------------------- #
# 2. the operator really works through the extension namespace
# --------------------------------------------------------------------------- #

coords = []
for i in range(10):
    ang = 2.0 * math.pi * i / 10.0
    radius = 1.0 + (0.3 if i % 2 else -0.1)
    coords.append((math.cos(ang) * radius, 0.0, math.sin(ang) * radius))

mesh = bpy.data.meshes.new("FFlexRepoCheck")
mesh.from_pydata(coords, [(i, (i + 1) % len(coords)) for i in range(len(coords))], [])
mesh.update()
obj = bpy.data.objects.new("FFlexRepoCheck", mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
result = bpy.ops.fflex.even_space(use_panel_settings=False, measure="CHAIN")
check("bpy.ops.fflex.even_space ran (from the repo install)",
      "FINISHED" in result, "%s" % (result,))
bpy.ops.object.mode_set(mode="OBJECT")

points = [v.co.copy() for v in obj.data.vertices]
gaps = [(points[(i + 1) % len(points)] - points[i]).length for i in range(len(points))]
check("equal spacing achieved (spread < 1e-5)", max(gaps) - min(gaps) < 1e-5,
      "spread=%.9f" % (max(gaps) - min(gaps)))

# --------------------------------------------------------------------------- #
# 3. disable - twice in a row, the case that used to raise mid-cleanup
# --------------------------------------------------------------------------- #

addon_utils.disable(MODULE)
try:
    addon_utils.disable(MODULE)
    second_ok, second_error = True, ""
except Exception as ex:                                   # noqa: BLE001
    second_ok, second_error = False, "%s: %s" % (type(ex).__name__, ex)
check("disable() is idempotent (second call does not raise)", second_ok, second_error)

after = state()
check("no classes left registered", not after["classes"], "%s" % (after["classes"],))
check("Scene.fflex removed", not after["scene_prop"])
check("no fflex key map items left", not after["keymap_items"], "%s" % (after["keymap_items"],))
check("'3D View' add-on key map removed", "3D View" not in after["keymap_names"],
      "%s" % (after["keymap_names"],))
check("handlers back to baseline", after["handlers"] == state_on["handlers"])
try:
    bpy.ops.fflex.even_space(use_panel_settings=False, measure="CHAIN")
    op_gone = False
except Exception:                                         # noqa: BLE001
    op_gone = True
check("operator no longer callable after disable", op_gone)

# --------------------------------------------------------------------------- #
# 4. re-enable after a clean disable still works (the update / F8 path)
# --------------------------------------------------------------------------- #

module = addon_utils.enable(MODULE, default_set=True, persistent=True)
again = state()
check("re-enable registers everything again",
      not sorted({"%s.%s" % p for p in CLASS_PATHS} - set(again["classes"])),
      "%s" % (again["classes"],))
check("no duplicated key map item after re-enable",
      len(again["keymap_items"]) == 1, "%s" % (again["keymap_items"],))
addon_utils.disable(MODULE)

print("")
print("RESULT: %s" % ("PASS" if not _failures else "FAIL -> %s" % _failures))
if _failures:
    raise SystemExit(1)
