# Transform plugins

> Added in v0.8.0. See `specs/v0.8-plan.md` § R1.

Built-in transforms (crop+rescale, color jitter, loudnorm, pitch+tempo …) live in
`src/yt_uniquifier/core/transforms/` and self-register via `register(TransformSpec(…))`
at import time. A **plugin** is just an external Python package that does exactly
the same thing — it ships a module that calls `register(...)`, and advertises that
module under the `yt_uniquifier.transforms` entry-points group so yt-uniquifier
discovers it automatically.

No fork is needed. No changes to `core/`. A plugin published to PyPI becomes
visible to every yt-uniquifier install in the same virtualenv after `pip install`.

## Trust model

Plugins are ordinary Python packages, so loading one runs arbitrary code from a
third party. Trust them the same way you'd trust any dependency you `pip install`.
yt-uniquifier wraps every plugin import in `try/except` so a broken plugin logs a
warning and is skipped (it cannot brick the tool), but it does **not** sandbox.

## Minimal plugin (hello-world)

Project layout:

```
my-plugin/
├── pyproject.toml
└── src/
    └── my_uniq_pingpong/
        └── __init__.py
```

`src/my_uniq_pingpong/__init__.py` — the registration runs as a side effect of
import, exactly like the built-ins:

```python
from pydantic import BaseModel, Field
from yt_uniquifier.core.transforms import FilterChain, LabelAllocator, TransformSpec, register


class PingPongParams(BaseModel, extra="forbid"):
    strength: float = Field(0.0, ge=0.0, le=1.0)


def build(params: PingPongParams, alloc: LabelAllocator, in_label: str) -> FilterChain:
    out = alloc.next("v")
    # Trivial example: identity filter wrapped to demonstrate the contract.
    return FilterChain(in_label=in_label, out_label=out, filter_str="null")


register(TransformSpec(id="video.pingpong", kind="video", schema=PingPongParams, build=build))
```

`pyproject.toml` — the entry-point declaration is what wires it up:

```toml
[project]
name = "my-uniq-pingpong"
version = "0.1.0"
dependencies = ["yt-uniquifier", "pydantic>=2"]

[project.entry-points."yt_uniquifier.transforms"]
pingpong = "my_uniq_pingpong"
```

Install in the same env as yt-uniquifier:

```bash
pip install -e ./my-plugin
yt-uniq probe --list-transforms | grep pingpong
# video.pingpong
```

Reference the new transform from any profile YAML:

```yaml
transforms:
  - id: video.pingpong
    params: { strength: 0.5 }
```

## Contract reminder

A transform builder MUST:

* return one `FilterChain` whose `filter_str` does **not** wrap itself in
  `[in_label]...[out_label]` — the pipeline adds that wrapping (see
  `CLAUDE.md` § Architecture invariants).
* be deterministic given the same `(params, in_label, rng)` tuple. If the
  builder uses randomness, accept `rng: random.Random | None = None` and use
  it instead of constructing your own `Random(...)` — otherwise resumed runs
  diverge non-deterministically.
* use the `LabelAllocator` for every new label, never hard-code `v1`, `a3`, …

A transform builder MUST NOT:

* mutate `params` or any caller-provided object,
* call `subprocess` or read the filesystem (that's a `core/` concern),
* import optional dependencies at module top — defer to first call so absence
  is a clean error rather than an import-time crash.

## Failure modes (and where to look)

| Symptom | Likely cause | Where to look |
|---|---|---|
| `WARNING: third-party transform plugin '<name>' failed to load` | Plugin's import raised | Run `python -c "import <module>"` and read the traceback |
| Plugin installed but `yt-uniq probe --list-transforms` doesn't show it | Entry-point name typo, or wrong group | `python -c "from importlib.metadata import entry_points as e; print(list(e(group='yt_uniquifier.transforms')))"` |
| `KeyError: 'video.pingpong'` on `yt-uniq run` despite install | Profile loaded before plugin import order — uninstall+reinstall in a clean venv | Check `pip show <plugin>` confirms install location matches `python -c "import sys; print(sys.path)"` |
| Resume after profile change still uses cached state | Expected — the plan hash changed, so the resume cache key is different. Either restart cleanly or revert the profile | `core/pipeline.py::compute_plan_hash` |

## Versioning

The `register` / `TransformSpec` / `FilterChain` / `LabelAllocator` public
surface is **stable starting v0.8.0**. Breaking changes will only happen at
major version bumps with a release-note migration guide.
