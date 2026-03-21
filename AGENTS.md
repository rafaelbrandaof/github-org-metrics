# AGENTS.md

## Cursor Cloud specific instructions

This is a single-file Python CLI tool (`github_metrics.py`) that fetches GitHub org metrics via the REST API.

### Quick Reference

- **Install deps:** `uv sync`
- **Lint:** `uv run ruff check .`
- **Format:** `uv run ruff format .`
- **Run:** `uv run python github_metrics.py <org> [options]` (requires `GITHUB_TOKEN` env var)

See `README.md` for full CLI options and usage examples.

### Non-obvious notes

- There are **no automated tests** in this repo — no test files, no test framework.
- The app requires a valid `GITHUB_TOKEN` environment variable with org-level read permissions. Without it, all API calls fail.
- Running against large orgs or repos (e.g. `astral-sh/uv`) can take several minutes due to paginated GitHub API calls. Use `--target-repos` and `--fast` flags to limit scope.
- The `--repos N` flag limits the number of repos **analyzed** but still fetches the full repo list first.
- Repos with very low activity in the analyzed period may trigger a `KeyError: 'Lines Added'` during analysis — this is a pre-existing issue in the codebase, not an environment problem.
- `uv` must be on `PATH`. If freshly installed, ensure `$HOME/.local/bin` is in `PATH`.
