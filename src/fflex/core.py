# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""Core helpers for FFlex.

Everything in this module is UI free (except :func:`make_frame` which only
*reads* the view/cursor state) so it can be unit tested from ``blender
--background``.

Contains
--------
* :class:`MeshSession`   - uniform access to the active mesh (edit / object mode)
* :class:`Frame`         - a work frame: screen, global, local or 3D cursor axes
* :class:`Curve`         - spline through control points (arc length parameterised)
* :func:`build_chains`   - ordered edge loops / chains from a vertex selection
* even spacing helpers
"""

import math
from bisect import bisect_right
from collections import deque

import bmesh
from mathutils import Vector

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}

SPACE_SCREEN = "SCREEN"
SPACE_GLOBAL = "GLOBAL"
SPACE_LOCAL = "LOCAL"
SPACE_CURSOR = "CURSOR"
SPACE_ITEMS = (SPACE_SCREEN, SPACE_GLOBAL, SPACE_LOCAL, SPACE_CURSOR)

PIVOT_CENTROID = "CENTROID"
PIVOT_ORIGIN = "ORIGIN"
PIVOT_CURSOR = "CURSOR"
PIVOT_WORLD = "WORLD"
PIVOT_ITEMS = (PIVOT_CENTROID, PIVOT_ORIGIN, PIVOT_CURSOR, PIVOT_WORLD)

MEASURE_CHAIN = "CHAIN"
MEASURE_PLANE = "PLANE"

EPS = 1e-9


# --------------------------------------------------------------------------- #
# frame of reference (the "axes" FFlex works in)
# --------------------------------------------------------------------------- #

def _ortho_normalize(u, n):
    """Return ``u`` orthogonalised against the unit vector ``n`` (unit length)."""
    u = u - n * u.dot(n)
    if u.length < 1e-7:
        u = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
        u = u - n * u.dot(n)
    return u.normalized()


class Frame:
    """An orthonormal work frame ``(origin, u, v, n)``.

    ``n``   - the axis of the frame, the plane of the frame is perpendicular to it,
    ``u``   - first in plane axis, ``v`` - second in plane axis.
    """

    __slots__ = ("origin", "u", "v", "n", "space", "axis")

    def __init__(self, origin, u, v, n, space=SPACE_SCREEN, axis="Z"):
        self.origin = Vector(origin)
        self.u = Vector(u).normalized()
        self.v = Vector(v).normalized()
        self.n = Vector(n).normalized()
        self.space = space
        self.axis = axis

    # -- conversions ------------------------------------------------------- #
    def to_plane(self, co):
        """3D point -> (a, b, c) in frame space (``c`` along the frame axis)."""
        d = Vector(co) - self.origin
        return (d.dot(self.u), d.dot(self.v), d.dot(self.n))

    def from_plane(self, a, b, c=0.0):
        return self.origin + self.u * a + self.v * b + self.n * c

    def project(self, co):
        """Project a 3D point onto the frame plane (drops the axis component)."""
        a, b, _c = self.to_plane(co)
        return self.from_plane(a, b, 0.0)

    def copy(self):
        return Frame(self.origin, self.u, self.v, self.n, self.space, self.axis)

    def describe(self):
        if self.space == SPACE_SCREEN:
            return "Screen (view)"
        return "%s %s" % (self.space.capitalize(), self.axis)


def view_basis(context):
    """Return ``(right, up, forward)`` for the current 3D view.

    Falls back to the front view when there is no view (background mode).
    """
    rv3d = getattr(context, "region_data", None)
    if rv3d is not None:
        try:
            rot = rv3d.view_rotation.to_matrix()
            return (rot @ Vector((1.0, 0.0, 0.0)),
                    rot @ Vector((0.0, 1.0, 0.0)),
                    rot @ Vector((0.0, 0.0, -1.0)))
        except Exception:  # pragma: no cover - defensive
            pass
    return (Vector((1.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0)), Vector((0.0, -1.0, 0.0)))


def space_axes(context, obj, space):
    """Return the three basis vectors of ``space`` (X, Y, Z order)."""
    if space == SPACE_GLOBAL:
        return (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0)))
    if space == SPACE_LOCAL and obj is not None:
        m = obj.matrix_world.to_3x3()
        return (m.col[0].normalized(), m.col[1].normalized(), m.col[2].normalized())
    if space == SPACE_CURSOR:
        cursor = getattr(context.scene, "cursor", None)
        m = getattr(cursor, "matrix", None)
        if m is not None:
            m3 = m.to_3x3()
            return (m3.col[0].normalized(), m3.col[1].normalized(), m3.col[2].normalized())
    r, u, _f = view_basis(context)
    return (r, u, r.cross(u).normalized())


def pivot_point(context, obj, pivot, anchor):
    """Resolve the pivot (frame origin) for the given pivot mode."""
    if pivot == PIVOT_ORIGIN and obj is not None:
        return obj.matrix_world.translation.copy()
    if pivot == PIVOT_CURSOR:
        return Vector(context.scene.cursor.location)
    if pivot == PIVOT_WORLD:
        return Vector((0.0, 0.0, 0.0))
    return Vector(anchor)


def make_frame(context, obj, space, axis, pivot, anchor):
    """Build the work frame used to edit the imaginary curve."""
    origin = pivot_point(context, obj, pivot, anchor)

    if space == SPACE_SCREEN or space not in SPACE_ITEMS:
        right, up, forward = view_basis(context)
        n = forward.normalized()
        u = _ortho_normalize(right, n)
        v = n.cross(u).normalized()
        return Frame(origin, u, v, n, SPACE_SCREEN, "VIEW")

    axes = space_axes(context, obj, space)
    idx = AXIS_INDEX.get(axis, 2)
    n = axes[idx].normalized()
    u = _ortho_normalize(axes[(idx + 1) % 3], n)
    v = n.cross(u).normalized()
    return Frame(origin, u, v, n, space, axis)


# --------------------------------------------------------------------------- #
# mesh access
# --------------------------------------------------------------------------- #

class MeshSession:
    """Read/write the active mesh no matter if we are in edit or object mode.

    Vertices are always addressed by index and re-fetched on every pass: Blender
    may swap the edit mesh under a long running modal operator (undo, another
    tool, a mode toggle), and stale ``BMVert`` references then raise
    ``BMesh data of type BMVert has been removed``.
    """

    def __init__(self, obj):
        self.obj = obj
        self.edit_mode = obj.mode == "EDIT"
        if self.edit_mode:
            self.bm = bmesh.from_edit_mesh(obj.data)
        else:
            self.bm = bmesh.new()
            self.bm.from_mesh(obj.data)
        self.bm.verts.ensure_lookup_table()

    def bmesh(self):
        """The current bmesh (re-read for edit mode, cached for object mode)."""
        if self.edit_mode:
            self.bm = bmesh.from_edit_mesh(self.obj.data)
        self.bm.verts.ensure_lookup_table()
        return self.bm

    def vertex(self, index):
        return self.bmesh().verts[index]

    def coords(self, indices):
        bm = self.bmesh()
        return [bm.verts[i].co.copy() for i in indices]

    def commit(self):
        bm = self.bmesh()
        bm.normal_update()
        if self.edit_mode:
            bmesh.update_edit_mesh(self.obj.data)
        else:
            bm.to_mesh(self.obj.data)
            self.obj.data.update()

    def free(self):
        if not self.edit_mode:
            self.bm.free()


# --------------------------------------------------------------------------- #
# spline
# --------------------------------------------------------------------------- #

def _hermite_clamped(p0, p1, p2, p3, t, clamp=1.0):
    """Catmull-Rom tangents limited to ``clamp * min(segment length)``.

    The limit is what keeps the curve glued to a heavily curved edge loop: a
    plain Catmull-Rom overshoots on tight control polygons, the clamped one
    cannot (``clamp`` = the *Roundness* option, 1.0 .. 2.0).

    The end segments of an open curve get a duplicated neighbour point, so their
    neighbour distance is zero - the limit then falls back to the one available
    segment, which keeps the curve leaving the end point *along* the loop instead
    of flattening it out.
    """
    m1 = (p2 - p0) * 0.5
    m2 = (p3 - p1) * 0.5
    d0 = (p1 - p0).length
    d1 = (p2 - p1).length
    d2 = (p3 - p2).length
    limit1 = clamp * (d0 if d0 > EPS else d1)
    if 0.0 < limit1 < m1.length:
        m1 = m1 * (limit1 / m1.length)
    limit2 = clamp * (d2 if d2 > EPS else d1)
    if 0.0 < limit2 < m2.length:
        m2 = m2 * (limit2 / m2.length)

    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2
    return p1 * h00 + m1 * h10 + p2 * h01 + m2 * h11


def _cubic_bspline(p0, p1, p2, p3, t):
    """Reference uniform cubic B-spline (kept for tests/comparisons)."""
    t2 = t * t
    t3 = t2 * t
    b0 = (1.0 - t) ** 3
    b1 = 3.0 * t3 - 6.0 * t2 + 4.0
    b2 = -3.0 * t3 + 3.0 * t2 + 3.0 * t + 1.0
    b3 = t3
    return (p0 * b0 + p1 * b1 + p2 * b2 + p3 * b3) / 6.0


class Curve:
    """A spline through control points, parameterised by arc length.

    ``roundness`` (1.0 .. 2.0) rounds the shape off: 1.0 keeps the control polygon
    as it is (with clamped tangents, so a tight polygon cannot overshoot), higher
    values blend the polygon towards a smoothed one. The end points of an open
    curve are pinned, so the first and last vertex never drift - no matter how
    much the curve is rounded.
    """

    __slots__ = ("ctrl", "closed", "roundness", "segments", "samples", "cum", "total")

    def __init__(self, ctrl, closed=False, roundness=1.0, segments=12):
        self.ctrl = [Vector(c) for c in ctrl]
        self.closed = bool(closed) and len(ctrl) >= 3
        self.roundness = float(roundness)
        self.segments = max(2, int(segments))
        self.samples = []
        self.cum = [0.0]
        self.total = 0.0
        self.rebuild()

    # -- construction ------------------------------------------------------ #
    def copy(self):
        new = Curve.__new__(Curve)
        new.ctrl = [c.copy() for c in self.ctrl]
        new.closed = self.closed
        new.roundness = self.roundness
        new.segments = self.segments
        new.samples = [s.copy() for s in self.samples]
        new.cum = list(self.cum)
        new.total = self.total
        return new

    def reverse(self):
        self.ctrl.reverse()
        self.rebuild()

    def shaped_ctrl(self):
        """The polygon actually sampled: rounded off, but with pinned ends."""
        ctrl = self.ctrl
        blend = max(0.0, min(1.0, self.roundness - 1.0))
        n = len(ctrl)
        if blend <= 0.0 or n < 3:
            return ctrl
        if self.closed:
            smoothed = [(ctrl[i - 1] + ctrl[i] * 2.0 + ctrl[(i + 1) % n]) / 4.0 for i in range(n)]
        else:
            smoothed = [ctrl[0]]
            smoothed += [(ctrl[i - 1] + ctrl[i] * 2.0 + ctrl[i + 1]) / 4.0 for i in range(1, n - 1)]
            smoothed.append(ctrl[-1])
        return [c.lerp(s, blend) for c, s in zip(ctrl, smoothed)]

    def rebuild(self):
        ctrl = self.shaped_ctrl()
        n = len(ctrl)
        self.samples = []
        if n == 0:
            self.cum, self.total = [0.0], 0.0
            return
        if n == 1:
            self.samples = [ctrl[0].copy()]
            self.cum, self.total = [0.0], 0.0
            return

        clamp = max(1.0, min(2.0, self.roundness))
        seg = self.segments
        pts = []

        if self.closed:
            for i in range(n):
                p0, p1 = ctrl[(i - 1) % n], ctrl[i]
                p2, p3 = ctrl[(i + 1) % n], ctrl[(i + 2) % n]
                for k in range(seg):
                    pts.append(_hermite_clamped(p0, p1, p2, p3, k / seg, clamp))
            pts.append(pts[0].copy())
        else:
            for i in range(n - 1):
                p0 = ctrl[max(i - 1, 0)]
                p1 = ctrl[i]
                p2 = ctrl[i + 1]
                p3 = ctrl[min(i + 2, n - 1)]
                for k in range(seg):
                    pts.append(_hermite_clamped(p0, p1, p2, p3, k / seg, clamp))
            pts.append(ctrl[-1].copy())

        self.samples = pts
        cum = [0.0]
        for a, b in zip(pts[:-1], pts[1:]):
            cum.append(cum[-1] + (b - a).length)
        self.cum = cum
        self.total = cum[-1]

    # -- queries ----------------------------------------------------------- #
    def point_at_length(self, s):
        if not self.samples:
            return Vector((0.0, 0.0, 0.0))
        total = self.total
        if total <= EPS:
            return self.samples[0].copy()
        s = max(0.0, min(total, s))
        i = bisect_right(self.cum, s) - 1
        i = max(0, min(i, len(self.cum) - 2))
        a, b = self.samples[i], self.samples[i + 1]
        seg = self.cum[i + 1] - self.cum[i]
        f = 0.0 if seg <= EPS else (s - self.cum[i]) / seg
        return a.lerp(b, f)

    def point_at_param(self, t):
        return self.point_at_length(max(0.0, min(1.0, t)) * self.total)

    def param_of_length(self, s):
        if self.total <= EPS:
            return 0.0
        return max(0.0, min(1.0, s / self.total))

    def closest_param(self, co):
        """Closest point on the sampled curve -> ``(normalized param, distance)``."""
        pts = self.samples
        if len(pts) < 2 or self.total <= EPS:
            return 0.0, (Vector(co) - pts[0]).length if pts else 0.0
        co = Vector(co)
        best_d = float("inf")
        best_t = 0.0
        cum = self.cum
        total = self.total
        for i in range(len(pts) - 1):
            a = pts[i]
            ab = pts[i + 1] - a
            l2 = ab.length_squared
            if l2 <= 1e-16:
                u = 0.0
            else:
                u = (co - a).dot(ab) / l2
                u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
            d = (co - (a + ab * u)).length_squared
            if d < best_d:
                best_d = d
                best_t = (cum[i] + u * (cum[i + 1] - cum[i])) / total
        return best_t, math.sqrt(best_d)

    def resample(self, count):
        """``count`` control points evenly spaced along the current curve."""
        count = max(2, int(count))
        if self.total <= EPS:
            base = self.ctrl[0] if self.ctrl else Vector((0.0, 0.0, 0.0))
            return [base.copy() for _ in range(count)]
        if self.closed:
            return [self.point_at_length(self.total * i / count) for i in range(count)]
        return [self.point_at_length(self.total * i / (count - 1)) for i in range(count)]

    def even_params(self, count, equalize=False):
        """Evenly distributed normalised parameters for ``count`` points."""
        return even_curve_params(self, count, self.closed, equalize)

    def screen_points(self, project):
        """``project``: 3D -> 2D or None; returns the visible polyline."""
        out = []
        for p in self.samples:
            sp = project(p)
            if sp is not None:
                out.append(sp)
        return out


# --------------------------------------------------------------------------- #
# control point derivation / parameter fitting
# --------------------------------------------------------------------------- #

def polyline_lengths(points, closed=False):
    """Cumulative arc lengths of a polyline (optionally closed)."""
    cum = [0.0]
    n = len(points)
    if n < 2:
        return cum
    pairs = list(zip(points[:-1], points[1:]))
    if closed:
        pairs.append((points[-1], points[0]))
    for a, b in pairs:
        cum.append(cum[-1] + (Vector(b) - Vector(a)).length)
    return cum


def _pick_even_by_index(points, count):
    n = len(points)
    count = max(2, min(count, n))
    if n == 1:
        return [points[0].copy() for _ in range(count)]
    step = (n - 1) / (count - 1)
    return [Vector(points[min(n - 1, int(round(i * step)))]) for i in range(count)]


def derive_control_points(points, count, closed, use_density=False):
    """Control points for a loop/chain.

    ``points``      - world space positions along the chain (order matters)
    ``use_density`` - True: keep the vertex distribution of the mesh (dense
                      areas get dense control points); False: even arc length.
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    if n == 0:
        return []
    if n <= 2:
        return [p.copy() for p in pts]

    if use_density:
        return _pick_even_by_index(pts, count)

    cum = polyline_lengths(pts, closed)
    total = cum[-1]
    if total <= EPS:
        return _pick_even_by_index(pts, count)

    count = max(2, int(count))
    if closed:
        targets = [total * i / count for i in range(count)]
    else:
        targets = [total * i / (count - 1) for i in range(count)]

    out = []
    seg = 0
    for s in targets:
        while seg < len(cum) - 2 and cum[seg + 1] < s:
            seg += 1
        length = cum[seg + 1] - cum[seg]
        f = 0.0 if length <= EPS else (s - cum[seg]) / length
        a = pts[seg]
        b = pts[(seg + 1) % n]
        out.append(a.lerp(b, f))
    return out


def length_params(points, closed=False):
    """Normalised arc length position of every point along its own chain.

    This is how Mio3 Flex maps a loop onto its curve: vertex *i* keeps its own
    fraction of the loop length, so a curved loop produces a curve that hugs it
    and the vertices follow that curve.
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    if n < 2:
        return [0.0] * n
    cum = polyline_lengths(pts, closed)
    total = cum[-1]
    if total <= EPS:
        if closed:
            return [i / n for i in range(n)]
        return [i / (n - 1) for i in range(n)]
    return [cum[i] / total for i in range(n)]


def auto_control_count(points, closed=False, roundness=1.0, segments=12, tolerance=0.02,
                       minimum=3, maximum=16, use_density=False, probes=48):
    """Fewest control points whose curve hugs ``points`` within ``tolerance``.

    ``tolerance`` is a share of the chain length (0.02 = 2%). This is what keeps
    the imaginary curve glued to an already curved edge loop instead of cutting
    corners across it.
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    if n <= 2:
        return max(2, n)
    total = polyline_lengths(pts, closed)[-1]
    if total <= EPS:
        return max(2, min(n, minimum))
    limit = max(1e-4, float(tolerance)) * total

    # a subset is enough to judge the deviation and keeps this fast
    step = max(1, int(math.ceil(n / float(max(8, probes)))))
    sample = pts[::step]
    if sample[-1] is not pts[-1]:
        sample.append(pts[-1])

    start = max(3 if closed else 2, minimum)
    best = min(maximum, max(start, n))
    for count in range(start, best + 1):
        curve = Curve(derive_control_points(pts, count, closed, use_density),
                      closed, roundness, segments)
        if max(curve.closest_param(p)[1] for p in sample) <= limit:
            return count
    return best


# --------------------------------------------------------------------------- #
# even distribution of points on a curve
# --------------------------------------------------------------------------- #

def _param_positions(curve, params):
    return [curve.point_at_param(p) for p in params]


def _param_gaps(curve, params, closed):
    points = _param_positions(curve, params)
    n = len(points)
    if closed:
        return [(points[(i + 1) % n] - points[i]).length for i in range(n)]
    return [(points[i + 1] - points[i]).length for i in range(n - 1)]


def _param_spread(curve, params, closed):
    gaps = _param_gaps(curve, params, closed)
    if not gaps:
        return 0.0
    return max(gaps) - min(gaps)


def even_curve_params(curve, count, closed=False, equalize=True, iterations=128):
    """``count`` normalised parameters spread evenly along ``curve``.

    ``equalize`` relaxes the parameters until the *straight line* gaps between
    the resulting points are equal (equal arc length alone still leaves
    different chords where the curve bends).
    """
    n = int(count)
    if n <= 1:
        return [0.0]
    base = [i / n for i in range(n)] if closed else [i / (n - 1) for i in range(n)]
    if not equalize or n < 3 or curve.total <= EPS:
        return base

    total = curve.total
    best = list(base)
    best_spread = _param_spread(curve, best, closed)
    params = list(base)
    for _ in range(max(0, int(iterations))):
        gaps = _param_gaps(curve, params, closed)
        proposal = list(params)
        indices = range(1, n) if closed else range(1, n - 1)
        for i in indices:
            proposal[i] = params[i] + 0.5 * (gaps[i] - gaps[i - 1]) / total
        for i in range(1, n):
            lower = proposal[i - 1] + 1e-9
            upper = 1.0 if i == n - 1 else proposal[i + 1] - 1e-9
            if upper < lower:
                upper = lower
            proposal[i] = max(lower, min(upper, proposal[i]))
        params = proposal
        spread = _param_spread(curve, params, closed)
        if spread < best_spread:
            best_spread = spread
            best = list(params)
        if best_spread <= 1e-12:
            break
    return best


# --------------------------------------------------------------------------- #
# edge loop / chain extraction
# --------------------------------------------------------------------------- #

def _visible(vert):
    """False when the vertex is hidden in the viewport / edit mode."""
    if getattr(vert, "hide", False):
        return False
    # a vertex whose every face is hidden is not visible either, even when its own
    # hide flag is clear (hiding faces in edit mode leaves the vertices flagged off)
    faces = getattr(vert, "link_faces", ())
    if faces and all(getattr(face, "hide", False) for face in faces):
        return False
    return True


def _edge_visible(edge):
    """False for hidden edges and for edges that only belong to hidden faces."""
    if getattr(edge, "hide", False):
        return False
    if not _visible(edge.verts[0]) or not _visible(edge.verts[1]):
        return False
    return True


def _adjacency(bm):
    """Selected-edge adjacency: ``{vert index: [neighbour index, ...]}``.

    Only *selected edges* are walked. Two selected vertices that are joined by an
    unselected edge (the ends of two rails on a strip, for example) must not be
    glued into one chain - that is what used to merge separate loops. Hidden
    geometry is skipped entirely: the tool never works on what cannot be seen.
    """
    bm.verts.index_update()
    bm.edges.index_update()
    sel = [v for v in bm.verts if v.select and _visible(v)]
    selset = set(v.index for v in sel)
    pairs = {}
    for v in sel:
        for e in v.link_edges:
            o = e.other_vert(v)
            if o.index not in selset or o is v:
                continue
            if not _edge_visible(e):
                continue
            key = (v.index, o.index) if v.index < o.index else (o.index, v.index)
            if key in pairs:
                continue
            pairs[key] = bool(e.select)

    # a pure vertex selection (object mode, or freshly flagged verts) has no edge
    # flags at all - then every pair of selected neighbours counts
    use_edges = any(pairs.values())
    adj = {v.index: [] for v in sel}
    for (a, b), selected in pairs.items():
        if use_edges and not selected:
            continue
        adj[a].append(b)
        adj[b].append(a)
    return adj


def build_chains(bm):
    """Ordered chains of the selected edges -> ``[(indices, closed), ...]``.

    The result is independent of the vertex indices: chains start at a
    geometrically defined point and follow the straightest continuation, so
    running the tool twice on the same selection gives the same curve.
    """
    adj = _adjacency(bm)
    if not adj:
        return []

    coords = {v.index: v.co.copy() for v in bm.verts if v.index in adj}

    def rank(i):
        co = coords[i]
        return (round(co.x, 6), round(co.y, 6), round(co.z, 6))

    def step_dir(prev, cur):
        if prev is None:
            return None
        d = coords[cur] - coords[prev]
        return d.normalized() if d.length > EPS else None

    used = set()

    def key(a, b):
        return (a, b) if a < b else (b, a)

    def has_unused(i):
        return any(key(i, j) not in used for j in adj.get(i, ()))

    ends = [i for i in adj if len(set(adj[i])) <= 1]
    starts = sorted(ends, key=rank) + sorted((i for i in adj if i not in ends), key=rank)

    chains = []
    for start in starts:
        while has_unused(start):
            chain = [start]
            prev = None
            cur = start
            closed = False
            guard = 0
            while guard < len(adj) + 2:
                guard += 1
                cands = [j for j in adj[cur] if key(cur, j) not in used]
                if not cands:
                    break
                fresh = [j for j in cands if j not in chain]
                if not fresh:
                    if start in cands and len(chain) > 2:
                        used.add(key(cur, start))
                        closed = True
                    break
                if start in fresh and len(chain) > 2:
                    nxt = start
                else:
                    # keep going straight instead of hopping onto a crossing loop
                    direction = step_dir(prev, cur)

                    def cost(j, direction=direction, cur=cur):
                        if direction is None:
                            return (0.0, rank(j))
                        nd = coords[j] - coords[cur]
                        if nd.length <= EPS:
                            return (2.0, rank(j))
                        return (round(1.0 - direction.dot(nd.normalized()), 6), rank(j))

                    nxt = min(fresh, key=cost)
                used.add(key(cur, nxt))
                if nxt == start:
                    closed = True
                    break
                chain.append(nxt)
                prev, cur = cur, nxt
            if chain:
                chains.append((chain, closed))
    return chains


def split_closed_at_corners(points, angle_degrees=45.0, exact=2):
    """Corner indices of a closed polygon that should split it into rails.

    Only exactly ``exact`` sharp corners trigger a split, so ordinary loops (a
    square ring has four corners, a circle has none) stay whole.
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    if n < 6:
        return []
    corners = []
    for i in range(n):
        prev = pts[(i - 1) % n]
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        a = cur - prev
        b = nxt - cur
        if a.length <= EPS or b.length <= EPS:
            continue
        angle = math.degrees(a.angle(b, 0.0))
        if angle > angle_degrees:
            corners.append(i)
    if len(corners) != exact:
        return []
    return corners


def ribbon_cut_edges(points, closed=True, pinch_ratio=0.4, min_arc_gap=0.2):
    """Edge indices that cut a ribbon boundary into its two rails.

    The boundary of a ribbon is one closed loop of two long rails joined by two
    much shorter ends. Those two ends are found as edges that are several times
    shorter than their neighbours and that sit roughly opposite each other, so a
    square ring (all edges equal) or a bumpy loop is left alone.
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    if not closed or n < 6:
        return []
    lengths = [(pts[(i + 1) % n] - pts[i]).length for i in range(n)]
    if sum(lengths) <= EPS:
        return []
    pinched = []
    for i in range(n):
        prev = lengths[(i - 1) % n]
        cur = lengths[i]
        nxt = lengths[(i + 1) % n]
        reference = min(prev, nxt)
        if reference <= EPS:
            reference = max(prev, nxt)
        if reference > EPS and (cur / reference) < pinch_ratio:
            pinched.append(i)
    if len(pinched) != 2:
        return []
    first, second = pinched
    arc = (second - first) % n
    if arc < n * min_arc_gap or (n - arc) < n * min_arc_gap:
        return []
    return [first, second]


def split_closed_chain(indices, cut_a, cut_b):
    """Two open chains from a closed chain, cut across the edges ``cut_a``/``cut_b``."""
    n = len(indices)
    if n < 4:
        return list(indices), []
    a, b = sorted((int(cut_a) % n, int(cut_b) % n))
    if a == b:
        return list(indices), []
    part_a = [indices[(a + 1 + k) % n] for k in range(b - a)]
    part_b = [indices[(b + 1 + k) % n] for k in range((a - b) % n)]
    return part_a, part_b


def bfs_from_vertices(bm, indices, limit=8, blocked=None, hard_edges=None):
    """Breadth first walk over mesh edges starting from a set of vertices.

    Returns ``(distances, sources)``: for every reached vertex the number of edges
    to the nearest start vertex, and which start vertex that was. FFlex uses it to
    blend the loops that lie *between* two fitted curves.

    ``blocked`` vertices may be reached (their distance is measured) but the walk
    never expands through them - that is how the band between two curves is closed
    off, so loops lying *outside* the band are never picked up.

    ``hard_edges`` are edges the walk must not cross at all (sharp creases). They
    keep the band on one smooth sheet, so the thickness rim and the far wall of a
    solid stay untouched.
    """
    distances = {}
    sources = {}
    queue = deque()
    stops = set(blocked or ())
    for index in indices:
        if index not in distances:
            distances[index] = 0
            sources[index] = index
            queue.append(index)
    bm.verts.ensure_lookup_table()
    while queue:
        current = queue.popleft()
        step = distances[current]
        if step >= limit:
            continue
        if current in stops:
            continue
        vert = bm.verts[current]
        for edge in vert.link_edges:
            other_vert = edge.other_vert(vert)
            other = other_vert.index
            if not _edge_visible(edge) or not _visible(other_vert):
                continue        # hidden geometry is not part of the surface any more
            if hard_edges:
                low, high = (current, other) if current < other else (other, current)
                if (low, high) in hard_edges:
                    continue
            if other not in distances:
                distances[other] = step + 1
                sources[other] = sources[current]
                queue.append(other)
    return distances, sources


def crease_edges(bm, angle_limit):
    """Edges whose two faces meet at a sharper angle than ``angle_limit``.

    Used to keep the interpolation on one sheet of the surface: the rim of a
    thickness (and anything behind it) is separated by such creases.
    """
    hard = set()
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        try:
            angle = edge.calc_face_angle(0.0)
        except ValueError:
            continue
        if angle > angle_limit:
            first = edge.verts[0].index
            second = edge.verts[1].index
            hard.add((first, second) if first < second else (second, first))
    return hard


def extend_chain(bm, indices, closed):
    """Grow a chain (in both directions) through degree-2 vertices of the mesh."""
    if closed:
        return list(indices), True

    def degree(v):
        return len([e for e in v.link_edges if _edge_visible(e)])

    bm.verts.ensure_lookup_table()
    chain = list(indices)
    inside = set(chain)
    closed = False
    for direction in (True, False):
        while True:
            end = bm.verts[chain[-1] if direction else chain[0]]
            if degree(end) != 2:
                break
            other_end = chain[0] if direction else chain[-1]
            nxt = None
            for e in end.link_edges:
                o = e.other_vert(end)
                if not _edge_visible(e) or not _visible(o):
                    continue
                if o.index not in inside:
                    nxt = o
                    break
                if o.index == other_end and len(chain) > 2:
                    closed = True
            if nxt is None:
                break
            if direction:
                chain.append(nxt.index)
            else:
                chain.insert(0, nxt.index)
            inside.add(nxt.index)
        if closed:
            break
    return chain, closed


# --------------------------------------------------------------------------- #
# even spacing
# --------------------------------------------------------------------------- #

def _path_point(walk, cum, total, s):
    """Point at arc length ``s`` along the polyline ``walk``."""
    s = 0.0 if s < 0.0 else (total if s > total else s)
    i = bisect_right(cum, s) - 1
    i = max(0, min(i, len(walk) - 2))
    length = cum[i + 1] - cum[i]
    f = 0.0 if length <= EPS else (s - cum[i]) / length
    return walk[i].lerp(walk[i + 1], f)


def _gap_spread(points, closed):
    if closed:
        walk = list(points) + [points[0]]
    else:
        walk = list(points)
    gaps = [(b - a).length for a, b in zip(walk[:-1], walk[1:])]
    if not gaps:
        return 0.0
    return max(gaps) - min(gaps)


def distribute_evenly(points, closed=False, equalize=True, iterations=128):
    """Redistribute ``points`` with an equal gap between neighbours.

    The points are slid along their own polyline path, so the shape of the chain
    is kept. Open chains keep their end points, closed loops keep ``points[0]``
    as the seam.

    ``equalize`` additionally relaxes the *chord* distances so that the gaps
    between the moved vertices really are equal (equal arc length alone leaves
    slightly different chords on cornered chains).
    """
    pts = [Vector(p) for p in points]
    n = len(pts)
    if n < 3:
        return pts

    walk = pts + [pts[0].copy()] if closed else list(pts)
    cum = polyline_lengths(walk, False)
    total = cum[-1]
    if total <= EPS:
        return pts

    # arc length coordinate of every original point (index 0 stays the seam)
    if closed:
        targets = [total * i / n for i in range(n)]
    else:
        targets = [total * i / (n - 1) for i in range(n)]

    best_u = list(targets)
    best = _gap_spread([_path_point(walk, cum, total, u) for u in best_u], closed)

    if equalize:
        u = list(targets)
        for _ in range(max(0, int(iterations))):
            positions = [_path_point(walk, cum, total, x) for x in u]
            if closed:
                gaps = [(positions[(i + 1) % n] - positions[i]).length for i in range(n)]
            else:
                gaps = [(positions[i + 1] - positions[i]).length for i in range(n - 1)]
            proposal = list(u)
            if closed:
                for i in range(1, n):
                    proposal[i] = u[i] + 0.5 * (gaps[i] - gaps[i - 1])
            else:
                for i in range(1, n - 1):
                    proposal[i] = u[i] + 0.5 * (gaps[i] - gaps[i - 1])
            # keep the order (and therefore the topology) intact
            for i in range(1, n):
                lower = proposal[i - 1] + 1e-9
                upper = total if i == n - 1 else proposal[i + 1] - 1e-9
                if upper < lower:
                    upper = lower
                proposal[i] = max(lower, min(upper, proposal[i]))
            u = proposal
            spread = _gap_spread([_path_point(walk, cum, total, x) for x in u], closed)
            if spread < best:
                best = spread
                best_u = list(u)
            if best <= 1e-12:
                break

    return [_path_point(walk, cum, total, x) for x in best_u]


def distribute_evenly_in_frame(points, frame, closed=False, equalize=True):
    """Even spacing measured inside the work frame plane.

    The in plane component is redistributed evenly, the axis component of every
    point is kept as it was (the chain is *sheared*, not flattened).
    """
    pts = [Vector(p) for p in points]
    if len(pts) < 3:
        return pts
    planar = [frame.to_plane(p) for p in pts]
    flat = [frame.from_plane(a, b, 0.0) for (a, b, _c) in planar]
    spread = distribute_evenly(flat, closed, equalize)
    out = []
    for new_p, (_a, _b, c) in zip(spread, planar):
        out.append(new_p + frame.n * c)
    return out


def chain_metrics(points):
    """``(total length, min gap, max gap)`` of a polyline."""
    pts = [Vector(p) for p in points]
    if len(pts) < 2:
        return 0.0, 0.0, 0.0
    gaps = [(b - a).length for a, b in zip(pts[:-1], pts[1:])]
    return sum(gaps), min(gaps), max(gaps)
