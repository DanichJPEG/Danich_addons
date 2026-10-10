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

То же самое без GUI:

```powershell
blender --background --online-mode --command extension repo-add danichjpeg `
    --url https://danichjpeg.github.io/Danich_addons/index.json --name "DanichJPEG Add-ons"
blender --background --online-mode --command extension sync
blender --background --online-mode --command extension install -e fflex
```

### ⚠️ Если FFlex уже установлен вручную (zip или папка в `scripts/addons`)

Старую (легаси) версию нужно **отключить и удалить**: `Edit → Preferences → Add-ons`
→ снять галочку `FFlex` → *Remove*. Это не косметика, а необходимость — у легаси-аддона
и у расширения совпадают `bl_idname` операторов и панели. Если включить оба,
Blender при включении второго выкидывает классы первого
(`bl_idname 'fflex.flex' has been registered before, unregistering previous`),
`Scene.fflex` остаётся от старого модуля, а `Ctrl+Shift+F` дублируется.
Проверено на Blender 5.2.

---

## Как выпустить обновление (для автора)

1. Правите код в `src/fflex/`.
2. **Поднимаете `version`** в `src/fflex/blender_manifest.toml` (например `1.5.1`)
   и в надписи версии в `src/fflex/ui.py` / `src/fflex/i18n.py`.
   Это обязательно: Blender кэширует `index.json` и при том же номере версии, но
   другом размере архива установка падает с
   `FATAL_ERROR Archive size mismatch "fflex", expected …, was …` (проверено).
   Новая версия — новый файл, старый архив продолжает работать у тех, кто не обновился.
3. Собрать репозиторий:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\build_repo.ps1
   ```

   Скрипт делает три вещи встроенными командами Blender:

   ```powershell
   blender --background --factory-startup --command extension validate  .\src\fflex
   blender --background --factory-startup --command extension build --source-dir .\src\fflex --output-dir .\docs
   blender --background --factory-startup --command extension server-generate --repo-dir .\docs --html
   ```

4. Закоммитить `docs/` (там появляются `fflex-<версия>.zip`, обновлённый `index.json`
   и `index.html` для просмотра в браузере) и запушить.
5. Пользователи получат обновление при следующей синхронизации или при старте Blender.

Старые `*.zip` из `docs/` можно не удалять — `index.json` перечисляет все версии,
и Blender сам выберет подходящую. `build_repo.ps1 -Clean` чистит папку перед сборкой.

---

## Первичная публикация на GitHub Pages

1. Создать **публичный** репозиторий `Danich_addons`.
2. Залить проект (`src/`, `docs/`, `build_repo.ps1`, `tests/`, `README.md`, `.gitignore`).
   Каталог `docs/` должен быть закоммичен — иначе Pages нечего отдавать.
3. `Settings → Pages` → *Source*: `Deploy from a branch` → *Branch*: `main`,
   *Folder*: `/docs` → **Save**.
4. Через минуту проверить, что открывается
   `https://danichjpeg.github.io/Danich_addons/index.json`.

Если вместо JSON отдаётся 404 — проверьте точное написание имени репозитория в
`Settings → Pages` (путь в URL чувствителен к регистру) и что Pages собран
(`Actions`/`Pages` покажет статус сборки).

---

## Структура

```
Danich_addons/
  src/fflex/                     исходники расширения
    blender_manifest.toml        метаданные (id = fflex, version, license, …)
    __init__.py                  register()/unregister(), без bl_info
    compat.py  core.py  i18n.py  props.py  ops_flex.py  ops_space.py  ui.py
  docs/                          ПУБЛИКУЕТСЯ (index.json + zip + index.html)
  tests/check_from_repo.py       проверка установки из репозитория
  build_repo.ps1                 сборка docs/
  .gitignore
```

Blender ставит расширение как `bl_ext.danichjpeg.fflex` — поэтому весь код
использует `__package__`, а не жёсткое имя `FFlex`.

---

## Проверка (локально, ничего не публикуя)

```powershell
# 1. статический разбор + циклы register/unregister (харнесс blender-addon-dev)
powershell -NoProfile -ExecutionPolicy Bypass -File <скилл>\scripts\run_check.ps1 `
    -Path .\src\fflex -Form extension -Cycles 5

# 2. установка ИЗ репозитория и работа оператора
python -m http.server 8124 --directory .\docs
$env:BLENDER_USER_CONFIG     = "$PWD\.testcfg"
$env:BLENDER_USER_SCRIPTS    = "$PWD\.testscripts"
$env:BLENDER_USER_EXTENSIONS = "$PWD\.testext"
blender --background --online-mode --command extension repo-add danichjpeg `
    --url http://127.0.0.1:8124/index.json --clear-all
blender --background --online-mode --command extension sync
blender --background --online-mode --command extension install -s -e fflex
blender --background --online-mode --python .\tests\check_from_repo.py
```

`tests/check_from_repo.py` проверяет: все 8 классов зарегистрированы, 5 операторов
доступны, `Scene.fflex` создан, `Ctrl+Shift+F` привязан, `fflex.even_space` реально
работает (равные промежутки), затем двукратный `disable()` не бросает исключение и
не оставляет ни классов, ни операторов, ни пунктов keymap, ни пустого add-on
keymap, ни `Scene.fflex`; повторное включение снова регистрирует всё и не дублирует
горячую клавишу.

---

## Отличия кода от легаси-версии 1.5.0

Функциональность та же; изменена только обвязка регистрации, без неё обновление
и повторное включение аддона ломались:

* `__init__.py` — удалён `bl_info` (в расширении метаданные живут в манифесте).
* `compat.py` — добавлены `register_classes()` / `unregister_classes()`: проверяют
  `cls.is_registered` и не падают на повторной регистрации. Важная деталь:
  `hasattr(bpy.types, "<Имя>")` **не работает** для `PropertyGroup` и
  `AddonPreferences` — они зарегистрированы, но через `bpy.types` не видны
  (проверено на 5.2), поэтому ориентироваться на `bpy.types` нельзя.
* `props.py`, `ui.py`, `ops_flex.py`, `ops_space.py` — используют эти хелперы.
  Раньше `unregister_class()` для уже снятого класса бросал
  `RuntimeError: missing bl_rna attribute … (may not be registered)` посередине
  уборки, и всё, что шло после него (handlers, меню, `Scene.fflex`), оставалось.
* `ui.py` — `register_keymap()` сначала снимает прежнюю привязку,
  `unregister_keymap()` удаляет и сам add-on keymap, когда в нём не осталось
  пунктов (пустой keymap иначе сохраняется в `userpref.blend`), а `register()`
  перед добавлением пунктов в меню убирает их (иначе при перезагрузке скриптов
  пункты меню накапливались).

Правки внесены в файлы этого репозитория. Легаси-копия
(`…\Documents\blender vibecoding\FFlex` и установленная в
`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\FFlex`) осталась
прежней — при переходе на репозиторий её лучше удалить.

---

## Лицензия

GPL-3.0-or-later (`SPDX:GPL-3.0-or-later`), как указано в манифесте и в заголовках
файлов аддона.
