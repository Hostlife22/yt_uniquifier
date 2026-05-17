"""End-to-end corpus workflow on real ffmpeg."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.conftest import needs_ffmpeg
from yt_uniquifier.cli.app import app
from yt_uniquifier.core.qa.corpus import Corpus


@needs_ffmpeg
@pytest.mark.integration
def test_add_then_search_finds_self(tiny_clip: Path, tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    c = Corpus(corpus_dir)
    entry = c.add(tiny_clip, samples=10)
    assert entry.sample_count > 0

    matches = c.search_match(tiny_clip, threshold=0.5, samples=10)
    assert any(m.entry.id == entry.id for m in matches)
    self_match = next(m for m in matches if m.entry.id == entry.id)
    assert self_match.combined > 0.99


@needs_ffmpeg
@pytest.mark.integration
def test_corpus_cli_add_list_remove(tiny_clip: Path, tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    runner = CliRunner()

    r = runner.invoke(app, [
        "corpus", "add", str(tiny_clip),
        "--samples", "10",
        "--corpus-dir", str(corpus_dir),
    ])
    assert r.exit_code == 0, r.stdout
    assert "added" in r.stdout

    r = runner.invoke(app, [
        "corpus", "list", "--json", "--corpus-dir", str(corpus_dir),
    ])
    assert r.exit_code == 0
    import json as _json
    entries = _json.loads(r.stdout)
    assert len(entries) == 1
    entry_id = entries[0]["id"]

    r = runner.invoke(app, [
        "corpus", "remove", entry_id, "--corpus-dir", str(corpus_dir),
    ])
    assert r.exit_code == 0
    r = runner.invoke(app, [
        "corpus", "list", "--json", "--corpus-dir", str(corpus_dir),
    ])
    assert _json.loads(r.stdout) == []
