# Python Tooling Reference

## Ruff
- Treat all warnings as fixable.
- Autofix where appropriate.
- If ruff flags ambiguity, prefer explicit code.

## Mypy
- Use strict mode:
  - `--strict`
  - `--warn-unused-ignores`
  - `--warn-redundant-casts`
- If ignoring types, comment why.

## Virtualenv
- Use `python -m venv .venv`.
- Dependencies documented in `requirements.txt` or `pyproject.toml`.

## Testing
- Use pytest.
- Test names should describe behavior, not implementation.

## CLI Patterns
- Use argparse or typer.
- Use exit codes meaningfully.
