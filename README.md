# DanichJPEG Add-ons — репозиторий расширений Blender

Статический репозиторий, из которого Blender сам скачивает аддоны и обновляет их
(Blender 4.2+; сборки рассчитаны на 5.0+). Сейчас в нём один аддон — **FFlex**
(автор DanichJpeg, GPL-3.0-or-later).

**URL репозитория для Blender:**

```
https://danichjpeg.github.io/Danich_addons/index.json
```

Именно с `/index.json` на конце: для HTTP(S) Blender использует адрес как есть и
имя файла не дописывает.

---

## Установка (для пользователя)

1. `Edit → Preferences → Get Extensions`.
2. Открыть список репозиториев (выпадающий список сверху, там `extensions.blender.org`,
   `blenderkit`…) → кнопка **+** → *Add Remote Repository*.
3. Заполнить:
   * **Name** — как репозиторий будет виден в списке: `DanichJPEG Add-ons`
   * **URL**: `https://danichjpeg.github.io/Danich_addons/index.json`

   Внутренний ID репозитория Blender создаёт сам (виден в списке репозиториев,
   например `danichjpeg_github_io`). Для аддона он не важен: FFlex нигде не
   опирается на имя репозитория — он работает через `__package__`
   (`bl_ext.<ID репозитория>.fflex`).
4. Сохранить и нажать **Sync** (круговые стрелки) — в списке появится `FFlex`.
5. Выбрать в фильтре свой репозиторий → **Install**.
6. Панель аддона: `3D View → Sidebar (N) → вкладка FFlex`, горячая клавиша `Ctrl+Shift+F`.

Галочку **Check for Updates on Startup** можно оставить включённой — Blender будет
сам подтягивать новые версии.


### ⚠️ Если FFlex уже установлен вручную (zip или папка в `scripts/addons`)

Старую (легаси) версию нужно **отключить и удалить**: `Edit → Preferences → Add-ons`
→ снять галочку `FFlex` → *Remove*. Это не косметика, а необходимость — у легаси-аддона
и у расширения совпадают `bl_idname` операторов и панели. Если включить оба,
Blender при включении второго выкидывает классы первого
(`bl_idname 'fflex.flex' has been registered before, unregistering previous`),
`Scene.fflex` остаётся от старого модуля, а `Ctrl+Shift+F` дублируется.
Проверено на Blender 5.2.


## Лицензия

GPL-3.0-or-later (`SPDX:GPL-3.0-or-later`), как указано в манифесте и в заголовках
файлов аддона.
