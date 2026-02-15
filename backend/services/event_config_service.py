from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from backend.config import (
    COMMANDER_BG_ZOOM,
    DEFAULT_BG_QUERY,
    DEFAULT_BG_ZOOM,
    EVENT_CONFIG_FILE_PATH,
    MAX_ROUNDS,
    PARTICIPANTS_FILE_PATH,
    SUGGEST_LIMIT,
    SUGGEST_MIN_CHARS,
)
from backend.repositories.json_store import atomic_write_json


class EventState(str, Enum):
    REGISTRATION_EMPTY = "registration_empty"
    REGISTRATION_OPEN = "registration_open"
    RAFFLE_STARTED = "raffle_started"
    PAIRINGS_RUNNING = "pairings_running"
    VOTING = "voting"


class SettingsLockLevel(str, Enum):
    ALWAYS = "always"
    UNTIL_FIRST_REGISTRATION = "until_first_registration"
    UNTIL_RAFFLE_START = "until_raffle_start"
    UNTIL_PAIRINGS_START = "until_pairings_start"
    UNTIL_VOTING_START = "until_voting_start"


class SettingsUpdateError(Exception):
    pass


class ScryfallSettings(BaseModel):
    commander_suggest_query_template: str = "game:paper is:commander name:{q}"
    partner_suggest_query_template: str = "game:paper is:partner name:{q}"
    partner_capable_query_template: str = '!"{name}" is:partner'
    default_background_query: str = DEFAULT_BG_QUERY
    random_commander_query: str = "game:paper is:commander -t:background"
    round_report_avatar_query_template: str = 'game:paper is:normal !"{name}"'
    commander_preview_query_template: str = 'game:paper is:commander !"{name}"'
    card_preview_query_template: str = 'game:paper is:commander !"{name}"'
    card_preview_fallback_query_template: str = 'game:paper !"{name}"'


class UISettings(BaseModel):
    default_bg_zoom: float = DEFAULT_BG_ZOOM
    commander_bg_zoom: float = COMMANDER_BG_ZOOM
    chip_preview_modal_style: bool = False
    chip_preview_reveal_animation: bool = False
    chip_preview_swipe_enabled: bool = False


class APISettings(BaseModel):
    suggest_min_chars: int = Field(default=SUGGEST_MIN_CHARS, ge=1, le=10)
    suggest_limit: int = Field(default=SUGGEST_LIMIT, ge=1, le=100)


class VotingSettings(BaseModel):
    scheme_type: str = "top3_fixed"
    points_scheme: dict[str, Any] = Field(default_factory=lambda: {
        "play_phase": {"1": 4, "2": 3, "3": 2, "4": 1},
        "best_deck_voting": {"1": 3, "2": 2, "3": 1},
        "best_deck_overall": {
            "1": 8,
            "2": 5,
            "3": 3,
            "4": 2,
            "5": 1,
            "6": 0,
            "7": 0,
            "8": 0,
        },
        "deck_creator_guess": {"correct_guess": 1},
    })


class EventSettings(BaseModel):
    min_decks_to_start: int = Field(default=3, ge=2, le=64)
    default_num_pods: int = Field(default=2, ge=1, le=32)
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, le=30)
    require_all_confirmed_before_pairings: bool = True

    participants: list[str] = Field(default_factory=list)

    scryfall: ScryfallSettings = Field(default_factory=ScryfallSettings)
    ui: UISettings = Field(default_factory=UISettings)
    api: APISettings = Field(default_factory=APISettings)
    voting: VotingSettings = Field(default_factory=VotingSettings)


def _load_default_participants(path: Path = PARTICIPANTS_FILE_PATH) -> list[str]:
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    except Exception:
        return []


def get_default_settings(participants_path: Path = PARTICIPANTS_FILE_PATH) -> EventSettings:
    return EventSettings(participants=_load_default_participants(participants_path))


DEFAULT_SETTINGS = get_default_settings()


SETTING_LOCKS: dict[str, SettingsLockLevel] = {
    "participants": SettingsLockLevel.UNTIL_FIRST_REGISTRATION,
    "min_decks_to_start": SettingsLockLevel.UNTIL_RAFFLE_START,
    "default_num_pods": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "max_rounds": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "require_all_confirmed_before_pairings": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.commander_suggest_query_template": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.partner_suggest_query_template": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.partner_capable_query_template": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.default_background_query": SettingsLockLevel.ALWAYS,
    "scryfall.random_commander_query": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.round_report_avatar_query_template": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.commander_preview_query_template": SettingsLockLevel.UNTIL_PAIRINGS_START,
    "scryfall.card_preview_query_template": SettingsLockLevel.ALWAYS,
    "scryfall.card_preview_fallback_query_template": SettingsLockLevel.ALWAYS,
    "ui.default_bg_zoom": SettingsLockLevel.ALWAYS,
    "ui.commander_bg_zoom": SettingsLockLevel.ALWAYS,
    "ui.chip_preview_modal_style": SettingsLockLevel.ALWAYS,
    "ui.chip_preview_reveal_animation": SettingsLockLevel.ALWAYS,
    "ui.chip_preview_swipe_enabled": SettingsLockLevel.ALWAYS,
    "api.suggest_min_chars": SettingsLockLevel.ALWAYS,
    "api.suggest_limit": SettingsLockLevel.ALWAYS,
    "voting.scheme_type": SettingsLockLevel.UNTIL_VOTING_START,
    "voting.points_scheme": SettingsLockLevel.UNTIL_VOTING_START,
}


def _to_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()


def load_event_settings(
    path: Path = EVENT_CONFIG_FILE_PATH,
    participants_path: Path = PARTICIPANTS_FILE_PATH,
) -> tuple[EventSettings, dict[str, Any]]:
    """
    Returns parsed settings + metadata.
    Metadata contains source and optional validation error string.
    """
    if not path.exists():
        return get_default_settings(participants_path), {"source": "defaults", "path": str(path), "error": None}

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        return get_default_settings(participants_path), {"source": "defaults", "path": str(path), "error": f"read_error: {exc}"}

    try:
        parsed = EventSettings.model_validate(payload)
        if not parsed.participants:
            parsed.participants = get_default_settings(participants_path).participants
        return parsed, {"source": "file", "path": str(path), "error": None}
    except ValidationError as exc:
        return get_default_settings(participants_path), {"source": "defaults", "path": str(path), "error": f"validation_error: {exc}"}


def detect_event_state(
    start_file_exists: bool,
    raffle_list: list[dict],
    pairings_state: dict | None,
) -> EventState:
    deck_count = len([e for e in raffle_list if e.get("deck_id") is not None])

    if not start_file_exists:
        if deck_count == 0:
            return EventState.REGISTRATION_EMPTY
        return EventState.REGISTRATION_OPEN

    phase = ((pairings_state or {}).get("phase") or "").strip().lower()
    active_round = int((pairings_state or {}).get("active_round") or 0)

    if phase == "voting":
        return EventState.VOTING
    if active_round > 0:
        return EventState.PAIRINGS_RUNNING
    return EventState.RAFFLE_STARTED


def _is_level_editable(level: SettingsLockLevel, event_state: EventState) -> bool:
    if level == SettingsLockLevel.ALWAYS:
        return True
    if level == SettingsLockLevel.UNTIL_FIRST_REGISTRATION:
        return event_state == EventState.REGISTRATION_EMPTY
    if level == SettingsLockLevel.UNTIL_RAFFLE_START:
        return event_state in {EventState.REGISTRATION_EMPTY, EventState.REGISTRATION_OPEN}
    if level == SettingsLockLevel.UNTIL_PAIRINGS_START:
        return event_state in {
            EventState.REGISTRATION_EMPTY,
            EventState.REGISTRATION_OPEN,
            EventState.RAFFLE_STARTED,
        }
    if level == SettingsLockLevel.UNTIL_VOTING_START:
        return event_state != EventState.VOTING
    return False


def settings_editability(event_state: EventState) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, level in SETTING_LOCKS.items():
        out[key] = {
            "lock_level": level.value,
            "editable": _is_level_editable(level, event_state),
        }
    return out


def settings_as_dict(settings: EventSettings) -> dict[str, Any]:
    return _to_dict(settings)


def _flatten_patch(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key

        # If this path is a full setting key, keep it as leaf (including dict values)
        if path in SETTING_LOCKS:
            out[path] = value
            continue

        if isinstance(value, dict):
            nested = _flatten_patch(value, path)
            out.update(nested)
            continue

        out[path] = value
    return out


def _set_by_dotted_path(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    cursor = data
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value


def _editable_keys(event_state: EventState) -> set[str]:
    editable = set()
    for key, level in SETTING_LOCKS.items():
        if _is_level_editable(level, event_state):
            editable.add(key)
    return editable


def apply_settings_patch(
    current_settings: EventSettings,
    patch: dict[str, Any],
    event_state: EventState,
) -> tuple[EventSettings, list[str]]:
    if not isinstance(patch, dict) or not patch:
        raise SettingsUpdateError("Patch payload must be a non-empty JSON object.")

    flattened = _flatten_patch(patch)
    unknown_keys = sorted([k for k in flattened.keys() if k not in SETTING_LOCKS])
    if unknown_keys:
        raise SettingsUpdateError(f"Unknown settings keys: {', '.join(unknown_keys)}")

    editable_keys = _editable_keys(event_state)
    blocked = sorted([k for k in flattened.keys() if k not in editable_keys])
    if blocked:
        raise SettingsUpdateError(f"Settings are locked for current event state ({event_state.value}): {', '.join(blocked)}")

    merged = settings_as_dict(current_settings)
    for key, value in flattened.items():
        _set_by_dotted_path(merged, key, value)

    try:
        updated = EventSettings.model_validate(merged)
    except ValidationError as exc:
        raise SettingsUpdateError(f"Validation failed: {exc}") from exc

    return updated, sorted(flattened.keys())


def save_event_settings(settings: EventSettings, path: Path = EVENT_CONFIG_FILE_PATH) -> None:
    atomic_write_json(path, settings_as_dict(settings))


def reset_settings_with_locks(
    current_settings: EventSettings,
    event_state: EventState,
) -> tuple[EventSettings, list[str], list[str]]:
    default_dict = settings_as_dict(get_default_settings())
    editable_keys = _editable_keys(event_state)

    editable_patch: dict[str, Any] = {}
    skipped_locked_keys: list[str] = []

    for key in SETTING_LOCKS.keys():
        value = default_dict
        for part in key.split('.'):
            value = value[part]

        if key in editable_keys:
            _set_by_dotted_path(editable_patch, key, value)
        else:
            skipped_locked_keys.append(key)

    if not editable_patch:
        return current_settings, [], sorted(skipped_locked_keys)

    updated, changed_keys = apply_settings_patch(current_settings, editable_patch, event_state)
    return updated, changed_keys, sorted(skipped_locked_keys)
