# Spec 08 — Fingerprint-aware QA + Corpus

> **Phase 8 (v0.2)** · 2 дня · **No deps** (parallel-safe with 06, 07)

## Goal

QA-отчёт измеряет не только «насколько output похож на input» (v0.1), а
**вероятность матча output против любого файла в пользовательском корпусе**.
То есть отвечает на вопрос «не попадёт ли это в Content ID-collision с моими
же предыдущими загрузками».

## Scope

**In:**

- `core/qa/corpus.py` — индекс уже-загруженных файлов с pHash + audio FP.
- `core/qa/cid_predict.py` — predictive scorer по chunk-метрикам.
- `cli/cmd_corpus.py` — `yt-uniq corpus add/list/remove`.
- `cli/cmd_qa.py` — флаг `--vs-corpus`.
- `core/qa/report.py` — секция «vs corpus» + heatmap по 4-сек блокам.
- `core/qa/templates/report.html.j2` — heatmap UI.
- Расширение `QAReport` модели.

**Not in:** прямой вызов YouTube Content ID API (нет такого публичного API);
distributed corpus (только локальный кеш).

## Modules

### `core/qa/corpus.py`

```python
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np

CORPUS_DIR = Path.home() / ".cache" / "yt_uniquifier" / "corpus"

@dataclass(frozen=True)
class CorpusEntry:
    id: str                  # sha1(path) prefix
    path: Path
    added_at: float          # unix timestamp
    duration_sec: float
    audio_fingerprint: list[int] | None   # chromaprint subfingerprints
    phash_frames: list[int] | None        # 64-bit phash per sample frame
    sample_count: int


class Corpus:
    def __init__(self, root: Path = CORPUS_DIR) -> None: ...

    def add(self, path: Path, *, samples: int = 60) -> CorpusEntry: ...
    def remove(self, entry_id: str) -> bool: ...
    def list_all(self) -> list[CorpusEntry]: ...

    def search_match(
        self, target: Path, *,
        threshold: float = 0.5,
    ) -> list[tuple[CorpusEntry, float]]:
        """Return entries with predicted match probability >= threshold.

        Match probability = max(audio_fp_jaccard, mean_phash_similarity).
        """
```

Хранение:
- `index.json` — список entries (без heavy data).
- `<entry_id>.npz` — numpy-сохранение фреймовых pHash + audio fingerprint
  bytes.
- Atomic write через `.tmp` + `os.replace`.

### `core/qa/cid_predict.py`

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ChunkSimilarity:
    start_sec: float
    end_sec: float
    phash_similarity: float
    audio_similarity: float


@dataclass(frozen=True)
class CIDPredictResult:
    match_probability_self: float    # input vs output, max-of-chunks
    chunk_similarities: list[ChunkSimilarity]
    weakest_chunk: ChunkSimilarity | None  # chunk most likely to trigger match
    corpus_matches: list[tuple[str, float]]  # entry_id, prob


def predict(
    input_path: Path,
    output_path: Path,
    *,
    chunk_sec: float = 4.0,
    corpus: "Corpus | None" = None,
) -> CIDPredictResult:
    """Chunked similarity prediction.

    Content ID operates on overlapping ~4-second windows; a global average
    hides the weakest chunk. We measure per-chunk and take the max.
    """
```

Имплементация:
1. Через `ffmpeg -ss N -t 4 -f image2pipe -vcodec png ...` извлекаем по
   одному ключевому кадру каждого 4-сек блока для обоих файлов → pHash.
2. Через `ffmpeg -ss N -t 4 -f wav -` извлекаем сэмплы → передаём в `fpcalc`
   (или, если нет, считаем простой spectral centroid через `astats`).
3. Для каждого блока считаем pHash distance + audio Jaccard.
4. `match_probability_self = max(per_chunk_combined_similarity)`.
5. Если `corpus` дан — поиск через `corpus.search_match(output_path)`.

### `cli/cmd_corpus.py`

```python
import typer
from pathlib import Path
from yt_uniquifier.core.qa.corpus import Corpus

corpus_app = typer.Typer(no_args_is_help=True, help="Manage the local fingerprint corpus.")

@corpus_app.command("add")
def cmd_add(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None: ...

@corpus_app.command("list")
def cmd_list() -> None: ...

@corpus_app.command("remove")
def cmd_remove(entry_id: str) -> None: ...
```

Регистрируется как sub-app: `app.add_typer(corpus_app, name="corpus")`.

### `cli/cmd_qa.py` — `--vs-corpus`

Новый флаг:
```python
vs_corpus: bool = typer.Option(False, "--vs-corpus",
    help="Also check output against the local corpus.")
```

При установке — после обычного report'а:
```python
if vs_corpus:
    corpus = Corpus()
    matches = corpus.search_match(output, threshold=0.3)
    # Сериализуется в QAReport.corpus_matches
```

### `core/models.py` — расширение QAReport

```python
class QAReport(BaseModel):
    # … existing v0.1 fields …
    # NEW:
    cid_predict_self: float | None = None
    weakest_chunk_sec: tuple[float, float] | None = None
    chunk_similarities: list[dict] = []   # serialised ChunkSimilarity
    corpus_matches: list[dict] = []       # [{"entry_id":..., "path":..., "prob":...}]
```

### `core/qa/templates/report.html.j2` — heatmap

Добавляется секция «Per-chunk similarity»:
```html
<div class="section">
  <h2>Per-chunk similarity</h2>
  <p>Each cell is a 4-second window. Darker red = closer to source (CID risk).</p>
  <div class="heatmap">
    {% for c in report.chunk_similarities %}
    <span class="cell" style="background: {{ heatmap_color(c.combined) }}"
          title="{{ c.start_sec|round(1) }}s–{{ c.end_sec|round(1) }}s: {{ c.combined|round(3) }}">
    </span>
    {% endfor %}
  </div>
</div>
{% if report.corpus_matches %}
<div class="section">
  <h2>Corpus matches</h2>
  <table>
    <tr><th>entry</th><th>match probability</th></tr>
    {% for m in report.corpus_matches %}
    <tr><td>{{ m.path }}</td><td>{{ m.prob|round(3) }}</td></tr>
    {% endfor %}
  </table>
</div>
{% endif %}
```

`heatmap_color(x)` — jinja filter, маппинг 0..1 → CSS color (green→yellow→red).

## Acceptance

```bash
# 1. Add own previous upload to corpus.
yt-uniq corpus add ~/uploads/old_master.mp4
yt-uniq corpus list
# Lists entry with id, path, duration.

# 2. Process a new variant.
yt-uniq run ~/uploads/old_master.mp4 \
  --profile profiles/cid_aware.yaml \
  --out ~/uploads/variant_2.mp4

# 3. Standalone QA against corpus.
yt-uniq qa ~/uploads/old_master.mp4 ~/uploads/variant_2.mp4 --vs-corpus
# Output includes:
#   cid_predict_self: 0.42
#   weakest_chunk_sec: (12.0, 16.0)
#   corpus_matches: [(old_master.mp4, 0.42)]

# 4. HTML shows heatmap.
open ~/uploads/variant_2.mp4.qa.html
# Heatmap visible, weakest chunks coloured red.

# 5. Identity pair → match probability 1.0.
yt-uniq qa file.mp4 file.mp4 --vs-corpus
# cid_predict_self ≈ 1.0, corpus_matches contains file.mp4 with prob ≈ 1.0
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_corpus_lifecycle.py` | add → list contains; remove → list does not; atomic write |
| Unit | `tests/unit/test_corpus_search.py` | mock entry с известным fingerprint, поиск с порогом |
| Unit | `tests/unit/test_cid_predict_chunks.py` | mock chunk metrics, проверка max и weakest_chunk |
| Unit | `tests/unit/test_cid_predict_no_corpus.py` | predict() без corpus возвращает empty matches |
| Unit | `tests/unit/test_heatmap_color.py` | thresholds для green/yellow/red |
| Integration | `tests/integration/test_corpus_workflow.py` | add tiny_clip → process variant → search_match возвращает tiny_clip с prob > 0.5 (на synthetic — высокий self-match потому что transforms слабы на 2s) |
| Integration | `tests/integration/test_qa_with_corpus_cli.py` | end-to-end через CliRunner: add → run → qa --vs-corpus → JSON содержит corpus_matches |

## Risks

| Риск | Митигация |
|---|---|
| Корпус разрастается, lookup O(N×chunks) | Cap размера (env `YT_UNIQ_CORPUS_MAX=500`); LRU-eviction; pre-filter по duration |
| Chunk-by-chunk extraction медленный для длинных файлов | Использовать `-ss` + `-t` (быстрый seek по keyframe); кеш per-corpus-entry pHash уже извлечён при `add` |
| `fpcalc` отсутствует — audio часть predict'а падает | Graceful: возвращаем `audio_similarity = 0.0` per chunk с warning, only pHash используется |
| Heatmap не рендерится в плохих браузерах | Все CSS inline; spans с background-color работают везде |
| `weakest_chunk` интерпретируется как «доказательство матча» | В UI добавить tooltip: «not a definitive Content ID prediction; informational signal only» |
| User добавляет в corpus сами output'ы yt-uniquifier → ложно высокий self-match | `corpus add` warning, если файл содержит `-metadata encoder=yt-uniquifier/*` (наш метатег) |

## Hand-off

После Phase 8:
- `CIDPredictResult` доступен всему ядру как метрика «уникальности».
- Phase 9 (calibration) использует `predict(input, output).match_probability_self`
  как целевую функцию для bisect.
- HTML-отчёт содержит heatmap — пользователь видит, где output близок к input.
