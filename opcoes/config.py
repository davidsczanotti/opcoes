from __future__ import annotations

import os
from pathlib import Path

# Caminho padrão do banco. Pode ser sobrescrito via variável OPCOES_DB_PATH.
DEFAULT_DB_PATH = Path("data/opcoes_snapshots.db")


def get_db_path() -> Path:
    """Retorna o caminho do banco, permitindo override por env."""

    override = os.getenv("OPCOES_DB_PATH")
    if override:
        return Path(override).expanduser()
    return DEFAULT_DB_PATH


__all__ = ["get_db_path", "DEFAULT_DB_PATH"]
