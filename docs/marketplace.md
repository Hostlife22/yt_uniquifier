# Community profile marketplace (F9 / v0.9.0 R1)

Browse and install YAML profiles contributed by the community —
without leaving the CLI or GUI, and without trusting any code to
execute on your machine. The catalog is SHA-pinned per entry; a
profile that does not match its declared hash is refused before
the file lands on disk.

## Trust model

* **HTTPS-only.** The catalog and every profile URL it references
  are forced to `https://`. A non-HTTPS URL is rejected before
  any bytes are read, regardless of where the URL came from.
* **SHA-256 pinning.** Each catalog entry includes a hex SHA-256
  of the profile YAML it points at. `install` downloads, hashes,
  and refuses to write the file if the digest does not match.
* **Schema-validated.** Even after a SHA match, the YAML is run
  through `Profile.model_validate` (with `extra=forbid`) *before*
  the atomic rename into the per-user profile dir. A malformed
  profile never reaches its final filename.
* **No code execution.** Profiles are declarative YAML; loading
  one only invokes the pydantic schema. There is no path by which
  a marketplace entry can execute arbitrary code on your machine.
* **No silent fall-through.** Transport errors (network down,
  TLS failure) fall back to the local cache → in-wheel bootstrap,
  in that order. Configuration-shape errors (oversized payload,
  non-HTTPS URL, JSON parse failure) propagate as fatal — we do
  not serve a stale catalog for a misconfigured upstream.

## CLI

```bash
yt-uniq profile list-community          # cached + sorted by id
yt-uniq profile list-community --refresh
yt-uniq profile show cid_aware          # inspect before install
yt-uniq profile install cid_aware       # → ~/.config/yt_uniquifier/profiles/
yt-uniq profile install cid_aware --dest /custom/dir --overwrite
yt-uniq profile install-dir             # print default install dir
yt-uniq profile purge-cache             # force re-fetch on next call
```

## GUI

The Profile Editor screen has a **Browse community…** button.
The dialog lists every catalog entry, shows the URL and SHA on
selection, and installs into your per-user profile dir on click.
Installed profiles appear in the dropdown without restart.

## Catalog format

```json
{
  "version": 1,
  "entries": [
    {
      "id": "cid_aware",
      "name": "CID-aware (balanced)",
      "description": "Conservative micro-transforms…",
      "url": "https://raw.githubusercontent.com/…/cid_aware.yaml",
      "sha256": "d4cdb9e4877387ec259524da6ef9806c…",
      "author": "yt-uniquifier core",
      "tags": ["cid", "balanced"],
      "version": "1.0.0",
      "min_yt_uniquifier_version": "0.9.0"
    }
  ]
}
```

The schema lives at `core/profile_marketplace.py::Catalog` (with
`extra=forbid`); validation rejects any unknown fields.

## Where things live

| Item                 | Path                                                       |
|----------------------|------------------------------------------------------------|
| Bootstrap catalog    | `<wheel>/yt_uniquifier/marketplace/catalog.json`           |
| Cached catalog       | `~/.cache/yt_uniquifier/marketplace/catalog.json`          |
| Installed profiles   | `~/.config/yt_uniquifier/profiles/<id>.yaml` (default)     |
| Long-term catalog    | `https://raw.githubusercontent.com/yt-uniquifier/yt-uniquifier-profiles/main/catalog.json` |

The bootstrap catalog ships inside the wheel so a fresh install
without network reachability still lists a handful of starter
profiles. When the long-term `yt-uniquifier-profiles` repo
exists, the network fetch wins on the next refresh.

## Contributing a profile

1. Author the YAML against the schema documented in
   [Profiles](profiles.md). Run `yt-uniq preflight <some_source>
   --profile <your.yaml>` to sanity-check.
2. Compute the SHA-256 of the final YAML:

    ```bash
    shasum -a 256 your_profile.yaml
    ```

3. Open a PR against the `yt-uniquifier-profiles` repo (or the
   in-tree `src/yt_uniquifier/marketplace/catalog.json`
   bootstrap) with a new entry. Include `author`, a 1-line
   `description`, and at least one `tag`.
4. Mention the YAML's hosting URL — a raw GitHub URL is the
   canonical choice. The URL **must** be HTTPS.

Reviewers will install the profile from your branch, run it
against a reference source, and verify the SHA matches.
