# Docker & Compose Standards

## Dockerfile Rules
- Base images:
  - Python: `python:3.10-slim` or newer.
- Always pin versions when possible.
- Use multi-stage builds for Python apps:
  - builder stage installs deps
  - final stage runs app with minimal footprint
- Always use non-root user if feasible.
- Place application code in `/app`.

## Python in Docker
- Use `pip install --no-cache-dir`.
- Copy only required files to reduce layer churn.
- Always set `ENV PYTHONUNBUFFERED=1`.

## Compose Standards
- Never bind-mount the entire project into production containers.
- Use `.env` file or environment variables.
- Services must define:
  - healthchecks  
  - restart policies  
  - resource limits  

### Example Compose Labels (Traefik / monitoring)
- If generating Compose YAML, follow indentation of 2 spaces.
- Always define versionless Compose.

## Security
- Avoid exposing ports unless required.
- Do not embed secrets anywhere.

