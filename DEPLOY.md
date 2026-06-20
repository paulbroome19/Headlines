# Railway Deploy Notes

This file documents two non-obvious decisions in `railway.json`.

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

## (8) Why `nixpacksPlan.variables` reaches the runtime container

The Nixpacks documentation defines the `[variables]` section as:

> "Key-value pairs of variables to include in the **final image**."
>
> Source: https://nixpacks.com/docs/configuration/file (Configuration File Reference)

"Final image" means these are emitted as Docker `ENV` instructions in the generated
`Dockerfile` and are therefore baked into the image that Railway actually runs. They are
present for every container lifecycle phase — build, release (`preDeployCommand`), and runtime
(`startCommand`). They are NOT build-only; they do NOT require Railway service variables to be
separately configured.

`PYTHONPATH=backend/src` and `PYTHONUNBUFFERED=1` are therefore correctly set via
`nixpacksPlan.variables` and will be visible to the `uvicorn` process at runtime.
