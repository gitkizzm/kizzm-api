import json
from pathlib import Path

from backend.repositories.json_store import atomic_write_json


def load_pairings(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_pairings(path: Path, data: dict) -> None:
    atomic_write_json(path, data)
