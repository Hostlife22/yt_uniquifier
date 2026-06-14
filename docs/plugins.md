# Transform plugins

> Added in v0.8.0. Hardened in v1.2.0 with manifest + capability gate + audit-hook
> sandbox (Task 23). See `specs/v0.8-plan.md` § R1 and the v1.2.0 roadmap.

Built-in transforms (crop+rescale, color jitter, loudnorm, pitch+tempo …) live in
`src/yt_uniquifier/core/transforms/` and self-register via `register(TransformSpec(…))`
at import time. A **plugin** is just an external Python package that does exactly
the same thing — it ships a module that calls `register(...)`, and advertises that
module under the `yt_uniquifier.transforms` entry-points group so yt-uniquifier
discovers it automatically.

No fork is needed. No changes to `core/`. A plugin published to PyPI becomes
visible to every yt-uniquifier install in the same virtualenv after `pip install`.

## Manifest (v1.2.0)

Every third-party plugin distribution MUST ship a `yt_uniquifier_plugin.toml`
file at the package root. The manifest declares which transform kinds the plugin
is allowed to register; trying to register a kind the manifest didn't opt in to
raises `PluginViolation` at load time.

```toml
[plugin]
name = "my-uniq-pingpong"
version = "0.1.0"
capabilities = ["video_transform"]   # add "audio_transform" to register audio
# sha256 = "…"                       # optional self-declared wheel hash
```

`name` and `version` are required.  `capabilities` is a list of:

| Capability         | Permits `TransformSpec.kind` | Notes |
|--------------------|------------------------------|-------|
| `video_transform`  | `"video"`                    | Required for any `video.*` ID |
| `audio_transform`  | `"audio"`                    | Required for any `audio.*` ID |

A plugin omitting `capabilities` is loadable (its manifest is valid) but cannot
register any transform — useful as a no-op probe to verify discovery wiring.

Ship the manifest as package data so `importlib.metadata.distribution(...).files`
includes it.  Example `pyproject.toml` snippet:

```toml
[tool.setuptools.package-data]
my_uniq_pingpong = ["yt_uniquifier_plugin.toml"]
```

## Trust model (v1.2.0)

Plugins are ordinary Python packages, so loading one runs arbitrary code from a
third party. v1.2.0 adds two defence-in-depth layers on top of the entry-point
`try/except` introduced in v0.8.0:

1. **Manifest capability gate** — `register()` calls from a plugin without a
   manifest, or whose manifest doesn't list the matching capability, are
   rejected before the transform reaches the registry.
2. **Audit-hook sandbox** — every plugin's import-time code and every
   transform builder it ships runs inside a `sys.addaudithook` gate.
   Denylisted operations (filesystem writes — `os.unlink`/`os.remove`/`os.rename`,
   network egress — `socket.connect`/`socket.bind`, subprocess spawns —
   `subprocess.Popen`/`os.exec*`, dynamic `exec`/`compile`) raise
   `PluginViolation` instead of silently succeeding.

The sandbox is implemented via [PEP 578 audit hooks](https://peps.python.org/pep-0578/)
and is cross-platform (CPython, no OS-specific code).  It catches anything that
flows through the CPython runtime; a plugin that ships a C extension and issues
raw syscalls bypasses this layer.  Linux `seccomp` is a candidate for a stronger
second layer in a future release.

### Operator controls

| Flag / env var | Effect |
|---|---|
| `--no-plugins`  /  `YT_UNIQ_NO_PLUGINS=1`               | Skip all third-party plugins. The env var is fully pre-import; the CLI flag post-filters the registry so import-time side effects have already run. |
| `--plugins-allowlist a,b`  /  `YT_UNIQ_PLUGINS_ALLOWLIST=a,b` | Keep only plugins whose `[plugin].name` is in the comma-separated list. |
| `--unsafe-plugins`                                       | Disable the audit-hook sandbox.  Use only with trusted internal plugins; do not use with PyPI installs. |

Prefer the env-var form in production deployments: it takes effect before any
plugin import, so a malicious plugin's `__init__.py` never runs.

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
