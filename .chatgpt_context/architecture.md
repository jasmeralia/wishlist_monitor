# Architecture Notes

## Purpose
This file describes the stable, high-level architecture of the application.

## Components
- Python modules for business logic.
- Docker containers for runtime isolation.
- Jinja2 templates for rendering configuration or documentation.

## Guidance for Codex
- When modifying a file, use the active file in VS Code as the authoritative source.
- Do not pull code from memory; only from:
  - the active file
  - pinned context
  - explicit user instructions

## Structure Expectations
- Modular design.
- Single responsibility per module.
- Clear separation of:
  - I/O handling
  - business logic
  - data models
  - templates
  - CLI interfaces
