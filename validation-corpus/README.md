# Natural-content validation corpus

This directory is a local-only launch point for benchmarks on media you own or are
licensed to process. Media and results are ignored by Git. Rights evidence should
stay in your controlled records; the manifest stores only an internal reference.

1. Copy `manifest.example.yaml` to `manifest.local.yaml`.
2. Put source files under `media/` and replace every placeholder rights reference.
3. Validate without media while preparing the manifest:

   ```bash
   .venv/bin/python tools/natural_corpus.py validate \
     validation-corpus/manifest.example.yaml --allow-missing-media
   ```

4. Validate real files and run the complete profile/encoder matrix:

   ```bash
   .venv/bin/python tools/natural_corpus.py validate \
     validation-corpus/manifest.local.yaml
   .venv/bin/python tools/natural_corpus.py run \
     validation-corpus/manifest.local.yaml \
     --results validation-corpus/results/baseline
   ```

Add `--with-sscd` only when the `[ml]` extra and approved model weights are present.
Each cell retains benchmark/QA JSON, HTML and logs; `summary.json` binds the source
SHA-256, rights reference, profile and encoder. The existing raw similarity fields
are diagnostics, not evidence about an external rights-management system. Registered
quality metrics from RFC #12 will be added to this same result layout after approval.
