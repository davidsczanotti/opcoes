# Workflow: Opções

## Development Process
1. **Feature/Bug Identification:** Define the task in a new Track.
2. **Implementation:**
    - Develop code following project patterns.
    - Update CLI or Web interface as needed.
3. **Verification:**
    - Run unit tests: `poetry run pytest`.
    - Run E2E tests if scraper changes: `RUN_E2E_TESTS=1 poetry run pytest tests/test_scraper_e2e.py`.
4. **Documentation:** Update README or AGENTS.md if significant changes are made.

## Coding Standards
- Type hints are encouraged.
- Modular architecture (scraper, strategies, web, tax, etc.).
- SQLite as the single source of truth for persistent state.
