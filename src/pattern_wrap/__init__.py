# SPDX-License-Identifier: MIT
"""Pattern Wrap — накладывает мэш с узором на деформированный мэш и обрезает края.

Основной режим (PROJECT) работает так:
  1. берётся мэш-паттерн (как он выглядит в сцене, вместе с модификаторами);
  2. каждая его вершина простреливается лучом вдоль выбранной локальной оси
     паттерна в сторону целевого (деформированного) мэша;
  3. попавшие вершины садятся точно на поверхность цели, при этом собственный
     рельеф паттерна сохраняется (высота вершины над плоскостью паттерна
     откладывается вдоль нормали поверхности);
  4. промахнувшиеся вершины вместе со своими гранями удаляются — поэтому за
     силуэтом цели ничего не торчит, а границы получаются ровные.

Логика отделена от UI: функция wrap_pattern() работает под blender --background.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector, bvhtree, kdtree


PW_TAG = "pattern_wrap_result"
PW_SOURCE = "pattern_wrap_source"
MOD_NAME = "Pattern Wrap"

AXIS_VECTORS = {
    'NEG_Z': Vector((0.0, 0.0, -1.0)),
    'POS_Z': Vector((0.0, 0.0, 1.0)),
    'NEG_Y': Vector((0.0, -1.0, 0.0)),
    'POS_Y': Vector((0.0, 1.0, 0.0)),
    'NEG_X': Vector((-1.0, 0.0, 0.0)),
    'POS_X': Vector((1.0, 0.0, 0.0)),
}

AXIS_ITEMS = (
    ('AUTO', "Авто (вид на цель)",
     "Лучи идут на цель с той стороны, куда смотрят её нормали — лучший вид силуэта"),
    ('NEG_Z', "-Z (сверху вниз)", "Проекция вдоль локальной -Z паттерна"),
    ('POS_Z', "+Z (снизу вверх)", "Проекция вдоль локальной +Z паттерна"),
    ('NEG_Y', "-Y", "Проекция вдоль локальной -Y паттерна"),
    ('POS_Y', "+Y", "Проекция вдоль локальной +Y паттерна"),
    ('NEG_X', "-X", "Проекция вдоль локальной -X паттерна"),
    ('POS_X', "+X", "Проекция вдоль локальной +X паттерна"),
)

MODE_ITEMS = (
    ('PROJECT', "Проекция + обрезка", "Спроецировать паттерн на цель и обрезать по силуэту (рекомендуется)"),
    ('UV', "UV-развёртка + Surface Deform",
     "Развернуть цель по UV в плоскость, подогнать и обрезать по ней узор, "
     "привязать Surface Deform и вернуть цель вместе с узором на место"),
    ('TRIM_ONLY', "Только обрезка", "Ничего не двигать, только удалить то, что вышло за силуэт цели"),
    ('SURFACE_DEFORM', "Surface Deform + обрезка", "Обрезать, затем привязать паттерн модификатором Surface Deform"),
)


class WrapError(Exception):
    """Понятная пользователю ошибка вместо трейсбека."""


# --------------------------------------------------------------------------- #
#  Ядро: работает под blender --background, не требует UI
# --------------------------------------------------------------------------- #

def _axis_vector(key):
    if key in AXIS_VECTORS:
        return AXIS_VECTORS[key].copy()
    raise WrapError("Неизвестная ось проекции: %r" % (key,))


def _triangle_indices(me):
    """Индексы треугольников мэша (с запасным вариантом через полигоны)."""
    tris = []
    try:
        tris = [tuple(t.vertices) for t in me.loop_triangles]
    except Exception:
        tris = []
    if not tris:
        for poly in me.polygons:
            idx = list(poly.vertices)
            for i in range(1, len(idx) - 1):
                tris.append((idx[0], idx[i], idx[i + 1]))
    return tris


def evaluated_mesh_world(obj, depsgraph):
    """Триангулированная геометрия объекта с модификаторами, в мировых координатах.

    Если объект отключён в вьюпортах, лежит в скрытой коллекции или его
    вычисленный мэш пуст, берётся базовый мэш — инструмент не должен падать
    из-за состояния видимости.
    """
    ev = obj.evaluated_get(depsgraph)
    verts, tris = [], []
    me = ev.to_mesh()
    if me is not None:
        try:
            mw = ev.matrix_world.copy()
            verts = [mw @ v.co for v in me.vertices]
            tris = _triangle_indices(me)
        finally:
            ev.to_mesh_clear()
    if not verts:
        base = obj.data
        if base is not None and len(base.vertices):
            mw = obj.matrix_world.copy()
            verts = [mw @ v.co for v in base.vertices]
            tris = _triangle_indices(base)
    if not verts:
        raise WrapError(
            "У объекта «%s» нет геометрии: проверьте, что мэш не пустой и объект "
            "включён в вьюпортах" % obj.name)
    return verts, tris


class VisibilityGuard:
    """Временно включает объект и его коллекции, чтобы он вычислялся.

    При выходе возвращает всё как было (в том числе скрытые/исключённые
    коллекции в слое вида).
    """

    def __init__(self, obj):
        self.obj = obj
        self.saved = []
        self.active = False

    def _restore_note(self, target, attr, value, note):
        self.saved.append((target, attr, value, note))

    def __enter__(self):
        obj = self.obj
        if obj is None:
            return self
        if getattr(obj, "hide_viewport", False):
            self._restore_note(obj, "hide_viewport", obj.hide_viewport,
                               "объект был отключён в вьюпортах")
            obj.hide_viewport = False
        for coll in list(getattr(obj, "users_collection", []) or []):
            if getattr(coll, "hide_viewport", False):
                self._restore_note(coll, "hide_viewport", coll.hide_viewport,
                                   "коллекция была отключена в вьюпортах")
                coll.hide_viewport = False
        try:
            view_layer = bpy.context.view_layer
        except Exception:
            view_layer = None
        if view_layer is not None:
            for layer in self._walk(view_layer.layer_collection, obj):
                if layer.exclude:
                    self._restore_note(layer, "exclude", layer.exclude,
                                       "коллекция была исключена из слоя вида")
                    layer.exclude = False
                if layer.hide_viewport:
                    self._restore_note(layer, "hide_viewport", layer.hide_viewport,
                                       "коллекция была скрыта в слое вида")
                    layer.hide_viewport = False
        self.active = bool(self.saved)
        if self.active:
            bpy.context.view_layer.update()
        return self

    def _walk(self, layer, obj):
        found = []
        try:
            if obj.name in layer.collection.objects:
                found.append(layer)
        except Exception:
            pass
        for child in layer.children:
            found.extend(self._walk(child, obj))
        return found

    def __exit__(self, *exc_info):
        for target, attr, value, _note in reversed(self.saved):
            try:
                setattr(target, attr, value)
            except Exception:
                pass
        if self.saved:
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass
        return False

    def notes(self):
        return sorted({note for _t, _a, _v, note in self.saved})


def make_bvh_in_local_space(target, pattern, depsgraph):
    """BVH цели, приведённый в локальные координаты паттерна.

    evaluated_mesh_world() отдаёт точки уже в мире, поэтому здесь нужна только
    матрица паттерна (умножать ещё и на матрицу цели нельзя — это сдвинуло бы
    цель второй раз).
    """
    t_verts_w, t_tris = evaluated_mesh_world(target, depsgraph)
    if not t_tris:
        raise WrapError("У цели «%s» нет граней" % target.name)
    to_local = pattern.matrix_world.inverted()
    t_verts = [to_local @ v for v in t_verts_w]
    bvh = bvhtree.BVHTree.FromPolygons(t_verts, t_tris, all_triangles=True)
    return bvh, t_verts


def object_world_bounds(obj, depsgraph):
    """(центр, размеры, min, max) вычисленного мэша в мировых координатах."""
    verts, _tris = evaluated_mesh_world(obj, depsgraph)
    mn = Vector((min(v.x for v in verts), min(v.y for v in verts), min(v.z for v in verts)))
    mx = Vector((max(v.x for v in verts), max(v.y for v in verts), max(v.z for v in verts)))
    return (mn + mx) * 0.5, (mx - mn), mn, mx


def object_local_bounds(obj, depsgraph):
    """(центр, размеры) вычисленного мэша в локальных координатах объекта."""
    verts, _tris = evaluated_mesh_world(obj, depsgraph)
    inv = obj.matrix_world.inverted()
    local = [inv @ v for v in verts]
    mn = Vector((min(v.x for v in local), min(v.y for v in local), min(v.z for v in local)))
    mx = Vector((max(v.x for v in local), max(v.y for v in local), max(v.z for v in local)))
    return (mn + mx) * 0.5, (mx - mn)


def average_normal_world(obj, depsgraph):
    """Взвешенная по площади средняя нормаль (мировые координаты).

    Для незамкнутой поверхности её направление — это направление, с которого
    цель видна «в плане» лучше всего.
    """
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        m3 = ev.matrix_world.to_3x3()
        total = Vector()
        area = 0.0
        for poly in me.polygons:
            a = poly.area
            area += a
            total += (m3 @ poly.normal) * a
    finally:
        ev.to_mesh_clear()
    if area <= 0.0:
        return Vector()
    return total / area


def resolve_world_direction(pattern, target, axis, depsgraph):
    """Мировое направление лучей (от узора к цели) для выбранного режима."""
    if axis == 'AUTO':
        normal = average_normal_world(target, depsgraph)
        if normal.length > 0.15:
            # смотрим на цель с той стороны, куда смотрят её нормали
            return (-normal).normalized()
        t_center, _te, _a, _b = object_world_bounds(target, depsgraph)
        p_center, _pe, _c, _d = object_world_bounds(pattern, depsgraph)
        delta = t_center - p_center
        if delta.length > 1e-9:
            return delta.normalized()
        return Vector((0.0, 0.0, -1.0))
    return (pattern.matrix_world.to_3x3() @ _axis_vector(axis)).normalized()


def bake_object_transform(obj, matrix, origin=None):
    """Запекает матрицу в мэш объекта, оставляя у объекта чистый трансформ.

    После вызова rotation = 0, scale = 1, а location = origin (по умолчанию ноль),
    то есть мировые координаты те же, но объект больше не «держит» поворот:
    сбросить его в панели N ничего не сломает. Отражение (det < 0) разворачивает
    грани, чтобы нормали смотрели наружу.
    """
    origin = Vector((0.0, 0.0, 0.0)) if origin is None else Vector(origin)
    me = obj.data
    if me.shape_keys is not None:
        # с ключами формы bmesh-перезапись мэша запрещена — оставляем трансформ
        obj.matrix_world = matrix
        return False
    flip = matrix.to_3x3().determinant() < 0.0
    bm = bmesh.new()
    bm.from_mesh(me)
    for vert in bm.verts:
        vert.co = (matrix @ vert.co) - origin
    if flip:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()
    me.update()
    obj.matrix_world = Matrix.Translation(origin)
    return True


def place_pattern_as_decal(pattern, target, direction, depsgraph,
                           rotate=True, margin=0.04, fit=False, fit_mode='GROW',
                           gap=None, bounds=None, points=None, flip=False,
                           frame=None, allow_swap=True):
    """Ставит узор перед целью перпендикулярно лучам (двигает и, если надо, крутит).

    gap — расстояние от плоскости цели до ближайшей точки узора;
    отрицательное значение утапливает узор внутрь цели;
    если не задано, берётся margin * размер цели.
    bounds/points позволяют задать габариты и силуэт вместо самой цели
    (нужно для UV-развёртки).
    frame — готовый базис (u, v, n) для ориентации узора: так UV-режим кладёт
    узор по осям UV-развёртки, без самовольного разворота на 180°.
    allow_swap — можно ли менять местами U и V ради лучшего покрытия силуэта.
    flip — зеркалит узор по глубине (отражение относительно плоскости цели).
    Работает с объектом-копией: оригинал остаётся на месте.
    """
    d = Vector(direction).normalized()
    if bounds is not None:
        t_center, t_ext = Vector(bounds[0]), Vector(bounds[1])
    else:
        t_center, t_ext, _a, _b = object_world_bounds(target, depsgraph)
    p_center, _pe, _c, _d = object_world_bounds(pattern, depsgraph)
    local_center, local_ext = object_local_bounds(pattern, depsgraph)

    if points is not None:
        t_pts = [Vector(p) for p in points]
    else:
        t_pts, _tt = evaluated_mesh_world(target, depsgraph)
    # опорный радиус цели вдоль лучей считаем по точкам: у наклонённой плоскости
    # axis-aligned габарит даёт сильно завышенное значение
    r_target = max((abs((p - t_center).dot(d)) for p in t_pts), default=0.0)

    loc, rot_quat, scale = pattern.matrix_world.decompose()

    coverage = None
    if rotate:
        if frame is not None:
            # ориентацию задаёт вызывающий (UV-развёртка: оси UV-пространства)
            n = Vector(frame[2]).normalized()
            u = Vector(frame[0])
            u = (u - n * u.dot(n))
            if u.length < 1e-9:
                u = Vector((1.0, 0.0, 0.0))
            u.normalize()
            v = n.cross(u).normalized()
        else:
            # локальная +Z узора смотрит на цель
            n = -d
            ref = Vector((0.0, 0.0, 1.0))
            if abs(ref.dot(n)) > 0.95:
                ref = Vector((0.0, 1.0, 0.0))
            u = ref.cross(n)
            if u.length < 1e-6:
                u = Vector((1.0, 0.0, 0.0))
            u.normalize()
            # «своя» ось X узора: сохраняем авторский крен, а не разворачиваем
            own = (pattern.matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0)))
            own = own - n * own.dot(n)
            if own.length > 1e-6:
                u = own.normalized()
            v = n.cross(u).normalized()

        # Длинную сторону узора кладём вдоль длинной стороны силуэта цели:
        # иначе лента ложится поперёк и покрывает только полосу.
        half_u = max(abs((p - t_center).dot(u)) for p in t_pts)
        half_v = max(abs((p - t_center).dot(v)) for p in t_pts)
        px = 0.5 * local_ext.x * abs(scale.x)
        py = 0.5 * local_ext.y * abs(scale.y)

        def score(pat_u, pat_v, sil_u, sil_v):
            ru = pat_u / max(sil_u, 1e-9)
            rv = pat_v / max(sil_v, 1e-9)
            return min(ru, rv) - 0.15 * abs(math.log(max(ru, 1e-9) / max(rv, 1e-9)))

        straight = score(px, py, half_u, half_v)
        swapped = score(py, px, half_u, half_v)
        if allow_swap and swapped > straight:
            u, v = v, u
            half_u, half_v = half_v, half_u

        # Необязательно: подогнать узор под силуэт (в UV-режиме — и уменьшить тоже).
        fit_factor = 1.0
        if fit:
            ru = px / max(half_u, 1e-9)
            rv = py / max(half_v, 1e-9)
            if fit_mode == 'BOTH':
                fit_factor = max(1.0 / max(ru, 1e-9), 1.0 / max(rv, 1e-9))
            else:
                fit_factor = max(1.0 / max(ru, 1e-9), 1.0 / max(rv, 1e-9), 1.0)
            if abs(fit_factor - 1.0) > 1e-9:
                scale = scale * fit_factor
                px *= fit_factor
                py *= fit_factor
        coverage = (px / max(half_u, 1e-9), py / max(half_v, 1e-9))

        if flip:
            # отражение по глубине: меняем знак только у нормали, оставляя
            # U и V на месте — получается зеркало относительно плоскости узора
            n = -n
        rot = Matrix((u, v, n)).transposed().to_4x4()
    else:
        rot = rot_quat.to_matrix().to_4x4()
        fit_factor = 1.0

    rot3 = rot.to_3x3()
    scale_m = Matrix.Diagonal(scale).to_4x4()

    r_pattern = 0.0
    for sx in (-0.5, 0.5):
        for sy in (-0.5, 0.5):
            for sz in (-0.5, 0.5):
                offset = Vector((sx * local_ext.x, sy * local_ext.y, sz * local_ext.z))
                world_offset = rot3 @ (Vector((offset.x * scale.x, offset.y * scale.y,
                                               offset.z * scale.z)))
                r_pattern = max(r_pattern, abs(world_offset.dot(d)))

    gap = margin * max(t_ext.length, 1e-6) if gap is None else float(gap)
    new_center = t_center - d * (r_target + r_pattern + gap)

    matrix = (Matrix.Translation(new_center) @ rot @ scale_m
              @ Matrix.Translation(-local_center))
    # трансформ запекаем в мэш: у результата остаётся только смещение, поэтому
    # сброс поворота/масштаба в панели N ничего не ломает
    bake_object_transform(pattern, matrix, origin=new_center)
    bpy.context.view_layer.update()

    return {
        'direction': d,
        'rotated': bool(rotate),
        'basis': (u, v, n) if rotate else None,
        'shift': new_center - p_center,
        'distance': (r_target + r_pattern + gap),
        'coverage': coverage,
        'fit_factor': fit_factor,
    }


UV_MOD_NAME = "uv flatten"
UV_GROUP_BASE = "uv flatten"
UV_ATTR = "pw_uv_flat"
UV_LEGACY_NAME = "UV"


def _group_input(node, name):
    for sock in node.outputs:
        if sock.name == name:
            return sock
    raise WrapError("В геонодах нет входа %r" % name)


def _group_output(node, name):
    for sock in node.inputs:
        if sock.name == name:
            return sock
    raise WrapError("В геонодах нет выхода %r" % name)


def uv_flatten_group_name(uv_name="UVMap"):
    return "%s: %s" % (UV_GROUP_BASE, uv_name)


def uv_flatten_group_is_valid(tree):
    """Проверяет, что группа развёртки собрана целиком.

    Битая (например, недосозданная прошлой версией) группа с одним Group Input
    выдаёт пустую геометрию — цель «сжимается в ноль». Такую группу нельзя
    использовать, её нужно пересобрать.
    """
    if tree is None or getattr(tree, "bl_idname", None) != 'GeometryNodeTree':
        return False
    kinds = [node.bl_idname for node in tree.nodes]
    for required in ('NodeGroupInput', 'NodeGroupOutput',
                     'GeometryNodeInputNamedAttribute', 'GeometryNodeSetPosition'):
        if required not in kinds:
            return False
    output = next((n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
    if output is None or not any(sock.is_linked for sock in output.inputs):
        return False
    setpos = next((n for n in tree.nodes if n.bl_idname == 'GeometryNodeSetPosition'), None)
    if setpos is None or not any(sock.is_linked for sock in setpos.inputs):
        return False
    return True


def build_uv_flatten_group(uv_name="UVMap"):
    """Геоноды «uv flatten»: раскладывают мэш по UV в плоскость.

    UV читаются прямо из UV-слоя цели на каждом вычислении, поэтому развёртка
    обновляется автоматически. Группа собирается атомарно (под временным именем)
    и проверяется на целостность: недосозданная группа не останется в файле.
    """
    name = uv_flatten_group_name(uv_name)
    tree = bpy.data.node_groups.get(name)
    if uv_flatten_group_is_valid(tree):
        for node in tree.nodes:
            if node.bl_idname == 'GeometryNodeInputNamedAttribute':
                node.inputs['Name'].default_value = uv_name
        return tree
    if tree is not None:
        # битую группу убираем и строим заново
        try:
            bpy.data.node_groups.remove(tree)
        except Exception:
            pass

    tree = bpy.data.node_groups.new(name + " (assembling)", 'GeometryNodeTree')
    try:
        tree.interface.new_socket(name="Geometry", in_out='INPUT',
                                  socket_type='NodeSocketGeometry')
        tree.interface.new_socket(name="Geometry", in_out='OUTPUT',
                                  socket_type='NodeSocketGeometry')
        n_in = tree.nodes.new('NodeGroupInput')
        n_in.location = (-450, 0)
        n_out = tree.nodes.new('NodeGroupOutput')
        n_out.location = (300, 0)
        n_attr = tree.nodes.new('GeometryNodeInputNamedAttribute')
        n_attr.location = (-180, -150)
        n_attr.data_type = 'FLOAT_VECTOR'
        n_attr.inputs['Name'].default_value = uv_name
        n_pos = tree.nodes.new('GeometryNodeSetPosition')
        n_pos.location = (50, 0)
        links = tree.links
        links.new(_group_input(n_in, "Geometry"), n_pos.inputs['Geometry'])
        links.new(n_attr.outputs['Attribute'], n_pos.inputs['Position'])
        links.new(n_pos.outputs['Geometry'], _group_output(n_out, "Geometry"))
        if not uv_flatten_group_is_valid(tree):
            raise WrapError("Не удалось собрать геоноды «uv flatten»")
    except Exception:
        try:
            bpy.data.node_groups.remove(tree)
        except Exception:
            pass
        raise
    tree.name = name
    return tree


def count_ambiguous_uv(target):
    """Сколько вершин цели имеют разные UV в своих углах (мэш не разорван по швам)."""
    me = target.data
    uv = me.uv_layers.active
    if uv is None or not uv.data:
        return 0
    per_vertex = {}
    for loop in me.loops:
        u = uv.data[loop.index].uv
        per_vertex.setdefault(loop.vertex_index, set()).add((round(u.x, 6), round(u.y, 6)))
    return sum(1 for options in per_vertex.values() if len(options) > 1)


def find_uv_flatten_modifier(target):
    """Только поиск модификатора «uv flatten» — ничего не меняет.

    Эту функцию можно вызывать из draw(): она не пишет в данные.
    """
    if target is None:
        return None
    for item in target.modifiers:
        if item.type == 'NODES' and item.name == UV_MOD_NAME:
            return item
    return None


def uv_flatten_state(target):
    """Развёрнута ли цель (только чтение, безопасно для draw())."""
    mod = find_uv_flatten_modifier(target)
    return bool(mod is not None and mod.show_viewport)


def uv_flatten_modifier(target, create=False):
    """Модификатор «uv flatten» на цели; с create=True может править данные.

    Меняет (создаёт/пересобирает) группу геонод, поэтому вызывать её можно
    только из операторов, но не из draw().
    """
    mod = find_uv_flatten_modifier(target)
    uv = target.data.uv_layers.active
    if uv is None:
        return mod
    group = build_uv_flatten_group(uv.name)
    if mod is None:
        if not create:
            return None
        mod = target.modifiers.new(UV_MOD_NAME, 'NODES')
        mod.show_viewport = True
        mod.show_render = True
    if mod.node_group is not group:
        mod.node_group = group
    return mod


def set_uv_flatten(target, unfolded):
    """Развернуть (True) или сложить (False) цель переключателем модификатора."""
    mod = uv_flatten_modifier(target, create=bool(unfolded))
    if mod is None:
        return False
    mod.show_viewport = bool(unfolded)
    mod.show_render = bool(unfolded)
    return True


def remove_uv_flatten(target):
    """Убирает служебные данные развёртки (модификатор, старый атрибут, старая нода)."""
    removed = []
    mod = find_uv_flatten_modifier(target)
    if mod is not None:
        target.modifiers.remove(mod)
        removed.append(mod.name)
    attr = target.data.attributes.get(UV_ATTR)
    if attr is not None:
        target.data.attributes.remove(attr)
        removed.append(UV_ATTR)
    # группа от прежних версий (без имени UV-слоя) больше не нужна
    legacy = bpy.data.node_groups.get(UV_GROUP_BASE)
    if legacy is not None and legacy.users == 0:
        bpy.data.node_groups.remove(legacy)
        removed.append(legacy.name)
    return removed


def evaluated_vertex_count(obj, depsgraph=None):
    """Сколько вершин в вычисленном мэше (0 — объект «схлопнулся»)."""
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        return len(me.vertices) if me is not None else 0
    finally:
        if me is not None:
            ev.to_mesh_clear()


def setup_uv_flatten(target):
    """Готовит модификатор «uv flatten» и считает статистику по UV."""
    uv = target.data.uv_layers.active
    if uv is None or not uv.data:
        raise WrapError("У цели «%s» нет активной UV-развёртки" % target.name)
    mod = uv_flatten_modifier(target, create=True)
    return mod, count_ambiguous_uv(target)


def relax_pattern_on_surface(pattern, target, steps=3, strength=0.5, depsgraph=None):
    """Убирает локальные «пинчи» переноса: релаксация узора по поверхности цели.

    Каждая вершина тянется к среднему соседей и возвращается на поверхность,
    сохраняя своё удаление от неё (то есть рельеф и офсет).
    """
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    t_verts, t_tris = evaluated_mesh_world(target, depsgraph)
    if not t_tris:
        raise WrapError("У цели «%s» нет граней" % target.name)
    bvh = bvhtree.BVHTree.FromPolygons(t_verts, t_tris, all_triangles=True)

    me = pattern.data
    mw = pattern.matrix_world.copy()
    inv = mw.inverted()
    neighbours = [[] for _ in me.vertices]
    for edge in me.edges:
        a, b = edge.vertices
        neighbours[a].append(b)
        neighbours[b].append(a)

    coords = [mw @ v.co for v in me.vertices]
    offsets = []
    for p in coords:
        _loc, nor, _idx, dist = bvh.find_nearest(p)
        offsets.append((dist if dist is not None else 0.0,
                        nor if nor is not None else Vector((0.0, 0.0, 1.0))))

    k = max(0.0, min(1.0, float(strength)))
    for _ in range(max(0, int(steps))):
        moved = list(coords)
        for i, p in enumerate(coords):
            if not neighbours[i]:
                continue
            avg = Vector()
            for j in neighbours[i]:
                avg += coords[j]
            avg /= float(len(neighbours[i]))
            moved[i] = p.lerp(avg, k)
        for i, p in enumerate(moved):
            loc, nor, _idx, _dist = bvh.find_nearest(p)
            if loc is not None:
                moved[i] = loc + nor * offsets[i][0]
        coords = moved

    for i, v in enumerate(me.vertices):
        v.co = inv @ coords[i]
    me.update()
    return len(me.vertices)


def apply_surface_deform(context, obj):
    """Запекает Surface Deform (нужно перед релаксацией)."""
    applied = []
    view_layer = context.view_layer
    prev = view_layer.objects.active
    try:
        for ob in view_layer.objects:
            ob.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        for mod in list(obj.modifiers):
            if mod.type == 'SURFACE_DEFORM':
                name = mod.name
                bpy.ops.object.modifier_apply(modifier=name)
                applied.append(name)
    finally:
        if prev is not None:
            try:
                view_layer.objects.active = prev
            except Exception:
                pass
    return applied


def measure_against_surface(pattern, target, *, depsgraph=None):
    """Максимальное удаление результата от поверхности цели.

    Считается по вычисленной (с модификаторами) геометрии — то есть уже после
    Surface Deform.
    """
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    t_verts_w, t_tris = evaluated_mesh_world(target, depsgraph)
    bvh = bvhtree.BVHTree.FromPolygons(t_verts_w, t_tris, all_triangles=True)
    ev = pattern.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        mw = ev.matrix_world.copy()
        max_dist = 0.0
        worst = 0
        for v in me.vertices:
            _loc, _n, _i, dist = bvh.find_nearest(mw @ v.co)
            if dist is not None:
                if dist > max_dist:
                    max_dist = dist
                    worst = v.index
    finally:
        ev.to_mesh_clear()
    return max_dist, worst


def _projection_direction(axis, bm, t_verts, pattern, target):
    """Направление по локальной оси узора (для режимов без автонаправления)."""
    return _axis_vector(axis)


def wrap_pattern(pattern, target, *, mode='PROJECT', axis='NEG_Z', subdiv=1,
                 relief_scale=1.0, surface_offset=0.0, trim_shrink=0.0,
                 trim_partial=True, align_normals=True, do_trim=True,
                 direction_world=None, depsgraph=None):
    """Наложить паттерн на цель и обрезать лишнее. Меняет мэш объекта pattern.

    Возвращает словарь со статистикой.
    """
    if pattern.type != 'MESH':
        raise WrapError("Паттерн «%s» — не мэш" % pattern.name)
    if target.type != 'MESH':
        raise WrapError("Цель «%s» — не мэш" % target.name)
    if pattern is target:
        raise WrapError("Паттерн и цель — один и тот же объект")

    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    bvh, t_verts = make_bvh_in_local_space(target, pattern, depsgraph)

    me = pattern.data
    bm = bmesh.new()
    bm.from_mesh(me)
    if not bm.verts or not bm.faces:
        bm.free()
        raise WrapError("У паттерна «%s» нет граней" % pattern.name)

    faces_original = len(bm.faces)

    # 1. Плотность: без неё проекция получается гранёной.
    levels = max(0, int(subdiv))
    for _ in range(levels):
        if not bm.edges:
            break
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    faces_subdivided = len(bm.faces)

    # 2. Направление проекции и ось рельефа.
    if direction_world is not None:
        world_dir = Vector(direction_world).normalized()
    elif axis == 'AUTO':
        world_dir = resolve_world_direction(pattern, target, 'AUTO', depsgraph)
    else:
        world_dir = None

    if world_dir is not None:
        direction = pattern.matrix_world.inverted().to_3x3() @ world_dir
        if direction.length < 1e-9:
            bm.free()
            raise WrapError("Не удалось определить направление проекции")
        direction.normalize()
    else:
        direction = _axis_vector(axis)
    up = -direction

    # 3. Радиус: гарантированно начинаем луч за пределами всей геометрии.
    pts = [v.co for v in bm.verts]
    pts.extend(t_verts)
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    reach = (mx - mn).length + 1.0
    start_off = -direction * reach
    ray_len = 2.0 * reach

    # 4. Рельеф паттерна считается до любых перемещений.
    coords_up = [v.co.dot(up) for v in bm.verts]
    base_up = min(coords_up)

    # 5. Лучи.
    hits = {}
    missed = []
    for v in bm.verts:
        origin = v.co + start_off
        loc, nor, _idx, _dist = bvh.ray_cast(origin, direction, ray_len)
        if loc is None:
            missed.append(v.index)
        else:
            hits[v.index] = (loc.copy(), nor.copy(), v.co.dot(up) - base_up)

    hit_before_shrink = len(hits)
    if not hits:
        bm.free()
        raise WrapError(
            "Ни одна вершина паттерна не попала в цель. Проверьте ось проекции "
            "и взаимное расположение объектов (паттерн должен «смотреть» на цель)."
        )

    # 6. Дополнительное поджатие края внутрь.
    drop = set(missed)
    if trim_shrink > 0.0 and missed:
        kd = kdtree.KDTree(len(missed))
        for i, vi in enumerate(missed):
            kd.insert(bm.verts[vi].co, i)
        kd.balance()
        for v in bm.verts:
            if v.index in drop:
                continue
            _co, _i, dist = kd.find(v.co)
            if dist is not None and dist < trim_shrink:
                drop.add(v.index)

    # 7. Перенос вершин на поверхность цели с сохранением рельефа.
    if mode == 'PROJECT':
        for v in bm.verts:
            hit = hits.get(v.index)
            if hit is None:
                continue
            loc, nor, height = hit
            normal = nor
            if align_normals and normal.dot(up) < 0.0:
                normal = -normal
            v.co = loc + normal * (height * relief_scale + surface_offset)

        # 7b. Контрольный проход уже по итоговым координатам: подъём вдоль
        # нормали мог увести вершину за силуэт — такие тоже убираем.
        for v in bm.verts:
            if v.index in drop:
                continue
            loc, _n, _i, _d = bvh.ray_cast(v.co + start_off, direction, ray_len)
            if loc is None:
                drop.add(v.index)

    # 8. Обрезка: удаляем грани, у которых есть промахнувшиеся вершины.
    faces_del = []
    if do_trim and drop:
        for face in bm.faces:
            n_miss = 0
            for v in face.verts:
                if v.index in drop:
                    n_miss += 1
            if n_miss and (trim_partial or n_miss == len(face.verts)):
                faces_del.append(face)

    if faces_del:
        bmesh.ops.delete(bm, geom=faces_del, context='FACES_ONLY')
        loose_edges = [e for e in bm.edges if not e.link_faces]
        if loose_edges:
            bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
        loose_verts = [v for v in bm.verts if not v.link_faces]
        if loose_verts:
            bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')

    faces_after = len(bm.faces)
    if faces_after == 0:
        bm.free()
        raise WrapError(
            "После обрезки не осталось ни одной грани — ничего не изменено. "
            "Ослабьте «Поджать край» или проверьте ось проекции."
        )

    bm.to_mesh(me)
    bm.free()
    me.update()

    # 9. Контроль результата: ничего не должно висеть в воздухе.
    max_dist = 0.0
    outside = 0
    for v in me.vertices:
        co = v.co
        _loc, _nor, _idx, dist = bvh.find_nearest(co)
        if dist is not None and dist > max_dist:
            max_dist = dist
        loc, _nor2, _idx2, _d2 = bvh.ray_cast(co + start_off, direction, ray_len)
        if loc is None:
            outside += 1

    stats = {
        'faces_original': faces_original,
        'faces_before': faces_subdivided,
        'faces_after': faces_after,
        'kept': faces_after / float(faces_subdivided) if faces_subdivided else 0.0,
        'vertices_hit': hit_before_shrink,
        'vertices_total': len(hits) + len(missed),
        'relief_max': (max(coords_up) - base_up) if coords_up else 0.0,
        'max_surface_distance': max_dist,
        'outside_silhouette': outside,
    }
    return stats


def format_stats(stats, mode='PROJECT'):
    lines = [
        "Граней: %d → разбито %d → осталось %d (%d%%)"
        % (stats['faces_original'], stats['faces_before'], stats['faces_after'],
           round(100.0 * stats['kept'])),
        "Попало вершин: %d из %d" % (stats['vertices_hit'], stats['vertices_total']),
        "Рельеф паттерна: %.4f" % stats['relief_max'],
    ]
    if mode != 'TRIM_ONLY':
        lines.append("Макс. удаление от цели: %.4f" % stats['max_surface_distance'])
    if stats['outside_silhouette']:
        lines.append("Вне силуэта: %d вершин (проверьте параметры)" % stats['outside_silhouette'])
    else:
        lines.append("Вне силуэта: 0 — лишнего не торчит")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Работа с объектами Blender
# --------------------------------------------------------------------------- #

def bake_object_geometry(obj, depsgraph):
    """Запекает модификаторы объекта в его мэш (то, что видно в сцене)."""
    ev = obj.evaluated_get(depsgraph)
    new_me = bpy.data.meshes.new_from_object(
        ev, preserve_all_data_layers=True, depsgraph=depsgraph)
    old_me = obj.data
    new_me.name = old_me.name + "_baked"
    obj.data = new_me
    removed = len(obj.modifiers)
    try:
        obj.modifiers.clear()
    except Exception:
        while obj.modifiers:
            obj.modifiers.remove(obj.modifiers[0])
    if old_me.users == 0:
        bpy.data.meshes.remove(old_me)
    return removed


def remove_object(obj):
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def make_work_object(context, settings):
    """Объект, который будет обёрнут: копия паттерна либо сам паттерн."""
    src = settings.pattern
    depsgraph = context.evaluated_depsgraph_get()

    if not settings.use_copy:
        removed = bake_object_geometry(src, depsgraph)
        settings.result = None
        context.view_layer.update()
        return src, removed

    prev = settings.result
    if prev is not None and prev != src and prev.get(PW_TAG):
        remove_object(prev)
    settings.result = None

    ev = src.evaluated_get(depsgraph)
    new_me = bpy.data.meshes.new_from_object(
        ev, preserve_all_data_layers=True, depsgraph=depsgraph)
    new_me.name = src.data.name + "_wrap"
    ob = bpy.data.objects.new(src.name + "_wrap", new_me)
    ob.matrix_world = src.matrix_world.copy()
    ob[PW_TAG] = True
    ob[PW_SOURCE] = src.name
    collections = list(src.users_collection)
    if not collections:
        collections = [context.scene.collection]
    for coll in collections:
        _link_to(ob, coll)
    link_object_visible(ob, src)
    settings.result = ob
    context.view_layer.update()
    return ob, 0


def set_modifier_target(mod, target):
    """В 4.x/5.x цель Surface Deform называется по-разному."""
    for attr in ("target", "object"):
        if hasattr(mod, attr):
            setattr(mod, attr, target)
            return attr
    raise WrapError("Этот Blender не поддерживает цель для Surface Deform")


def add_surface_deform(context, obj, target, strength=1.0, bind=True):
    mod = obj.modifiers.new(name=MOD_NAME + " (Surface Deform)", type='SURFACE_DEFORM')
    set_modifier_target(mod, target)
    try:
        mod.strength = strength
    except Exception:
        pass
    if not bind:
        return mod
    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    try:
        for ob in view_layer.objects:
            ob.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        overrides = {"object": obj, "active_object": obj,
                     "selected_objects": [obj], "selected_editable_objects": [obj]}
        with bpy.context.temp_override(**overrides):
            bpy.ops.object.surfacedeform_bind(modifier=mod.name)
    finally:
        if prev_active is not None:
            try:
                view_layer.objects.active = prev_active
            except Exception:
                pass
    return mod


def cleanup_result(context, settings):
    """Удаляет созданный результат и служебные модификаторы."""
    removed = []
    res = settings.result
    if res is not None and res.get(PW_TAG):
        removed.append(res.name)
        remove_object(res)
    settings.result = None
    pattern = settings.pattern
    if pattern is not None:
        for mod in list(pattern.modifiers):
            if mod.name.startswith(MOD_NAME):
                pattern.modifiers.remove(mod)
                removed.append("%s / %s" % (pattern.name, mod.name))
    target = settings.target
    if target is not None:
        _hide_object(target, False)
        if uv_flatten_state(target):
            set_uv_flatten(target, False)
            removed.append("%s / %s (сложена)" % (target.name, UV_MOD_NAME))
    layout = settings.layout
    if layout is not None and layout.get(PW_TAG):
        removed.append(layout.name)
        remove_object(layout)
    settings.layout = None
    settings.result = None
    context.view_layer.update()
    return removed


# --------------------------------------------------------------------------- #
#  Свойства и UI
# --------------------------------------------------------------------------- #

class PW_Settings(bpy.types.PropertyGroup):
    pattern: bpy.props.PointerProperty(
        name="Паттерн", description="Мэш с узором, который накладываем",
        type=bpy.types.Object, poll=lambda self, ob: ob.type == 'MESH')
    target: bpy.props.PointerProperty(
        name="Цель", description="Деформированный мэш, по форме которого накладываем",
        type=bpy.types.Object, poll=lambda self, ob: ob.type == 'MESH')
    result: bpy.props.PointerProperty(
        name="Результат", description="Объект, созданный инструментом",
        type=bpy.types.Object)
    layout: bpy.props.PointerProperty(
        name="Развёртка", description="Объект-развёртка, созданный инструментом",
        type=bpy.types.Object)

    mode: bpy.props.EnumProperty(name="Режим", items=MODE_ITEMS, default='PROJECT')
    axis: bpy.props.EnumProperty(
        name="Направление", items=AXIS_ITEMS, default='AUTO',
        description="Куда летят лучи: по локальной оси узора или автоматически "
                    "смотреть на цель с той стороны, куда смотрят её нормали")
    auto_place: bpy.props.BoolProperty(
        name="Поставить узор перед целью",
        description="Перед наложением поставить копию узора перед целью "
                    "перпендикулярно лучам (оригинал не двигается). "
                    "Нужно, когда узор лежит в стороне от цели",
        default=True)
    fit_scale: bpy.props.BoolProperty(
        name="Подогнать размер под цель",
        description="Увеличить копию узора, чтобы она закрыла весь силуэт цели",
        default=False)
    flip_depth: bpy.props.BoolProperty(
        name="Флипнуть по глубине",
        description="Отразить узор относительно плоскости цели: если узор лёг "
                    "не той стороной, включите это",
        default=False)
    flip_used: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    uv_rotate: bpy.props.EnumProperty(
        name="Поворот узора",
        description="Доворот узора в плоскости UV-развёртки, если развёртка цели "
                    "ориентирована иначе (0° — узор ложится как нарисован)",
        items=(('0', "0°", "Как есть"), ('90', "90°", "Повернуть на 90°"),
               ('180', "180°", "Повернуть на 180°"), ('270', "270°", "Повернуть на 270°")),
        default='0')
    smooth_steps: bpy.props.IntProperty(
        name="Сглаживание дисторшна",
        description="Сколько шагов релаксации применить к результату "
                    "(0 — выключено). Убирает «пинчи» там, где сетка UV неровная",
        default=0, min=0, max=20)
    smooth_strength: bpy.props.FloatProperty(
        name="Сила сглаживания", default=0.5, min=0.0, max=1.0)
    show_report: bpy.props.BoolProperty(
        name="Показывать отчёт", default=True)

    subdiv: bpy.props.IntProperty(
        name="Плотность", description="Сколько раз разбить сетку паттерна перед наложением",
        default=1, min=0, max=4)
    relief_scale: bpy.props.FloatProperty(
        name="Рельеф", description="Насколько сохранять собственный рельеф паттерна",
        default=1.0, min=0.0, max=4.0, soft_max=2.0)
    surface_offset: bpy.props.FloatProperty(
        name="Отступ",
        description="Сдвиг всего узора вдоль нормали цели. Положительный — поднять "
                    "над поверхностью (если узор мерцает, z-fighting: 0.001 … 0.01), "
                    "отрицательный — утопить узор внутрь цели",
        default=0.0, soft_min=-0.5, soft_max=0.5)
    trim_shrink: bpy.props.FloatProperty(
        name="Поджать край", description="Отступить от силуэта внутрь на это расстояние",
        default=0.0, min=0.0, soft_max=0.5)
    trim_partial: bpy.props.BoolProperty(
        name="Резать по вершинам",
        description="Удалять грань, если хоть одна её вершина промахнулась (ничего не торчит)",
        default=True)
    align_normals: bpy.props.BoolProperty(
        name="Рельеф наружу", description="Разворачивать нормали цели в сторону паттерна",
        default=True)
    use_copy: bpy.props.BoolProperty(
        name="Не портить оригинал",
        description="Работать с копией паттерна, оригинал оставить как есть",
        default=True)
    auto_bind: bpy.props.BoolProperty(
        name="Привязать сразу", description="Для режима Surface Deform: сразу выполнить Bind",
        default=True)
    sd_strength: bpy.props.FloatProperty(name="Сила", default=1.0, min=0.0, max=10.0)

    stats: bpy.props.StringProperty(name="Отчёт", default="")


class PW_OT_pick_selection(bpy.types.Operator):
    bl_idname = "patternwrap.pick_selection"
    bl_label = "Взять из выделения"
    bl_description = "Активный объект становится паттерном, остальные выделенные — целью"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        selected = [ob for ob in context.selected_objects if ob.type == 'MESH']
        active = context.view_layer.objects.active
        if active is None or active.type != 'MESH':
            self.report({'ERROR'}, "Сделайте активным мэш с узором")
            return {'CANCELLED'}
        s.pattern = active
        others = [ob for ob in selected if ob is not active]
        if others:
            s.target = others[0]
        if s.target is None:
            self.report({'WARNING'}, "Паттерн: %s. Цель выберите вручную" % active.name)
        else:
            self.report({'INFO'}, "Паттерн: %s, цель: %s" % (s.pattern.name, s.target.name))
        return {'FINISHED'}


def _validate(settings):
    if settings.pattern is None:
        return "Не выбран паттерн (мэш с узором)"
    if settings.target is None:
        return "Не выбрана цель (деформированный мэш)"
    if settings.pattern is settings.target:
        return "Паттерн и цель — один объект"
    if not settings.pattern.data.polygons:
        return "У паттерна нет граней"
    if not settings.target.data.polygons:
        return "У цели нет граней"
    return None


def _layer_collections(layer):
    yield layer
    for child in layer.children:
        for item in _layer_collections(child):
            yield item


def _link_to(obj, collection):
    """Ссылает объект в коллекцию, если он там ещё не лежит."""
    try:
        if collection is not None and obj.name not in collection.objects:
            collection.objects.link(obj)
            return True
    except Exception:
        pass
    return False


def link_object_visible(obj, src=None):
    """Привязывает объект к коллекциям источника и гарантирует видимость."""
    linked = False
    if src is not None:
        for coll in list(src.users_collection):
            _link_to(obj, coll)
        linked = any(coll in obj.users_collection for coll in src.users_collection)
    scene_coll = bpy.context.scene.collection
    if not linked:
        _link_to(obj, scene_coll)
    try:
        view_layer = bpy.context.view_layer
        visible = set()
        for layer in _layer_collections(view_layer.layer_collection):
            if not layer.exclude:
                visible.add(layer.collection)
        if not any(coll in visible for coll in obj.users_collection):
            _link_to(obj, scene_coll)
    except Exception:
        pass
    try:
        obj.hide_set(False)
    except Exception:
        pass
    obj.hide_viewport = False
    obj.hide_render = False
    return obj


def result_is_usable(result, target, pattern=None):
    """Годится ли уже созданный результат, чтобы просто сложить цель обратно.

    Старый результат мог остаться от прежней версии, быть собранным из другого
    узора, отвязанным от сцены, потерять привязку Surface Deform или ссылаться
    на другую цель — тогда шаг 2 обязан пересобрать всё заново, а не «рапортовать
    готово» на битом объекте.
    """
    if result is None or result.name not in bpy.data.objects:
        return False
    if not result.data.polygons:
        return False
    if not list(result.users_collection):
        return False
    if pattern is not None:
        source = result.get(PW_SOURCE)
        if source and source != pattern.name:
            return False
        if not source:
            return False
    mods = [m for m in result.modifiers if m.type == 'SURFACE_DEFORM']
    if not mods:
        return False
    mod = mods[0]
    if not mod.show_viewport or not getattr(mod, "is_bound", False):
        return False
    for attr in ("target", "object"):
        bound = getattr(mod, attr, None)
        if bound is not None:
            return bound is target
    return False


def _uv_relief(work, direction=None):
    """Рельеф узора (размах геометрии) вдоль заданной оси, в мировых единицах.

    После запекания трансформа локальные оси узора совпадают с мировыми, поэтому
    «толщину» узора надо мерить вдоль нормали плоскости развёртки, а не по Z.
    """
    me = work.data
    if not me.vertices:
        return 0.0
    if direction is None:
        values = [v.co.z for v in me.vertices]
        scale = abs(work.matrix_world.to_scale().z)
    else:
        d = Vector(direction).normalized()
        values = [v.co.dot(d) for v in me.vertices]
        scale = (work.matrix_world.to_3x3() @ d).length
    return abs((max(values) - min(values)) * scale)


def _uv_plane_normal(target):
    normal = (target.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0)))
    if normal.length < 1e-12:
        normal = Vector((0.0, 0.0, 1.0))
    return normal.normalized()


def _hide_object(ob, hidden):
    try:
        ob.hide_set(hidden)
    except Exception:
        pass
    ob.hide_render = hidden


def uv_preview_active(settings):
    """Развёрнута ли цель прямо сейчас."""
    target = settings.target
    return bool(target is not None and uv_flatten_state(target))


def _has_surface_deform(obj):
    for mod in obj.modifiers:
        if mod.type == 'SURFACE_DEFORM':
            return True
    return False


def _uv_stats_text(settings, stats, offset, stage, coverage=None):
    lines = []
    if stage == 'folded':
        relief = stats.get('relief', 0.0)
        if stats.get('failed'):
            lines.append("ВНИМАНИЕ: узор НЕ сел на цель")
            lines.append("Удаление %.4f при ожидаемом ~%.4f — нажмите "
                         "«1. Развернуть и обрезать» заново"
                         % (stats.get('distance', 0.0), relief + offset))
        else:
            lines.append("Узор перенесён на цель (Surface Deform), цель сложена обратно")
            lines.append("Удаление узора от поверхности: %.4f (рельеф %.4f + офсет %.4f)"
                         % (stats.get('distance', 0.0), relief, offset))
    else:
        lines.append("Цель развёрнута по UV в плоскость (модификатор «%s»)" % UV_MOD_NAME)
        lines.append("Граней узора: %d → разбито %d → осталось %d (%d%%)"
                     % (stats.get('faces_original', 0), stats.get('faces_before', 0),
                        stats.get('faces_after', 0), round(100.0 * stats.get('kept', 0.0))))
        lines.append("Попало в развёртку вершин: %d из %d"
                     % (stats.get('vertices_hit', 0), stats.get('vertices_total', 0)))
        lines.append("Офсет до поверхности: %.4f" % offset)
        if coverage:
            lines.append("Покрытие развёртки: %.0f%% x %.0f%%"
                         % (100.0 * coverage[0], 100.0 * coverage[1]))
        if stats.get('uv_ambiguous'):
            lines.append("Швы UV не разорваны у %d вершин — у швов возможен дисторшн"
                         % stats['uv_ambiguous'])
        lines.append("Шаг 2: «Вернуть на цель» — сложить всё обратно")
    lines.append("Объект: %s" % stats.get('object', '?'))
    obj = settings.result
    if obj is not None and stats.get('object') == obj.name:
        collections = ", ".join(c.name for c in obj.users_collection) or "вне сцены!"
        lines.append("Коллекция: %s" % collections)
    text = "\n".join(lines)
    settings.stats = text
    return text


def _uv_place_and_bind(context, settings, work, offset):
    """Развернуть цель, подогнать и обрезать узор, привязать Surface Deform."""
    target = settings.target
    _mod, ambiguous = setup_uv_flatten(target)
    set_uv_flatten(target, True)
    context.view_layer.update()

    if evaluated_vertex_count(target) == 0:
        set_uv_flatten(target, False)
        context.view_layer.update()
        raise WrapError(
            "Геоноды развёртки цели выдают пустую геометрию (объект «%s» "
            "схлопывается в ноль). Нажмите «Развернуть / сложить цель» — "
            "группа геонод будет пересобрана автоматически" % target.name)
    depsgraph = context.evaluated_depsgraph_get()

    normal = (target.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    direction = -normal

    # ориентация узора = оси UV-развёртки (локальные X/Y цели), плюс доворот
    m3 = target.matrix_world.to_3x3()
    axis_x = (m3 @ Vector((1.0, 0.0, 0.0)))
    axis_y = (m3 @ Vector((0.0, 1.0, 0.0)))
    angle = math.radians(float(settings.uv_rotate))
    if angle:
        ca, sa = math.cos(angle), math.sin(angle)
        axis_x, axis_y = axis_x * ca + axis_y * sa, axis_x * (-sa) + axis_y * ca
    frame = (axis_x, axis_y, normal)

    info = place_pattern_as_decal(work, target, direction, depsgraph, rotate=True,
                                  fit=settings.fit_scale, fit_mode='BOTH',
                                  gap=offset, flip=settings.flip_depth,
                                  frame=frame, allow_swap=False)
    context.view_layer.update()

    stats = wrap_pattern(work, target, mode='TRIM_ONLY', subdiv=settings.subdiv,
                         trim_shrink=settings.trim_shrink,
                         trim_partial=settings.trim_partial,
                         direction_world=direction,
                         depsgraph=context.evaluated_depsgraph_get())
    stats['relief'] = _uv_relief_flat(work, normal)
    stats['uv_ambiguous'] = ambiguous
    add_surface_deform(context, work, target, strength=settings.sd_strength, bind=True)
    return stats, info


def _uv_relief_flat(work, normal=None):
    """Рельеф узора сразу после установки (вдоль нормали плоскости развёртки)."""
    return _uv_relief(work, normal)


def _run_uv(context, settings, stay_unfolded=False):
    """UV-путь: развернуть цель -> подогнать и обрезать узор -> сложить обратно."""
    with VisibilityGuard(settings.target), VisibilityGuard(settings.pattern):
        return _run_uv_inner(context, settings, stay_unfolded)


def _uv_verify(context, work, target, relief, offset):
    """Проверяет, что узор действительно лёг на цель. Возвращает (ок, удаление)."""
    distance, _idx = measure_against_surface(
        work, target, depsgraph=context.evaluated_depsgraph_get())
    limit = (relief + abs(offset)) * 3.0 + 0.01
    return distance <= limit, distance


def _run_uv_inner(context, settings, stay_unfolded=False):
    target = settings.target
    offset = float(settings.surface_offset)   # минус = утопить узор внутрь цели
    result = settings.result
    flip_changed = bool(settings.flip_depth) != bool(settings.flip_used)

    # Шаг 2: годный результат уже есть — просто складываем цель обратно.
    # Если результат остался от прежней версии/прогона (отвязан от сцены, потерял
    # привязку или привязан к другой цели) — идём полным циклом, а не рапортуем
    # «готово» на битом объекте.
    if (not stay_unfolded and not flip_changed
            and result_is_usable(result, target, settings.pattern)):
        set_uv_flatten(target, False)
        context.view_layer.update()
        if settings.smooth_steps > 0:
            apply_surface_deform(context, result)
            relax_pattern_on_surface(result, target, steps=settings.smooth_steps,
                                     strength=settings.smooth_strength)
            context.view_layer.update()
        relief = _uv_relief(result, _uv_plane_normal(target))
        ok, distance = _uv_verify(context, result, target, relief, offset)
        stats = {'object': result.name, 'distance': distance, 'relief': relief,
                 'failed': not ok}
        text = _uv_stats_text(settings, stats, offset, 'folded')
        return result, stats, text

    # Шаг 1: полный цикл (развернуть → подогнать → обрезать → привязать).
    work, baked = make_work_object(context, settings)
    try:
        stats, info = _uv_place_and_bind(context, settings, work, offset)
    except Exception:
        set_uv_flatten(target, False)
        context.view_layer.update()
        raise

    settings.flip_used = bool(settings.flip_depth)
    stats['object'] = work.name

    if stay_unfolded:
        text = _uv_stats_text(settings, stats, offset, 'unfolded', info.get('coverage'))
        return work, stats, text

    set_uv_flatten(target, False)
    context.view_layer.update()
    if settings.smooth_steps > 0:
        apply_surface_deform(context, work)
        relax_pattern_on_surface(work, target, steps=settings.smooth_steps,
                                 strength=settings.smooth_strength)
        context.view_layer.update()
    relief = stats.get('relief') or _uv_relief(work, _uv_plane_normal(target))
    ok, distance = _uv_verify(context, work, target, relief, offset)
    stats['distance'] = distance
    stats['failed'] = not ok
    text = _uv_stats_text(settings, stats, offset, 'folded')
    return work, stats, text


def _run(context, settings, mode, trim_only=False):
    """Общий путь для операторов: подготовить объект, обернуть, отчитаться."""
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    with VisibilityGuard(settings.target), VisibilityGuard(settings.pattern):
        if mode == 'UV':
            return _run_uv(context, settings, stay_unfolded=trim_only)
        return _run_mesh(context, settings, mode, trim_only)


def _run_mesh(context, settings, mode, trim_only=False):
    work, baked = make_work_object(context, settings)
    direction_world = None
    place_text = ""

    if settings.axis == 'AUTO' or settings.auto_place:
        direction_world = resolve_world_direction(
            work, settings.target, settings.axis,
            context.evaluated_depsgraph_get())

    if settings.auto_place:
        info = place_pattern_as_decal(
            work, settings.target, direction_world,
            context.evaluated_depsgraph_get(),
            rotate=(settings.axis == 'AUTO'),
            fit=settings.fit_scale)
        shift = info['shift']
        place_text = ("Узор поставлен перед целью: сдвиг (%.3f, %.3f, %.3f)%s"
                      % (shift.x, shift.y, shift.z,
                         ", повёрнут по нормали цели" if info['rotated'] else ""))
        if info.get('fit_factor', 1.0) > 1.0:
            place_text += "\nУзор увеличен в %.2f раза, чтобы закрыть силуэт" % info['fit_factor']
        cov = info.get('coverage')
        if cov:
            place_text += "\nПокрытие силуэта: %.0f%% x %.0f%%" % (100.0 * cov[0], 100.0 * cov[1])
            if min(cov) < 0.9:
                place_text += ("\nВнимание: узор меньше силуэта — часть цели останется "
                               "открытой (включите «Подогнать размер»)")
        context.view_layer.update()

    stats = wrap_pattern(
        work, settings.target,
        mode='TRIM_ONLY' if trim_only else mode,
        axis=settings.axis,
        subdiv=settings.subdiv,
        relief_scale=settings.relief_scale,
        surface_offset=settings.surface_offset,
        trim_shrink=settings.trim_shrink,
        trim_partial=settings.trim_partial,
        align_normals=settings.align_normals,
        do_trim=True,
        direction_world=direction_world,
        depsgraph=context.evaluated_depsgraph_get(),
    )

    if mode == 'SURFACE_DEFORM' and not trim_only:
        add_surface_deform(context, work, settings.target,
                           strength=settings.sd_strength, bind=settings.auto_bind)

    text = format_stats(stats, mode) + "\nОбъект: %s" % work.name
    if place_text:
        text += "\n" + place_text
    if baked:
        text += "\nЗапечено модификаторов: %d" % baked
    settings.stats = text
    return work, stats, text


class PW_OT_wrap(bpy.types.Operator):
    bl_idname = "patternwrap.wrap"
    bl_label = "Обернуть и обрезать"
    bl_description = "Наложить узор по форме цели и убрать всё, что торчит за силуэт"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        err = _validate(s)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        try:
            work, stats, text = _run(context, s, s.mode)
        except WrapError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        except Exception as exc:  # pragma: no cover - защита от неожиданностей
            self.report({'ERROR'}, "%s: %s" % (type(exc).__name__, exc))
            return {'CANCELLED'}
        if stats.get('failed'):
            self.report({'WARNING'},
                        "Узор не сел на цель — нажмите «1. Развернуть и обрезать» заново")
        elif stats.get('kept') is not None and stats['kept'] < 0.05:
            self.report({'WARNING'},
                        "Узор обрезан почти полностью (%.1f%%) — проверьте ось проекции"
                        % (100.0 * stats['kept']))
        else:
            self.report({'INFO'}, "Готово: %s" % work.name)
        self.report({'INFO'}, text.replace("\n", " | "))
        return {'FINISHED'}


class PW_OT_trim_only(bpy.types.Operator):
    bl_idname = "patternwrap.trim_only"
    bl_label = "Только обрезать по силуэту"
    bl_description = "Ничего не двигать — просто удалить часть узора за пределами цели"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        err = _validate(s)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        try:
            _work, stats, text = _run(context, s, s.mode, trim_only=True)
        except WrapError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, text.replace("\n", " | "))
        return {'FINISHED'}


class PW_OT_rebind(bpy.types.Operator):
    bl_idname = "patternwrap.rebind"
    bl_label = "Привязать заново"
    bl_description = "Обновить привязку Surface Deform (после правки цели)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        if s.pattern is None or s.target is None:
            self.report({'ERROR'}, "Не выбраны паттерн и цель")
            return {'CANCELLED'}
        try:
            add_surface_deform(context, s.pattern, s.target,
                               strength=s.sd_strength, bind=True)
        except WrapError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Привязка обновлена")
        return {'FINISHED'}


class PW_OT_bake_pattern(bpy.types.Operator):
    bl_idname = "patternwrap.bake_pattern"
    bl_label = "Запечь модификаторы паттерна"
    bl_description = "Применить модификаторы паттерна, чтобы рельеф стал настоящей геометрией"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        if s.pattern is None:
            self.report({'ERROR'}, "Не выбран паттерн")
            return {'CANCELLED'}
        if not s.pattern.modifiers:
            self.report({'INFO'}, "У паттерна нет модификаторов")
            return {'FINISHED'}
        removed = bake_object_geometry(s.pattern, context.evaluated_depsgraph_get())
        self.report({'INFO'}, "Запечено модификаторов: %d" % removed)
        return {'FINISHED'}


class PW_OT_cleanup(bpy.types.Operator):
    bl_idname = "patternwrap.cleanup"
    bl_label = "Убрать результат"
    bl_description = "Удалить созданный инструментом объект и его модификаторы"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        removed = cleanup_result(context, s)
        s.stats = ""
        self.report({'INFO'}, "Удалено: %s" % (", ".join(removed) if removed else "нечего"))
        return {'FINISHED'}


class PW_OT_select_result(bpy.types.Operator):
    bl_idname = "patternwrap.select_result"
    bl_label = "Выделить обёрнутый мэш"
    bl_description = "Сделать результат активным объектом и выделить его"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        ob = s.result
        if ob is None or ob.name not in bpy.data.objects:
            self.report({'ERROR'}, "Результата нет — сначала оберните узор")
            return {'CANCELLED'}
        link_object_visible(ob)
        for other in context.view_layer.objects:
            other.select_set(False)
        ob.hide_set(False)
        ob.hide_viewport = False
        ob.select_set(True)
        context.view_layer.objects.active = ob
        collections = ", ".join(c.name for c in ob.users_collection) or "вне сцены"
        self.report({'INFO'}, "Выделен %s (коллекция: %s)" % (ob.name, collections))
        return {'FINISHED'}


class PW_OT_toggle_flatten(bpy.types.Operator):
    bl_idname = "patternwrap.toggle_flatten"
    bl_label = "Развернуть / сложить цель"
    bl_description = "Переключить модификатор «uv flatten»: развернуть цель по UV " \
                     "в плоскость или сложить обратно"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        if s.target is None:
            self.report({'ERROR'}, "Не выбрана цель")
            return {'CANCELLED'}
        try:
            # пересобирает группу геонод, если она оказалась битой
            setup_uv_flatten(s.target)
        except WrapError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        unfolded = not uv_flatten_state(s.target)
        if not set_uv_flatten(s.target, unfolded):
            self.report({'ERROR'}, "Не удалось создать модификатор «%s»" % UV_MOD_NAME)
            return {'CANCELLED'}
        context.view_layer.update()
        self.report({'INFO'}, "Цель %s" % ("развёрнута" if unfolded else "сложена"))
        return {'FINISHED'}


class PW_OT_smooth(bpy.types.Operator):
    bl_idname = "patternwrap.smooth"
    bl_label = "Сгладить дисторшн результата"
    bl_description = "Запечь Surface Deform и расслабить сетку узора по поверхности " \
                     "цели: убирает «пинчи» и растяжения там, где сетка UV неровная"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.pattern_wrap
        ob = s.result
        if ob is None or ob.name not in bpy.data.objects:
            self.report({'ERROR'}, "Результата нет")
            return {'CANCELLED'}
        if s.mode == 'UV':
            set_uv_flatten(s.target, False)
            context.view_layer.update()
        try:
            applied = apply_surface_deform(context, ob)
            count = relax_pattern_on_surface(
                ob, s.target, steps=max(1, s.smooth_steps or 3),
                strength=s.smooth_strength)
        except WrapError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        s.stats = ("Сглажено вершин: %d\nЗапечено модификаторов: %s\nОбъект: %s"
                   % (count, ", ".join(applied) if applied else "нет", ob.name))
        self.report({'INFO'}, "Сглажено вершин: %d" % count)
        return {'FINISHED'}


class PW_PT_main(bpy.types.Panel):
    bl_idname = "PW_PT_main"
    bl_label = "Pattern Wrap"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Pattern Wrap"

    def draw(self, context):
        layout = self.layout
        s = context.scene.pattern_wrap

        col = layout.column(align=True)
        col.prop(s, "pattern", text="Узор")
        col.prop(s, "target", text="Цель")
        layout.operator("patternwrap.pick_selection", icon='RESTRICT_SELECT_OFF')

        layout.separator()
        layout.prop(s, "mode")
        if s.mode == 'UV':
            col = layout.column(align=True)
            col.prop(s, "subdiv")
            col.prop(s, "surface_offset")
            col.prop(s, "uv_rotate")
            col.prop(s, "flip_depth")
            col.prop(s, "fit_scale")
            col.prop(s, "trim_shrink")
            col.prop(s, "trim_partial")
            col.separator()
            col.prop(s, "smooth_steps")
            if s.smooth_steps:
                col.prop(s, "smooth_strength")
        else:
            if s.mode != 'TRIM_ONLY':
                layout.prop(s, "axis")
            layout.prop(s, "auto_place")
            if s.auto_place and s.mode == 'PROJECT':
                layout.prop(s, "fit_scale")
            if s.mode == 'PROJECT':
                col = layout.column(align=True)
                col.prop(s, "subdiv")
                col.prop(s, "relief_scale")
                col.prop(s, "surface_offset")
            if s.mode == 'SURFACE_DEFORM':
                col = layout.column(align=True)
                col.prop(s, "sd_strength")
                col.prop(s, "auto_bind")
            col = layout.column(align=True)
            col.prop(s, "trim_shrink")
            col.prop(s, "trim_partial")
            if s.mode == 'PROJECT':
                col.prop(s, "align_normals")
        layout.prop(s, "use_copy")

        if s.pattern is not None and s.pattern.modifiers:
            box = layout.box()
            box.label(text="У узора есть модификаторы", icon='ERROR')
            box.label(text="они будут запечены")
            box.operator("patternwrap.bake_pattern", icon='MODIFIER')

        layout.separator()
        if s.mode == 'UV':
            row = layout.row(align=True)
            row.operator("patternwrap.trim_only", icon='UV', text="1. Развернуть и обрезать")
            row = layout.row(align=True)
            row.operator("patternwrap.wrap", icon='MOD_SHRINKWRAP',
                         text="2. Вернуть на цель")
            if uv_preview_active(s):
                box = layout.box()
                box.label(text="Цель развёрнута в плоскость", icon='INFO')
                box.label(text="шаг 2 вернёт её на место")
        else:
            layout.operator("patternwrap.wrap", icon='MOD_SHRINKWRAP')
            layout.operator("patternwrap.trim_only", icon='MOD_BOOLEAN')
            if s.mode == 'SURFACE_DEFORM':
                layout.operator("patternwrap.rebind", icon='MOD_DEFORM')

        col = layout.column(align=True)
        col.operator("patternwrap.select_result", icon='RESTRICT_SELECT_OFF')
        col.operator("patternwrap.toggle_flatten", icon='MOD_UVPROJECT')
        if s.mode == 'UV' and s.smooth_steps:
            col.operator("patternwrap.smooth", icon='MOD_SMOOTH')
        col.operator("patternwrap.cleanup", icon='TRASH')


class PW_PT_report(bpy.types.Panel):
    bl_idname = "PW_PT_report"
    bl_label = "Отчёт"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Pattern Wrap"
    bl_parent_id = "PW_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.pattern_wrap

        row = layout.row()
        row.prop(s, "show_report", text="Показывать отчёт")
        box = layout.box()
        if s.stats and s.show_report:
            for line in s.stats.split("\n"):
                box.label(text=line)
        else:
            box.label(text="нет данных", icon='INFO')
        if s.result is not None and s.result.get(PW_TAG):
            col = box.column(align=True)
            col.label(text="Результат: %s" % s.result.name, icon='OBJECT_DATA')
            col.label(text="граней: %d" % len(s.result.data.polygons))
            col.label(text="модификаторы: %s"
                          % (", ".join(m.type for m in s.result.modifiers) or "нет"))
            col.operator("patternwrap.select_result", icon='RESTRICT_SELECT_OFF')
        if s.target is not None:
            box.label(text="Цель: %s (%s)"
                          % (s.target.name,
                             "развёрнута" if uv_flatten_state(s.target) else "сложена"),
                      icon='OBJECT_DATA')


# --------------------------------------------------------------------------- #
#  Регистрация
# --------------------------------------------------------------------------- #

_CLASSES = (
    PW_Settings,
    PW_OT_pick_selection,
    PW_OT_wrap,
    PW_OT_trim_only,
    PW_OT_rebind,
    PW_OT_bake_pattern,
    PW_OT_cleanup,
    PW_OT_select_result,
    PW_OT_toggle_flatten,
    PW_OT_smooth,
    PW_PT_main,
    PW_PT_report,
)

# Список живёт в globals и переживает повторное выполнение файла (F8 / Reload
# Scripts): иначе старые классы остаются зарегистрированными и register() падает
# с «already registered as a subclass».
_REGISTERED = globals().get("_REGISTERED", [])
_SCENE_PROP = "pattern_wrap"


def register():
    # Идемпотентность: сначала всегда снимаем то, что могли зарегистрировать
    # предыдущим вызовом (в том числе из прошлого выполнения модуля).
    unregister()
    globals()["_reload_guard"] = True

    for cls in _CLASSES:
        bpy.utils.register_class(cls)
        _REGISTERED.append(cls)
    bpy.types.Scene.pattern_wrap = bpy.props.PointerProperty(type=PW_Settings)
    _REGISTERED.append(_SCENE_PROP)


def unregister():
    for item in reversed(list(_REGISTERED)):
        if isinstance(item, str):
            if getattr(bpy.types.Scene, item, None) is not None:
                try:
                    delattr(bpy.types.Scene, item)
                except Exception:
                    pass
            continue
        try:
            bpy.utils.unregister_class(item)
        except Exception:
            # Класс уже снят — повторный вызов unregister() не должен падать.
            pass
    del _REGISTERED[:]


if __name__ == "__main__":
    register()
