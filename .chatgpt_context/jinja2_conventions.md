# Jinja2 Conventions

## Syntax
- Use `{{ variable }}` for values.
- Use `{% ... %}` for control structures.
- No inline logic heavier than basic if/for.

## Variable Safety
- Assume all variables can be missing unless declared required.
- Use filters:
  - `|default('')`
  - `|int`, `|float`
- Avoid manipulating complex objects in the template.

## Formatting
- Keep templates as dumb as possible.
- 2-space indentation for all templates.
- Separate logic into Python code; templates handle display only.

## Template Structure
- Prefer partials when content repeats.
- Avoid deeply nested `{% if %}` structures—refactor upstream if needed.

## File Naming
- Use `.j2` extension.
