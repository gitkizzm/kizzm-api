import json
from pathlib import Path

from backend.repositories.json_store import atomic_write_json


def load_raffle_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            content = json.load(f)
            if isinstance(content, list):
                return content
            if isinstance(content, dict):
                return [content]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def write_raffle_list(path: Path, data: list[dict]) -> None:
    atomic_write_json(path, data)
