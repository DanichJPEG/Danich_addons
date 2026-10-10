# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""Standalone even spacing (a LoopTools "Space" analogue with FFlex spaces).

``fflex.even_space`` redistributes the selected vertices with an equal gap:

* **Along Chain (3D)** - equal 3D distance along the loop itself,
* **In Axis Plane**    - equal distance measured inside the work plane of the
  chosen space (screen / global / local / 3D cursor axes); the coordinate along
  the frame axis of every vertex is kept, so the chain is sheared, not flattened.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator
from mathutils import Vector

from . import compat, core
from .core import MeshSession
from .ops_flex import redraw_view3d
from .props import (
    AXIS_ITEMS,
    MEASURE_ITEMS,
    PIVOT_ITEMS,
    SPACE_ITEMS,
    get_settings,
)


class FFLEX_OT_even_space(Operator):
    bl_idname = "fflex.even_space"
    bl_label = "Even Space"
    bl_description = ("Distribute the selected vertices with equal spacing along the edge loop "
                      "(screen / global / local / 3D cursor axes)")
    bl_options = {"REGISTER", "UNDO"}

    use_panel_settings: BoolProperty(default=True, options={"HIDDEN"})
    space_mode: EnumProperty(name="Space", items=SPACE_ITEMS, default=core.SPACE_SCREEN)
    axis: EnumProperty(name="Axis", items=AXIS_ITEMS, default="Z")
    pivot: EnumProperty(name="Pivot", items=PIVOT_ITEMS, default=core.PIVOT_CENTROID)
    measure: EnumProperty(name="Measure", items=MEASURE_ITEMS, default=core.MEASURE_CHAIN)
    extend: BoolProperty(name="Extend to Loop", default=False)
    equal_gaps: BoolProperty(name="Equal Gaps", default=True)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def invoke(self, context, event):
        return self.execute(context)

    def sync_from_settings(self, context):
        settings = get_settings(context)
        if settings is None or not self.use_panel_settings:
            return
        self.space_mode = settings.space_mode
        self.axis = settings.axis
        self.pivot = settings.pivot
        self.measure = settings.space_measure
        self.extend = settings.space_extend
        self.equal_gaps = settings.equal_gaps

    def execute(self, context):
        self.sync_from_settings(context)

        obj = context.active_object
        session = MeshSession(obj)
        bm = session.bm
        matrix_world = obj.matrix_world.copy()
        matrix_inv = matrix_world.inverted()

        chains = core.build_chains(bm)
        if not chains:
            session.free()
            self.report({"WARNING"}, "Select an edge loop first (at least 3 connected vertices)")
            return {"CANCELLED"}

        before_total = 0.0
        before_min = float("inf")
        before_max = 0.0
        after_min = float("inf")
        after_max = 0.0
        touched = 0

        for indices, closed in chains:
            if self.extend:
                indices, closed = core.extend_chain(bm, indices, closed)
            if len(indices) < 3:
                continue

            verts = [bm.verts[i] for i in indices]
            world = [matrix_world @ v.co for v in verts]

            if self.measure == core.MEASURE_PLANE:
                anchor = sum(world, Vector((0.0, 0.0, 0.0))) / len(world)
                frame = core.make_frame(context, obj, self.space_mode, self.axis, self.pivot, anchor)
                result = core.distribute_evenly_in_frame(world, frame, closed, self.equal_gaps)
            else:
                result = core.distribute_evenly(world, closed, self.equal_gaps)

            total, gap_min, gap_max = core.chain_metrics(world)
            before_total += total
            before_min = min(before_min, gap_min)
            before_max = max(before_max, gap_max)

            for vert, point in zip(verts, result):
                vert.co = matrix_inv @ point
                touched += 1

            _total, gap_min, gap_max = core.chain_metrics(result)
            after_min = min(after_min, gap_min)
            after_max = max(after_max, gap_max)

        session.commit()
        session.free()
        redraw_view3d()

        if touched == 0:
            self.report({"WARNING"}, "Nothing to space: the selection is too short")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "FFlex even space: %d vertices, gap %.4f-%.4f -> %.4f-%.4f"
            % (touched, before_min, before_max, after_min, after_max),
        )
        return {"FINISHED"}


CLASSES = (FFLEX_OT_even_space,)


def register():
    compat.register_classes(CLASSES)


def unregister():
    compat.unregister_classes(CLASSES)
