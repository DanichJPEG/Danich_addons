# SPDX-License-Identifier: GPL-3.0-or-later
# FFlex - author: DanichJpeg
"""Russian interface strings (used when Blender runs in Russian)."""

import bpy

TRANSLATIONS = {
    "ru_RU": {
        "*": {
            # operators
            "Flow Flex": "Flow Flex (кривая)",
            "Flow Flex (Apply)": "Flow Flex (применить)",
            "Even Space": "Равные расстояния",
            "Interactively fit the selected edge loops onto an editable imaginary curve "
            "(screen space or global / local / 3D cursor axes)":
                "Интерактивно подгоняет выбранные петли рёбер по редактируемой мнимой кривой "
                "(экранное пространство, глобальные / локальные оси, оси 3D курсора)",
            "Fit the selected edge loops onto an imaginary curve in one go "
            "(iterations repeat the fit - good for smoothing)":
                "Подгоняет выбранные петли по мнимой кривой сразу "
                "(повторы сглаживают результат)",
            "Distribute the selected vertices with equal spacing along the edge loop "
            "(screen / global / local / 3D cursor axes)":
                "Распределяет выбранные вершины с равным расстоянием "
                "(экранное пространство, глобальные / локальные оси, оси 3D курсора)",

            # settings
            "Space": "Пространство",
            "Axis": "Ось",
            "Pivot": "Центр",
            "Control Points": "Точки кривой",
            "Auto Points": "Авто-точки",
            "Fit Tolerance": "Допуск подгонки",
            "Roundness": "Округлость",
            "Curve Quality": "Качество кривой",
            "Strength": "Сила",
            "Keep Offset": "Сохранять смещение",
            "Project to Plane": "Проецировать на плоскость",
            "Vertex Density": "По плотности вершин",
            "Even Spacing": "Равные расстояния",
            "Equal Gaps": "Равные промежутки",
            "Split Ribbons": "Разделять ленты",
            "Pinch Threshold": "Порог перехвата",
            "Corner Angle": "Порог угла",
            "Right Click": "Правая кнопка",
            "Left Click On Empty": "ЛКМ по пустому месту",
            "Apply": "Применить",
            "Reset Curve": "Сброс кривой",
            "Nothing": "Ничего",
            "Clear Selection": "Снять выделение",
            "Mirror X": "Зеркало по X",
            "Show Original": "Показывать оригинал",
            "Iterations": "Повторы",
            "Measure": "Измерение",
            "Extend to Loop": "Расширить до петли",

            # descriptions
            "The imaginary curve is edited and fitted in screen space":
                "Мнимая кривая редактируется и подгоняется в экранном пространстве",
            "Work in global axes - pick the axis below":
                "Работа в глобальных осях — ось выбирается ниже",
            "Work in the active object's local axes":
                "Работа в локальных осях активного объекта",
            "Work in the 3D cursor's axes": "Работа в осях 3D курсора",
            "Frame axis (the curve is edited in the plane perpendicular to it)":
                "Ось рамки: кривая редактируется в плоскости, перпендикулярной ей",
            "Where the work frame plane passes through":
                "Через что проходит плоскость рабочей рамки",
            "Number of control points of the imaginary curve":
                "Количество контрольных точек мнимой кривой",
            "Guess the number of control points so that the imaginary curve really "
            "follows the selected loop instead of cutting corners across it "
            "(the count is written back into Control Points)":
                "Подобрать число контрольных точек так, чтобы мнимая кривая реально шла по "
                "выделенной петле, а не срезала углы (число записывается в «Точки кривой»)",
            "How far the imaginary curve may drift from the loop, as a share of the "
            "loop length (0.02 = 2%)":
                "Насколько мнимая кривая может отойти от петли, в долях длины петли (0.02 = 2%)",
            "1.0 keeps sharp corners, 2.0 rounds the curve off":
                "1.0 — углы сохраняются, 2.0 — кривая скругляется",
            "Samples per control point span": "Сэмплов на участок между точками",
            "Blend between the original shape (0) and the fitted curve (1)":
                "Смешивание исходной формы (0) и подогнанной кривой (1)",
            "Keep the small 3D offset of every vertex from the curve "
            "(off = vertices sit exactly on the curve)":
                "Сохранять небольшое 3D смещение вершин от кривой "
                "(выкл. — вершины ложатся точно на кривую)",
            "Fit the loop inside the work frame plane (flattens it onto the axis plane)":
                "Подгонять петлю внутри плоскости рамки (сплющивает её на плоскость оси)",
            "Place control points where the mesh is dense instead of evenly":
                "Ставить точки кривой по плотности сетки, а не равномерно",
            "Distribute the vertices with equal arc length along the curve "
            "(LoopTools 'Space' analogue)":
                "Распределять вершины с равной длиной дуги по кривой (аналог Space из LoopTools)",
            "A closed loop made of two long sides joined by two short ends (the "
            "boundary of a ribbon) is split into two separate curves, so both rails "
            "can be shaped on their own (K in the interactive tool)":
                "Замкнутый контур из двух длинных сторон, соединённых двумя короткими торцами "
                "(граница полосы), разрезается на две отдельные кривые, чтобы каждый борт "
                "можно было гнуть отдельно (клавиша K в режиме)",
            "An edge this many times shorter than its neighbours counts as the end "
            "of a ribbon":
                "Во столько раз более короткое ребро, чем его соседи, считается торцом полосы",
            "Fallback for Split Ribbons: a closed loop with exactly two sharper "
            "turns than this is split at them":
                "Запасной вариант для «Разделять ленты»: замкнутый контур ровно с двумя "
                "поворотами круче этого порога разрезается по ним",
            "What a right click does while the curve is being edited":
                "Что делает правая кнопка во время редактирования кривой",
            "What a plain left click on empty space does (dragging still makes a "
            "selection box)":
                "Что делает обычный клик ЛКМ по пустому месту (протяжка всё равно "
                "рисует рамку выделения)",
            "Mirror the deformation on the object's local X axis":
                "Зеркалить деформацию по локальной оси X объекта",
            "Draw the original loop while editing": "Показывать исходную петлю при редактировании",
            "How many times the instant fit is applied": "Сколько раз подряд применить подгонку",
            "How distances are measured for the even spacing operator":
                "Как измеряются расстояния для равномерного распределения",
            "Grow the selected vertices to the full edge loop before spacing":
                "Перед распределением расширить выделение до всей петли",

            # enum items
            "Screen (View)": "Экран (вид)",
            "Global": "Глобальные",
            "Local": "Локальные",
            "3D Cursor": "3D курсор",
            "Loop Centroid": "Центр петли",
            "Object Origin": "Начало объекта",
            "World Origin": "Начало мира",
            "Along Chain (3D)": "По цепочке (3D)",
            "In Axis Plane": "В плоскости оси",

            # hand drawn labels
            "Deform Space": "Пространство деформации",
            "Imaginary Curve": "Мнимая кривая",
        }
    }
}


def register(module_name):
    try:
        bpy.app.translations.register(module_name, TRANSLATIONS)
    except Exception:  # pragma: no cover - already registered / unsupported
        pass


def unregister(module_name):
    try:
        bpy.app.translations.unregister(module_name)
    except Exception:  # pragma: no cover
        pass


# --------------------------------------------------------------------------- #
# on screen help (drawn with blf, so it needs its own strings)
# --------------------------------------------------------------------------- #

HUD_TEXT = {
    "ru": {
        "title": "FFlex",
        "author": "DanichJpeg  1.5.0",
        "space": "Пространство",
        "pivot": "Центр",
        "points": "Точек",
        "auto_suffix": " (авто)",
        "curves": "Кривых",
        "roundness": "Округлость",
        "strength": "Сила",
        "even_spacing": "Равные расстояния",
        "equal_gaps": "Равные промежутки",
        "project": "Проекция на плоскость",
        "mirror": "Зеркало",
        "mirror_off": "Зеркало временно отключено в этой версии",
        "interpolate": "Между кривыми",
        "shape_linear": "лин",
        "shape_quadratic": "квадр",
        "shape_cubic": "куб",
        "shape_auto": "авто",
        "sheet_on": "без толщины",
        "sheet_off": "вся толщина",
        "on": "ВКЛ",
        "off": "выкл",
        "space_screen": "Экран (вид)",
        "space_global": "Глобальные",
        "space_local": "Локальные",
        "space_cursor": "3D-курсор",
        "pivot_centroid": "центр петли",
        "pivot_origin": "начало объекта",
        "pivot_cursor": "3D-курсор",
        "pivot_world": "начало мира",
        "mouse": "МЫШЬ",
        "keys": "КЛАВИШИ",
        "k_lmb_empty": "ЛКМ по пустому",
        "k_drag": "ЛКМ тянуть точку",
        "k_shift_lmb": "Shift + ЛКМ",
        "k_ctrl_lmb": "Ctrl + ЛКМ",
        "k_shift": "Shift + ЛКМ / рамка",
        "k_ctrl": "Ctrl + ЛКМ / рамка",
        "k_ctrl_curve": "Ctrl + ЛКМ (кривая)",
        "a_add_sel": "добавить к выделению",
        "a_sub_sel": "убрать из выделения",
        "k_rect": "ЛКМ + рамка",
        "k_wheel": "Shift + колесо",
        "k_smooth": "Alt + колесо",
        "k_rmb": "ПКМ по пустому",
        "a_deselect": "снять выделение",
        "a_split": "разделить на углах",
        "a_reset_points": "вернуть точки",
        "a_interpolate": "интерполяция петель",
        "a_gap_shape": "форма стыка выделенных кривых",
        "a_tension": "натяжение (E + мышь влево/вправо)",
        "a_sheet": "не заходить за изломы (толщина)",
        "a_mirror_axis": "ось зеркала",
        "k_arrows_ud": "↑ / ↓",
        "k_arrows_lr": "← / →",
        "a_confirm": "применить",
        "a_bend": "гнуть кривую",
        "a_multi": "добавить в выделение",
        "a_point": "добавить точку",
        "a_rect": "выделить точки",
        "a_count": "число точек",
        "a_smooth": "сглаживание перехода",
        "a_grab": "двигать точки",
        "a_roundness": "округлость",
        "a_strength": "сила",
        "a_even": "равные расстояния",
        "a_flatten": "проекция на плоскость",
        "a_space": "экран / глоб / лок / курсор",
        "a_axis": "ось рабочей плоскости",
        "a_lock": "фиксация направления",
        "a_pivot": "центр плоскости",
        "a_rh": "сброс / скрыть подсказки",
        "a_undo": "отменить последнее действие (выход — Esc)",
        "a_mrh": "сброс / скрыть",
        "a_del": "удалить точку",
        "a_ok_cancel": "применить / выйти и вернуть всё",
    },
    "en": {
        "title": "FFlex",
        "author": "DanichJpeg  1.5.0",
        "space": "Space",
        "pivot": "Pivot",
        "points": "Points",
        "auto_suffix": " (auto)",
        "curves": "Curves",
        "roundness": "Roundness",
        "strength": "Strength",
        "even_spacing": "Even spacing",
        "equal_gaps": "Equal gaps",
        "project": "Project to plane",
        "mirror": "Mirror",
        "mirror_off": "Mirror is temporarily disabled in this version",
        "interpolate": "Between curves",
        "shape_linear": "lin",
        "shape_quadratic": "quad",
        "shape_cubic": "cubic",
        "shape_auto": "auto",
        "sheet_on": "sheet only",
        "sheet_off": "through the rim",
        "on": "ON",
        "off": "off",
        "space_screen": "Screen (View)",
        "space_global": "Global",
        "space_local": "Local",
        "space_cursor": "3D Cursor",
        "pivot_centroid": "loop centroid",
        "pivot_origin": "object origin",
        "pivot_cursor": "3D cursor",
        "pivot_world": "world origin",
        "mouse": "MOUSE",
        "keys": "KEYS",
        "k_lmb_empty": "LMB empty space",
        "k_drag": "LMB drag handle",
        "k_shift_lmb": "Shift + LMB",
        "k_ctrl_lmb": "Ctrl + LMB",
        "k_shift": "Shift + LMB / box",
        "k_ctrl": "Ctrl + LMB / box",
        "k_ctrl_curve": "Ctrl + LMB (curve)",
        "a_add_sel": "add to the selection",
        "a_sub_sel": "remove from the selection",
        "k_rect": "LMB + drag box",
        "k_wheel": "Shift + Wheel",
        "k_smooth": "Alt + Wheel",
        "k_rmb": "RMB empty space",
        "a_deselect": "clear the selection",
        "a_split": "split at corners",
        "a_reset_points": "reset the points",
        "a_interpolate": "loop interpolation",
        "a_gap_shape": "blend shape of the selected curves",
        "a_sheet": "stay off the creases (thickness)",
        "a_mirror_axis": "mirror axis",
        "k_arrows_ud": "Up / Down",
        "k_arrows_lr": "Left / Right",
        "a_confirm": "confirm",
        "a_bend": "bend the curve",
        "a_multi": "add to selection",
        "a_point": "add a point",
        "a_rect": "select points",
        "a_count": "point count",
        "a_smooth": "transition smoothing",
        "a_grab": "move the points",
        "a_roundness": "roundness",
        "a_strength": "strength",
        "a_even": "even spacing",
        "a_flatten": "project to plane",
        "a_space": "screen / global / local / cursor",
        "a_axis": "work plane axis",
        "a_lock": "lock direction",
        "a_pivot": "plane centre",
        "a_rh": "reset / hide hints",
        "a_undo": "undo the last action (Esc leaves the tool)",
        "a_mrh": "reset / hide",
        "a_del": "delete point",
        "a_ok_cancel": "apply / leave and restore",
    },
}


def hud(lang, key):
    """Interface text for the on screen help (``lang`` = RU / EN / AUTO)."""
    lang = (lang or "AUTO").lower()
    if lang == "auto":
        try:
            lang = "ru" if bpy.app.translations.locale.lower().startswith("ru") else "en"
        except Exception:  # pragma: no cover
            lang = "en"
    table = HUD_TEXT.get(lang, HUD_TEXT["en"])
    return table.get(key, HUD_TEXT["en"].get(key, key))
