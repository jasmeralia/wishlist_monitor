# Common Requests & Behaviors

## When user says “apply this change”
Produce a unified diff patch whenever possible.

## When user asks for “update this file”
- Modify only the open file unless they specify others.
- Respect the project style guides.

## When user asks for “generate a new module”
- Include:
  - Type hints
  - Docstring
  - Example usage if helpful
  - Logging where appropriate

## When user asks for Docker support
- Include multi-stage Dockerfile.
- Validate Compose structure.
- Assume modern Docker (v24+).

## When generating Jinja2
- Keep templates minimal and declarative.
- Avoid mixing Python-like logic into templates.
