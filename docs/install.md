# Установка и запуск

Полный гайд: от чистой системы до работающего CLI / GUI / собранного
desktop-бинарника.

> Команды показаны для macOS / Linux. На Windows используй PowerShell:
> `python -m venv .venv` → `.venv\Scripts\activate` → дальше всё то же
> самое.

## 0. Готовые сборки (рекомендуется для большинства)

Начиная с **v1.0.0**, каждый релиз на
[GitHub Releases](https://github.com/hostlife22/Video-Deduplicator/releases)
несёт три desktop-бинарника + `SHA256SUMS` для проверки:

| Файл                                | OS              | Подпись                | Что внутри                                             |
|-------------------------------------|-----------------|------------------------|--------------------------------------------------------|
| `yt-uniq-gui-*.AppImage`            | Linux (x86_64)  | ✅ self-contained       | PyInstaller бандл + **bundled static ffmpeg/ffprobe**  |
| `yt-uniq-gui-macOS.zip` (`.app`)    | macOS 12+       | ⚠️ ad-hoc только (v1.1.0+) — см. ниже | `.app` бандл; ffmpeg = system (brew install ffmpeg)    |
| `yt-uniq-gui-Windows.zip` (`.exe`)  | Windows 10/11   | ❌ unsigned (см. ниже)  | PyInstaller бандл; ffmpeg = system (`choco install`)   |
| `SHA256SUMS`                        | все             | —                       | стандартный `sha256sum -c`-формат                       |

### Установка по платформам

**Linux (AppImage — рекомендуется):**

```bash
# 1) Скачать
curl -LO https://github.com/hostlife22/Video-Deduplicator/releases/download/v1.0.0/yt-uniq-gui-1.0.0-x86_64.AppImage
curl -LO https://github.com/hostlife22/Video-Deduplicator/releases/download/v1.0.0/SHA256SUMS

# 2) Проверить целостность (заменяет codesign)
sha256sum -c SHA256SUMS --ignore-missing

# 3) Сделать исполняемым и запустить
chmod +x yt-uniq-gui-*.AppImage
./yt-uniq-gui-*.AppImage
```

AppImage **самодостаточен** — Python, PyQt6 и ffmpeg/ffprobe внутри.
Распаковывать ничего не нужно; запускается на любом дистрибутиве с
glibc 2.31+ (Ubuntu 20.04+, Fedora 33+, Debian 11+).

**macOS (ad-hoc signed `.app`, v1.1.0+):**

С v1.1.0 бандл проходит ad-hoc codesign (`codesign --sign -`) — это
**не** Developer ID и **не** notarization, но подпись стабилизирует
бандл между обновлениями macOS, так что повторных «is damaged and
can’t be opened» при апгрейдах OS больше нет. Первый запуск
по-прежнему требует ручного bypass Gatekeeper:

```bash
# 1) Скачать + проверить
curl -LO https://github.com/hostlife22/Video-Deduplicator/releases/download/v1.1.0/yt-uniq-gui-macOS.zip
curl -LO https://github.com/hostlife22/Video-Deduplicator/releases/download/v1.1.0/SHA256SUMS
shasum -a 256 -c SHA256SUMS --ignore-missing

# 2) Распаковать
unzip yt-uniq-gui-macOS.zip
mv yt-uniq-gui.app /Applications/

# 3) Первый запуск — Gatekeeper покажет
#    "yt-uniq-gui cannot be opened because the developer cannot be verified".
#    Это ожидаемо: ad-hoc подпись ≠ Developer ID.
#
#    Bypass (один раз):
#       Right-click /Applications/yt-uniq-gui.app → Open
#       → Open Anyway → ввести админ-пароль.
#
#    После этого Gatekeeper запоминает решение, и обычный двойной
#    клик работает без диалога. Та же UX, что у HandBrake / Audacity
#    до того, как они купили Apple Developer Program.
#
# 4) Альтернатива — снять карантин-флаг вручную:
xattr -d com.apple.quarantine /Applications/yt-uniq-gui.app
#    (так делают brew cask formulas; работает без диалога вообще).
#
# ffmpeg должен быть в PATH:
brew install ffmpeg chromaprint
```

**Если spctl кричит «code object is not signed at all»** — значит
скачался pre-v1.1.0 бандл; обновись до последней release или примени
`codesign --deep --force --sign - /Applications/yt-uniq-gui.app`
вручную, чтобы получить ту же подпись, которую CI ставит.

**Windows (unsigned `.exe`):**

v1.1.0 продолжает шипиться **без подписи** под Windows — sponsored
SignPath OSS и коммерческие EV-сертификаты вне scope этого relase
(см. `specs/v1.0.1-to-v1.3-roadmap.plan.md` § v1.1.0 Task 9). UX
почти как с unsigned macOS-бандлом: SmartScreen один раз показывает
warning, дальше тишина.

```powershell
# 1) Скачать + проверить (PowerShell 5+)
Invoke-WebRequest -Uri "https://github.com/hostlife22/Video-Deduplicator/releases/download/v1.1.0/yt-uniq-gui-Windows.zip" -OutFile yt-uniq-gui-Windows.zip
Invoke-WebRequest -Uri "https://github.com/hostlife22/Video-Deduplicator/releases/download/v1.1.0/SHA256SUMS" -OutFile SHA256SUMS
Get-FileHash yt-uniq-gui-Windows.zip -Algorithm SHA256
# Сверить с строкой в SHA256SUMS. Любой mismatch = повреждён или
# подменён → НЕ распаковывать.

# 2) Распаковать
Expand-Archive yt-uniq-gui-Windows.zip -DestinationPath .

# 3) Первый запуск. Есть два пути.
#
#    Path A — bypass через UI (для большинства пользователей):
#       Двойной клик → SmartScreen выдаст синее окно
#       "Windows protected your PC". Кликнуть "More info" →
#       "Run anyway". Дальше Windows запоминает решение и
#       больше не спрашивает.
#
#    Path B — снять Mark-Of-The-Web сразу через PowerShell:
Unblock-File .\yt-uniq-gui\yt-uniq-gui.exe
#       Или, если разархивирована вся папка:
Get-ChildItem -Path .\yt-uniq-gui -Recurse | Unblock-File
.\yt-uniq-gui\yt-uniq-gui.exe
#       После Unblock-File SmartScreen-диалог не появится вовсе.

# ffmpeg должен быть в PATH:
choco install ffmpeg
```

**Корпоративные Windows-окружения с AppLocker / WDAC:** unsigned
exe там обычно полностью запрещён и Path A/B не помогут — нужен
admin override или signed cert. Это backlog-задача (см. roadmap);
для personal use оба пути выше работают на стоковой Windows 10/11.

### Почему unsigned на macOS/Windows?

v1.0.0 шипится **без** Apple Developer ID (~$99/год) и Windows code
signing cert (~$200-400/год). Это сознательное решение для open-source
проекта без коммерческого спонсорства. Подпись прилетит как **v1.0.x
patch release** как только credentials появятся; **bundle сам не
поменяется** — будет та же сборка, прогнанная через codesign + notarytool
(macOS) или signtool (Windows).

Verification без подписи делается через **SHA256SUMS**, которое
шипится на той же странице релиза. Любой mismatch = downloaded
file повреждён или подменён → не запускать, скачать заново с
официального GitHub URL.

См. `installers/README.md` в репозитории для технических деталей
будущей signing-pipeline.

### Supply-chain verification (v1.1.0+): cosign + SBOM

Помимо `SHA256SUMS` каждый артефакт в релизе сопровождается
**cosign keyless-bundle** (`<asset>.cosign.bundle`) и единым
**CycloneDX SBOM** (`sbom.cdx.json`). Подпись выпускается через
GitHub OIDC — никаких долгоживущих ключей у мейнтейнера нет,
поэтому ничего не нужно ротировать, и подменить релиз без записи
в публичный Sigstore Rekor log невозможно.

```bash
# Установить cosign один раз:
brew install cosign           # macOS
sudo apt install cosign       # Debian/Ubuntu 24.04+
# Или скачать бинарник: https://github.com/sigstore/cosign/releases

# Проверить любой артефакт (пример — AppImage):
cosign verify-blob \
  --bundle yt-uniq-gui-1.1.0-x86_64.AppImage.cosign.bundle \
  --certificate-identity-regexp 'https://github\.com/Hostlife22/Video-Deduplicator/' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  yt-uniq-gui-1.1.0-x86_64.AppImage
# → Verified OK

# SBOM в CycloneDX 1.5 JSON:
cyclonedx validate --input-file sbom.cdx.json    # схему проверить
# Или скормить в Grype / Trivy / OSV-Scanner для CVE-аудита:
grype sbom:sbom.cdx.json
```

Если cosign verification фейлит — значит артефакт подменён по
дороге (CDN, mirror, MITM); качайте напрямую с
`https://github.com/Hostlife22/Video-Deduplicator/releases`.

### Когда выбирать source-install вместо бинарника?

Источниковый путь (секции 2-7 ниже) нужен если:

- хочешь dev-mode + быстрый итерационный цикл (`make dev` + редактирование `src/`);
- нужны опциональные extras (`[ml]` для SSCD, `[scene]` для PySceneDetect, `[web]` для headless FastAPI);
- работаешь на arm64 Linux (AppImage пока x86_64-only);
- собираешь собственный бинарник для платформы, которую релизы не покрывают (BSD, alpine musl).

## 1. Системные требования

| Что | Минимум | Зачем |
|---|---|---|
| Python | 3.11+ | core stack |
| ffmpeg + ffprobe | 4.0+ (на PATH) | без них вообще ничего не работает |
| RAM | 4 GB | encoding комфортно идёт от 4 GB; для 4 K — 8 GB |
| Disk | 2 × размер исходника | сегменты + work_dir + final output |

**Опциональные бинарники** (graceful fallback если отсутствуют):

| Что | Зачем |
|---|---|
| `fpcalc` (chromaprint) | audio fingerprint similarity + corpus matching |
| ffmpeg с `libvmaf` | VMAF score в QA report |
| ffmpeg с `zscale` (zimg) | HDR-keep wrap, HDR→SDR tonemap |
| ffmpeg с `librubberband` | formant-preserving pitch shift (cid_aware profile) |
| `nvidia-smi` | auto-detect NVENC concurrent-session cap |

### Установка ffmpeg

**macOS (Homebrew):**
```bash
brew install ffmpeg          # обычно включает libvmaf, librubberband, zimg
brew install chromaprint     # для fpcalc (audio FP)
```

На актуальном Homebrew минимальная формула `ffmpeg` может не включать `zscale`
или `rubberband`. Для HDR/rubberband matrix используй keg-only full build и
явные overrides:

```bash
brew install ffmpeg-full
export YT_UNIQ_FFMPEG="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
export YT_UNIQ_FFPROBE="$(brew --prefix ffmpeg-full)/bin/ffprobe"
```

FFmpeg subprocesses have a 600-second no-output watchdog by default. Set
`YT_UNIQ_STALL_TIMEOUT_SEC=0` to disable it or another number of seconds to tune
it. `YT_UNIQ_WALL_TIMEOUT_SEC` is disabled by default and can impose a hard
per-process wall limit when required by an unattended deployment.

**Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install -y ffmpeg libchromaprint-tools
# Если нужен libvmaf / librubberband — собирать ffmpeg из исходников
# или ставить из ppa:savoury1/ffmpeg6
```

**Windows:**
```powershell
choco install ffmpeg chromaprint
# Или скачать из gyan.dev / BtbN/FFmpeg-Builds (нужны full builds)
```

**Проверка:**
```bash
ffmpeg -version | head -1
ffprobe -version | head -1
fpcalc -version 2>/dev/null || echo "fpcalc отсутствует — audio FP будет skipped"
```

---

## 2. Клонирование репозитория

```bash
git clone https://github.com/Hostlife22/yt_uniquifier.git
cd yt_uniquifier
```

---

## 3. Установка Python окружения

Создай изолированный venv. Без него `pip install` сломает системный Python.

```bash
python3.12 -m venv .venv          # на Linux/Mac: python3 если 3.12 default
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate            # Windows PowerShell
```

### Варианты установки

| Команда | Что ставит | Для кого |
|---|---|---|
| `pip install -e .` | CLI + core | минимум, только командная строка |
| `pip install -e ".[gui]"` | + PyQt6 + WebEngine | для `yt-uniq-gui` |
| `pip install -e ".[qa]"` | + chromaprint Python bindings | если нужен audio FP через pyacoustid |
| `pip install -e ".[dev]"` | + pytest + ruff + mypy + pytest-qt | разработка / запуск тестов |
| `pip install -e ".[dev,gui]"` | всё включено | **рекомендуется** |

Команда для большинства случаев:

```bash
pip install -e ".[dev,gui]"
```

`PyQt6-WebEngine` весит ~150 MB — это нормально, нужен для embedded QA
viewer в GUI. Если хочешь сэкономить место — поставь только `[gui]`
без dev, или вообще пропусти gui (CLI будет работать).

### Проверка установки

```bash
yt-uniq --help                     # CLI работает
yt-uniq probe --encoders           # детектит твои ffmpeg encoders
python -c "from yt_uniquifier.gui.app_pyqt import main; print('GUI ready')"
```

Auto-selection использует quality-first software encoder. Для проверенного
hardware throughput задайте `YT_UNIQ_ENCODER_POLICY=speed`; для практичного AV1
long-form CPU encode — `YT_UNIQ_ENCODER_POLICY=balanced`. Явный `--encoder`
строгий: недоступный encoder завершает preflight/run ошибкой, а не подменяется.

---

## 4. Первый запуск — CLI

Самый базовый flow:

```bash
# 1. Узнать что в файле
yt-uniq probe /path/to/master.mp4 | jq '.video[0]'

# 2. Валидация source vs YouTube targets + HDR/HEVC sanity
yt-uniq preflight /path/to/master.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml

# 3. Запустить uniquification
yt-uniq run /path/to/master.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out /tmp/uniq.mp4 \
  --encoder libx264
# Прогресс-бар покажет процесс. На 2h 1080p источнике — 30-60 мин.

# 4. Открыть QA report в браузере
open /tmp/uniq.mp4.qa.html          # macOS
xdg-open /tmp/uniq.mp4.qa.html      # Linux
start /tmp/uniq.mp4.qa.html         # Windows
```

Полный CLI reference — [README §CLI reference](https://github.com/Hostlife22/yt_uniquifier#cli-reference)
или `yt-uniq <команда> --help` для любой подкоманды.

---

## 5. Первый запуск — GUI

```bash
yt-uniq-gui
```

Откроется окно с sidebar навигацией на 10 экранов:

1. **Run** — drag-drop input → auto-probe → preflight → Run
2. **Batch** — директория файлов через ту же пайплайн
3. **Calibrate** — bisect intensity к target self-match
4. **QA Viewer** — embedded HTML отчёт + standalone QA pair
5. **Profile Editor** — редактирование YAML профилей
6. **History** — последние 100 запусков
7. **Corpus** — индекс прежних загрузок для self-collision check
8. **Queue** — distributed batch на shared FS
9. **Validation** — 3-step wizard для real-CID validation harness
10. **Settings** — theme switch, default profile, reset cache

Полная инструкция — [docs/gui.md](./gui.md).

### Workflow для первого знакомства

1. Open Run screen (default).
2. Drag-drop 30-second mp4 в поле "Input video".
3. Browse для output path.
4. Profile: `cid_aware`. Encoder: `auto`.
5. Click "Run preflight" → увидишь findings.
6. Click ▶ Run. Segment timeline пойдёт.
7. После завершения → KPI pills + "Open QA report".

---

## 6. Запуск тестов (опционально)

```bash
# Все тесты (~2 минуты)
pytest -q

# Только unit (быстро, ~10s)
pytest tests/unit/ -q

# Только GUI тесты (headless)
QT_QPA_PLATFORM=offscreen pytest tests/unit/test_gui_*.py -q

# Smoke test (открывает MainWindow, проходит по 10 экранам)
QT_QPA_PLATFORM=offscreen pytest tests/smoke/ -q
```

Lint + type-check:

```bash
ruff check .
mypy src/yt_uniquifier
```

Должно быть зелёное: 467 passed, 1 skipped, ruff/mypy clean.

---

## 7. Сборка desktop-бинарника

Опционально — если хочешь distributable `.app` / `.exe` / Linux executable.

```bash
pip install pyinstaller
pyinstaller pyinstaller/yt-uniq-gui.spec --clean --noconfirm
```

| OS | Результат | Запуск |
|---|---|---|
| **macOS** | `dist/yt-uniq-gui.app` (~250 MB) | `open dist/yt-uniq-gui.app` |
| **Windows** | `dist/yt-uniq-gui/yt-uniq-gui.exe` | double-click |
| **Linux** | `dist/yt-uniq-gui/yt-uniq-gui` | `./dist/yt-uniq-gui/yt-uniq-gui` |

**Первый запуск unsigned binary:**

- **macOS Gatekeeper** заблокирует. Right-click на `.app` → **Open** →
  click "Open" в диалоге. После одного раза система запомнит.
- **Windows SmartScreen** покажет warning. Click "More info" → "Run anyway".
- **Linux** — обычно сразу запускается; на некоторых дистрибутивах нужно
  `chmod +x dist/yt-uniq-gui/yt-uniq-gui` перед первым запуском.

**Альтернатива PyInstaller** — `pipx` (работает на всех платформах
одинаково):

```bash
pipx install 'yt-uniquifier[gui]'
yt-uniq-gui                # доступна из любой shell без активации venv
```

---

## 8. Troubleshooting

### "ffmpeg: command not found"

Установи ffmpeg (см. §1). Проверь что он на PATH:
```bash
which ffmpeg     # macOS/Linux
where ffmpeg     # Windows
```

### "No module named 'PyQt6'"

Не установлен `[gui]` extra. Запусти:
```bash
pip install -e ".[dev,gui]"
```

### "PyQt6-WebEngine не работает" / QA Viewer показывает label

Либо WebEngine не установлен (`pip install PyQt6-WebEngine~=6.7`), либо
ты в headless среде (`QT_QPA_PLATFORM=offscreen`). В обоих случаях
fallback "Open in browser" работает.

### Linux: "Could not load Qt platform plugin 'xcb'"

Не хватает системных библиотек:
```bash
sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
                 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
                 libxkbcommon-x11-0 libgl1
```

### "audio.pitch.rubberband.missing" preflight fail

Твой ffmpeg собран без `--enable-librubberband`. Варианты:
1. Использовать профиль без rubberband (например `medium.yaml` вместо
   `cid_aware.yaml`).
2. Переустановить ffmpeg с поддержкой rubberband (`brew install ffmpeg`
   на macOS — обычно уже включено; для Linux собирать из исходников).
3. Запустить с `--no-preflight` (но pitch transform упадёт на ffmpeg
   уровне).

### Wayland (Linux): drag-drop не работает в GUI

Известная Qt + Wayland проблема. Используй "Browse…" кнопку вместо
drag-drop, или запусти под XWayland: `QT_QPA_PLATFORM=xcb yt-uniq-gui`.

### Cache / state corruption

Удали кеши и попробуй снова:
```bash
rm -rf ~/.cache/yt_uniquifier/
rm -rf ~/.config/yt_uniquifier/
```

### Encoder detection slow at first launch (~3-5 s)

Нормально — детектируем каждый из ~10 кандидатов через real test-run.
Результат кешируется в `~/.cache/yt_uniquifier/encoders.json`. В UI:
**Settings → Reset encoder cache** если что-то пошло не так и нужна
перепроверка.

---

## 9. Update + clean uninstall

```bash
# Обновить до последнего main
cd yt_uniquifier
git pull
pip install -e ".[dev,gui]" --upgrade

# Полное удаление
pip uninstall yt-uniquifier
rm -rf .venv ~/.cache/yt_uniquifier ~/.config/yt_uniquifier
# и удалить директорию проекта если она тебе не нужна
```

---

## TL;DR — три команды

С `make` (рекомендуется на macOS/Linux):

```bash
git clone https://github.com/Hostlife22/yt_uniquifier.git && cd yt_uniquifier
make dev                  # создаёт .venv + ставит [dev,gui] extras
make gui                  # запуск desktop UI
```

Без `make` (Windows или системы без GNU make):

```bash
git clone https://github.com/Hostlife22/yt_uniquifier.git && cd yt_uniquifier
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev,gui]"
yt-uniq-gui
```

## Make-таргеты

```
make help              # показывает все таргеты с описанием

# environment
make venv              # создать .venv
make install           # .venv + production install ([gui])
make dev               # .venv + dev install ([dev,gui]) — рекомендовано
make dev-min           # .venv + [dev] only (без PyQt6 — CLI work)

# quality gates
make lint              # ruff check
make lint-fix          # ruff check --fix
make typecheck         # mypy --strict
make test              # pytest -q (full suite, ~2 min)
make test-unit         # только unit тесты (~10s)
make test-gui          # GUI тесты headless
make test-integration  # integration (нужен ffmpeg)
make check             # lint + typecheck + test (всё сразу)

# run
make gui               # yt-uniq-gui
make cli               # yt-uniq --help
make probe-encoders    # список доступных ffmpeg encoders

# packaging
make build             # PyInstaller .app/.exe/binary в dist/
make build-wheel       # pip-installable .whl в dist/

# maintenance
make reset-cache       # сбросить encoder/keyframe кеш
make clean             # удалить dist/, build/, __pycache__, *_cache
make distclean         # clean + удалить .venv (полный reset)
```
