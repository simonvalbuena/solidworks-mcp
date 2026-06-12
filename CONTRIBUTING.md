# Contributing to solidworks-mcp

Thanks for your interest in contributing!

## Development setup

**Windows is required** — the COM layer (`src/sw_connection.py`, `src/tools/file_ops.py`) imports `pythoncom` / `win32com` at module level, and pywin32 only installs on Windows. This applies even if you just want to run the tests.

**SolidWorks is NOT required** for development: the COM layer is fully mocked in the test suite.

```powershell
git clone https://github.com/haunchen/solidworks-mcp.git
cd solidworks-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pytest
```

## Running tests

```powershell
.venv\Scripts\pytest tests/ -v
```

All tests must pass without SolidWorks installed. Real-machine verification against SolidWorks 2021 is performed by the maintainer before merging behavior-affecting changes.

## Conventions

- **Specs are behavior contracts.** Specs in `docs/specs/` with `status: active` describe the expected behavior of shipped features. If your change alters observable behavior, update the corresponding spec in the same PR.
- **Commits** follow conventional commits (`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`). English or Chinese descriptions are both fine.
- **COM code style**: tool handlers are async and must dispatch COM work via `sw.execute(...)` — never touch COM objects outside the worker thread. See `CLAUDE.md` for architecture constraints.

## Pull requests

1. Fork the repo and create a feature branch from `main`
2. Make your changes; keep tests green (`pytest tests/ -v`)
3. Open a PR against `main` describing what changed and why; reference the spec requirement IDs you touched (e.g. `balloon.md #R3`)

CI runs the full test suite on `windows-latest` for every PR.
