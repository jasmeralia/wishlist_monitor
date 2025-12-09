# Project Overview

This folder provides stable reference context for Codex so that all generated code
remains consistent with this project's conventions.

## Languages Used
- Python (3.10+)
- Docker / Docker Compose
- Jinja2 templating

## Goals for AI Assistance
- Keep file edits consistent with the *current* version in the workspace.
- Only modify files when explicitly requested.
- Produce diffs when possible rather than rewriting entire files.
- Follow the conventions in all files in this folder.

## Project Principles
- Predictability over cleverness.
- Explicitness in configuration.
- Deterministic formatting.
- All code must pass ruff and mypy unless exceptions are documented.
