# Release Checklist — `release-x.x.x`

Use this checklist every time you cut a new release. Replace `x.x.x` with the
actual version number (e.g. `1.8.0`).

---

## 1. Create the release branch

```bash
git checkout main && git pull
git checkout -b release-x.x.x
```

---

## 2. Bump version strings

### Python

| File | Location | What to change |
|---|---|---|
| `main.py` | line ~13 | `__version__ = "x.x.x"` |

### HTML / Web UI

| File | Location | What to change |
|---|---|---|
| `web/static/js/sidebar.js` | line ~100 | `<div class="sidebar-version">vx.x.x</div>` |

Grep for the **old** version string across all HTML and JS files and update any
occurrences (footers, titles, badges, etc.):

```bash
grep -rn "OLD_VERSION" web/ metrics.html
```

### Documentation

| File | What to change |
|---|---|
| `README.md` | Title line: `# Changes Worker  vx.x.x` |
| `README.md` | Any prose that mentions the version |

---

## 3. Update RELEASE_NOTES.md

Add a new section **at the top** of `RELEASE_NOTES.md` following the existing
format:

```markdown
## vx.x.x — YYYY-MM-DD

### New Features
- …

### Bug Fixes
- …

### Changes
- **Version bump** — All footers and version references updated from vOLD to vx.x.x.
```

---

## 4. Update README.md

- Update the version in the title (`# Changes Worker  vx.x.x`).
- Add / revise any sections that describe new features, changed behaviour,
  or removed functionality shipped in this release.
- Verify the architecture diagrams (`img/architecture.png`,
  `img/architecture_attach.png`) are still accurate; regenerate if the pipeline
  changed.

---

## 5. Run & verify unit tests

```bash
# Full suite with verbose output
pytest tests/ -v --tb=short

# With coverage (mirrors CI)
pytest tests/ -v --tb=short --cov=. --cov-report=term-missing
```

- **All tests must pass.** Fix any failures before continuing.
- Review coverage — make sure new code paths added in this release have tests.

---

## 6. Run linters (mirrors CI)

```bash
ruff check . --select=E9,F63,F7,F82
ruff format --check .
```

Fix any issues before continuing.

---

## 7. Verify Docker build

```bash
docker compose build
docker compose up -d changes-worker
# Smoke-test: confirm the worker starts and logs the new version
docker compose logs changes-worker | head -20
docker compose down
```

---

## 8. Final review

- [ ] `__version__` in `main.py` matches `x.x.x`
- [ ] `README.md` title shows `vx.x.x`
- [ ] `RELEASE_NOTES.md` has the new section at the top
- [ ] All HTML templates checked for stale version strings
- [ ] `pytest` passes (all green)
- [ ] `ruff` passes (lint + format)
- [ ] Docker image builds and starts cleanly
- [ ] No unrelated / uncommitted changes in the worktree

---

## 9. Merge & tag

```bash
# Commit release changes
git add main.py README.md RELEASE_NOTES.md
# Include any other changed files (HTML, docs, images, etc.)
git commit -m "release: vx.x.x"

# Merge into main
git checkout main && git merge release-x.x.x

# Tag
git tag -a vx.x.x -m "vx.x.x"
git push origin main --tags
```

---

## 10. Post-release

- Create a GitHub Release from the tag, paste the `RELEASE_NOTES.md` section.
- Delete the `release-x.x.x` branch if no longer needed.
- Bump `__version__` on `main` to the next dev version (e.g. `x.x+1.0-dev`)
  if you follow that convention.

---
---

# Best Practices

## Semantic Versioning

Follow [semver](https://semver.org/) strictly:

| Bump | When |
|---|---|
| **MAJOR** (`2.0.0`) | Breaking changes — config schema changes that aren't backward-compatible, removed CLI flags, renamed API endpoints, checkpoint format changes that prevent rollback. |
| **MINOR** (`1.8.0`) | New features that are backward-compatible — new config keys (with defaults), new output modes, new CLI flags. |
| **PATCH** (`1.7.1`) | Bug fixes, doc corrections, dependency patches — no new behaviour. |

## Config.json Compatibility

- **New config keys must have sensible defaults** so existing `config.json`
  files continue to work without changes after an upgrade.
- If a config key is renamed or restructured, add migration logic (see
  `cbl_store.py` schema migration pattern) and document it in the release
  notes under a **Migration** heading.
- Ship an updated `config.json` in the repo with all new keys and comments.

## Dependency Audit

Before every release:

```bash
# Check for outdated packages
pip list --outdated

# Review requirements.txt — pin ranges, not exact versions
cat requirements.txt
```

- Update dependency pins in `requirements.txt` if there are security patches.
- Run the full test suite after any dependency bump.
- If you update `ruff` in `requirements.txt`, also update the rev in
  `.pre-commit-config.yaml` to match.

## Security Review

- [ ] No secrets, passwords, or tokens committed anywhere (grep for
      `password`, `token`, `secret`, `api_key` in committed files).
- [ ] `config.json` in the repo uses only placeholder values
      (`YOUR_DB_NAME`, `YOUR_PASSWORD`, etc.).
- [ ] Self-signed cert options (`accept_self_signed_certs`) default to `false`.
- [ ] Logging redaction (`logging.redaction_level`) is not set to `none` in
      shipped config.

```bash
# Quick secrets scan
grep -rn --include='*.py' --include='*.json' --include='*.yml' \
  -iE '(api_key|secret_key|bearer|password)\s*[:=]\s*"[^"]{8,}"' .
```

## Backward-Compatible Rollback

- **Checkpoint compatibility** — if the checkpoint document format changes,
  the new code should still read old-format checkpoints. Document whether
  rolling back to the previous version is safe.
- **Config compatibility** — new config keys should be ignored gracefully by
  older versions (they simply won't be present).
- Note any rollback caveats in `RELEASE_NOTES.md` under a **⚠️ Rollback
  Notes** heading when applicable.

## Branch & PR Hygiene

- Keep the release branch short-lived — ideally open and merge the PR within
  a day.
- The release PR should contain **only** version bumps, release notes, and doc
  updates. Feature work belongs on `main` before the branch is cut.
- Use a PR title like `release: vx.x.x` for easy filtering in git history.
- Require at least one approval before merging the release PR.

## Testing Beyond Unit Tests

- **Smoke test with real data** — run the worker against a live or staging
  Sync Gateway / CouchDB with a handful of docs flowing through the
  `_changes` feed.
- **Test each output mode you changed** — if you touched postgres output, run
  it against a real Postgres instance. Same for S3, HTTP, stdout.
- **Test Docker** — the CI runs tests natively; always verify the Docker image
  separately since the environment differs (e.g., CBL-C library paths).
- **Test the Admin UI** — open `http://localhost:8080` and click through the
  dashboard, settings, schema, wizard, and logs pages.

## Documentation Completeness

- Every new config key should be documented in `README.md` (at minimum) and
  in relevant `docs/` files.
- Every new UI feature should have or update a doc in `docs/`.
- Architecture diagrams in `img/` should reflect the current pipeline.
- If you added or changed CLI flags, update the `--help` text in `main.py`
  and the README usage section.

## Git Tags & GitHub Releases

- **Always use annotated tags** (`git tag -a`), not lightweight tags.
- Tag message should match the version: `vx.x.x`.
- The GitHub Release body should be a copy of the `RELEASE_NOTES.md` section
  for that version — this makes it easy to browse changes per release.
- Attach any relevant assets (e.g., standalone Docker image tar, sample
  configs) if applicable.

## Hotfix Process

For critical bugs discovered after release:

```bash
git checkout vx.x.x          # start from the release tag
git checkout -b hotfix-x.x.1
# fix, test, bump to x.x.1
git tag -a vx.x.1 -m "vx.x.1"
git checkout main && git merge hotfix-x.x.1
git push origin main --tags
```

- Hotfix releases are always **PATCH** bumps.
- Include a `RELEASE_NOTES.md` entry even for hotfixes.

## CI / GitHub Actions

The CI pipeline (`.github/workflows/ci.yml`) runs lint + tests on every push
and PR. Before releasing:

- Verify the CI badge is green on the release branch.
- CI tests against Python 3.11, 3.12, and 3.13 — make sure all three pass.
- If you've added new test files, confirm they're picked up by `pytest tests/`.

## Communication

- Post a summary in the team channel when the release is published.
- If there are breaking changes or migration steps, call them out explicitly
  so users know what to do before upgrading.
