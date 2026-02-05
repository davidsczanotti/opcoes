# Product Guidelines: Opções

## Principles
- **Accuracy:** Financial data must be handled with precision.
- **Auditability:** Keep clear logs/history of operations (especially for DARF and positions).
- **Usability:** The CLI should be intuitive, and the Web UI should provide clear insights.
- **Resilience:** The scraper should handle connection issues and site changes gracefully.

## Conventions
- **Database Paths:** Always respect `OPCOES_DB_PATH` or use defaults in `data/`.
- **Date Formats:** Use ISO format (YYYY-MM-DD) for internal storage and CLI inputs.
- **Numbers:** Handle Brazilian locale (pt-BR) formatting for CSV exports but keep standard numeric types internally.
