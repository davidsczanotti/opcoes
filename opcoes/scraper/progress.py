from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from .storage import CSV_FIELDS, _csv_reader, _ensure_parent

PROGRESS_VERSION = 1


def default_progress_path(output_csv: Path) -> Path:
    return output_csv.with_suffix(".progress.json")


def default_checkpoint_path(output_csv: Path) -> Path:
    return output_csv.with_suffix(".checkpoint.csv")


def load_progress(path: Path) -> Optional[Dict[str, object]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_progress(path: Path, data: Dict[str, object]) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def load_checkpoint_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader, _ = _csv_reader(f)
        for raw in reader:
            if not raw:
                continue
            row = {field: (raw.get(field) or "").strip() for field in CSV_FIELDS}
            rows.append(row)
    return rows
