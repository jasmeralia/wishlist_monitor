# Python Style Guide

## Versions
- Python 3.10+ minimum.
- Prefer type hints everywhere (PEP 484, PEP 561).

## Style
- Follow ruff defaults + Black formatting style.
- Line length: 88.
- Use pathlib over os.path when possible.
- Prefer dataclasses for structured data; avoid bare dicts for configs.
- No wildcard imports.
- Avoid unnecessary cleverness; clarity wins.

## Typing Rules
- Always annotate function parameters and return types.
- Use `TypedDict` or `Protocol` when shape matters.
- Avoid `Any`. Use `Union` or `Literal` instead.
- Use `Optional[X]` only when `None` is an expected case.
- Document assumptions for values that come from environment variables.

## Error Handling
- Fail early.
- Wrap external service calls with meaningful exception messages.
- Never swallow exceptions silently.

## Logging
- Use the stdlib `logging` module.
- Never print directly except in CLI entrypoints.

## Structure
- Keep modules small and focused.
- Use an `__init__.py` to define the public API of a module.
- Place reusable utilities in `/lib/` or `/common/`.

