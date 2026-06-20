# Railway Deploy Notes

This file documents non-obvious decisions in `railway.json` and `nixpacks.toml`.

## (11) Why Poetry is installed via nixPkgs in the setup phase

### The bug

The first Railway deploy failed with:

```
RUN poetry config virtualenvs.create false
/bin/bash: line 1: poetry: command not found
exit code: 127
```

### Root cause

Nixpacks' Python provider installs Poetry via `pip install poetry==$NIXPACKS_POETRY_VERSION`
**inside the default install phase** (confirmed in
[`src/providers/python.rs`](https://github.com/railwayapp/nixpacks/blob/main/src/providers/python.rs)).
The full default install command is:

```
python -m venv --copies /opt/venv \
  && . /opt/venv/bin/activate \
  && pip install poetry==$NIXPACKS_POETRY_VERSION \
  && poetry install --no-dev --no-interaction --no-ansi
```

Our custom `[phases.install]` in `nixpacks.toml` (without a `"..."` extension) **completely
replaces** those defaults. So when our `poetry config virtualenvs.create false` runs, Poetry
has never been installed.

### Fix chosen — add `poetry` to `[phases.setup] nixPkgs`

Poetry is available as a Nix package (`poetry`) from the NixOS package registry
([search.nixos.org](https://search.nixos.org/packages?channel=unstable&query=poetry)).
Adding it to the setup phase via:

```toml
[phases.setup]
nixPkgs = ["...", "poetry"]
```

installs Poetry system-wide **before** any phase commands run. The `"..."` syntax
(documented at [nixpacks.com/docs/configuration/file](https://nixpacks.com/docs/configuration/file))
extends — rather than replaces — the nixPkgs the Python provider already adds (Python, gcc, etc.).

This approach was chosen over alternatives because:
- It avoids running `poetry install` twice (which would happen if we extended install cmds with `"..."`)
- It keeps `poetry config virtualenvs.create false` working (see section 7 below)
- The Nix-provided poetry binary is on PATH system-wide, independent of any venv
- It is idiomatic Nixpacks: the setup phase is the right place for system-level tooling

The same `setup` phase is declared in both `nixpacks.toml` and the inline `nixpacksPlan` in
`railway.json` to keep them consistent (consolidation tracked in issue #7).

## (7) Why `poetry config virtualenvs.create false` precedes `poetry install`

The repo's Poetry config has `virtualenvs.create = true` and `virtualenvs.in-project = true`
(confirmed in `poetry config --list`). Under those defaults, `cd backend && poetry install -E s3`
creates a venv at `backend/.venv/` and installs uvicorn into `backend/.venv/bin/`.

When Railway starts the container it runs the bare `startCommand` from the repo root:

```
uvicorn core.api.main:app --host 0.0.0.0 --port $PORT
```

`backend/.venv/bin` is not on `PATH` at that point, so the default config produces
`uvicorn: command not found` at boot.

**Fix chosen — option (a):** run `poetry config virtualenvs.create false` in the install phase
before `poetry install`. This tells Poetry to skip venv creation for this process and install
packages directly into the Nixpacks build image's Python (which IS on PATH). The result is that
`uvicorn`, `alembic`, and all other dependencies are importable and executable from the image's
system Python at runtime.

Option (b) — adding `backend/.venv/bin` to the runtime PATH — was rejected because it is
fragile: the path depends on the venv being created, the Python version suffix in the path, and
the cwd at build time. Option (a) is the idiomatic Railway/Nixpacks approach.

## (8) How `PYTHONUNBUFFERED` and `PYTHONPATH` reach the runtime container

### Why `nixpacksPlan.variables` was WRONG

The original config placed these env vars in `railway.json`'s `build.nixpacksPlan.variables`.
That field does not exist in the Railway JSON schema
(`https://backboard.railway.app/railway.schema.json`). Railway silently ignores unknown
`nixpacksPlan` keys, so the vars were never actually set — `PYTHONPATH` would be unset at
runtime, causing `uvicorn core.api.main:app` to fail with a module-not-found import error.

### Corrected mechanism — `nixpacks.toml` + `nixpacksConfigPath`

The fix uses two real, schema-supported fields:

**1. `build.nixpacksConfigPath` in `railway.json`** (Railway schema field)

Railway passes this file path to the Nixpacks builder as the standalone config file.
`nixpacksConfigPath` is a documented field in the Railway JSON schema
(`https://backboard.railway.app/railway.schema.json`).

**2. `[variables]` in `nixpacks.toml`** (Nixpacks standalone config format)

The Nixpacks documentation defines the `[variables]` section as:

> "Key-value pairs of variables to include in the **final image**."
>
> Source: https://nixpacks.com/docs/configuration/file (Configuration File Reference)

Nixpacks emits `[variables]` entries as Docker `ENV` instructions in the generated
`Dockerfile`. They are baked into the image and are present at every container lifecycle
phase — build, release (`preDeployCommand`), and runtime (`startCommand`). They do NOT
require Railway service variables to be separately configured in the dashboard.

`nixpacks.toml` (repo root) now declares:

```toml
[variables]
PYTHONUNBUFFERED = "1"
PYTHONPATH = "backend/src"
```

and is wired into `railway.json` via:

```json
"build": {
  "builder": "NIXPACKS",
  "nixpacksConfigPath": "nixpacks.toml",
  ...
}
```

### Python provider declaration

`nixpacks.toml` also declares `providers = ["python"]` at the top level. This ensures
Nixpacks provisions the Python + Poetry toolchain even though `pyproject.toml` lives in
`backend/` rather than the repo root (where Nixpacks looks for auto-detection). Without an
explicit provider, detection may fall through and `poetry: command not found` would abort
the install phase.
