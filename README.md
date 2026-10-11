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

## Совместимость версий Blender

Нижняя граница задаётся строкой `blender_version_min` в манифесте. Сейчас там
**`4.5.0`** — Blender предложит аддон из репозитория начиная с 4.5 LTS.

| Blender | Что проверено | Результат |
|---|---|---|
| 4.5.14 LTS | `validate`/`build`/`server-generate`, харнесс (3 цикла), установка из репозитория, `check_from_repo.py` (23 проверки) | PASS |
| 4.5.14 LTS | GUI-прогон `t3_gui.py` (реальный вьюпорт: GPU-оверлей, HUD, модальный автомат, пикинг, деформация, скриншоты) | 94 OK, 0 FAIL |
| 5.2.0 LTS | то же самое | PASS |
| 5.1.2 / 5.0 | не прогонялись; 5.1 и 5.2 используют один и тот же enum `VIEW_3D` | — |

4.2–4.4 не проверялись (для них нужна отдельная сборка Blender), поэтому ставить
там `4.2.0` — это обещание без доказательства.

Та же правка перенесена в легаси-сборку: актуальный архив для «ручной» установки —
`FFlex-1.5.1.zip` (рядом с исходниками в `Documents\blender vibecoding`).
Прежний `FFlex-1.5.0.zip` оставлен как есть — он соответствует старому коду.

### Что мешало работать на 4.5

`compat.py` угадывал идентификатор 3D-вьюпорта по номеру версии:

```python
VIEW3D_TYPE = "VIEW3D" if bpy.app.version < (5, 0, 0) else "VIEW_3D"   # было
```

Но переименование `VIEW3D` → `VIEW_3D` произошло **до** 5.0: в 4.5.14 в enum
остался только `VIEW_3D`, и регистрация падала целиком:

```
TypeError: validating class: enum "VIEW3D" not found in
('EMPTY', 'VIEW_3D', 'IMAGE_EDITOR', …)
```

То есть легаси-версия FFlex 1.5.0 (та, что с `bl_info` и «Blender 4.2+») на 4.5
не запускалась вовсе. Теперь тип определяется запросом к самому enum, без
угадывания по версии, — работает и на 4.5, и на 5.x.

### Обновление уже установленного аддона

После публикации новой версии Blender подхватит её сам (*Check for Updates on
Startup*) или вручную: список репозиториев → **Sync** (круговые стрелки), затем в
карточке FFlex — **Update**. Из консоли:

```powershell
blender --background --online-mode --command extension update
```

Важно: обновление приходит **только при смене номера версии**. Переопубликовать
тот же номер нельзя — Blender не увидит разницы, а повторная установка того же
номера с другим размером архива упадёт с `Archive size mismatch`.

## История версий

**1.5.1** (текущая)
* работает на Blender 4.x: идентификатор 3D-вьюпорта определяется по enum, а не по
  номеру версии (на 4.5 аддон не регистрировался вообще — `enum "VIEW3D" not found`);
* идемпотентные `register()`/`unregister()`: обновление и повторное выключение не
  падают посередине уборки и не оставляют за собой классы, меню и `Scene.fflex`;
* пустой add-on keymap удаляется вместе с последним пунктом и больше не копится в
  `userpref.blend`;
* нижняя граница опущена до `4.5.0`;
* тот же набор правок перенесён в легаси-сборку `FFlex-1.5.1.zip`.

**1.5.0** — первая версия в репозитории (только Blender 5.0+).

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

## Чем расширение отличается от легаси-сборки

Код обеих сборок (1.5.1) теперь одинаковый, отличается только обёртка:

| | здесь (репозиторий) | `FFlex-1.5.1.zip` |
|---|---|---|
| метаданные | `blender_manifest.toml` | `bl_info` в `__init__.py` |
| имя модуля | `bl_ext.<id репозитория>.fflex` | `FFlex` |
| установка | из репозитория, обновление одной кнопкой | вручную: `Preferences → Add-ons → Install` |

Общие правки 1.5.1 (есть в обеих сборках) — обвязка регистрации, без неё
обновление и повторное включение аддона ломались:

* `compat.py` — `VIEW3D_TYPE` больше не угадывается по `bpy.app.version`
  (см. «Совместимость версий Blender»): идентификатор 3D-вьюпорта берётся из enum
  `Panel.bl_space_type`, иначе на 4.5 регистрация падала с
  `enum "VIEW3D" not found`.
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

Единственное отличие файлов — `__init__.py`: в репозитории нет `bl_info`
(метаданные в манифесте), в легаси-сборке он есть.

---

## Лицензия

GPL-3.0-or-later (`SPDX:GPL-3.0-or-later`), как указано в манифесте и в заголовках
файлов аддона.
