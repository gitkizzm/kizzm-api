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
    SUGGEST_LIMIT,
    SUGGEST_MIN_CHARS,
)


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


class ScryfallSettings(BaseModel):
    commander_suggest_query_template: str = "game:paper is:commander name:{q}"
    partner_suggest_query_template: str = "game:paper is:partner name:{q}"
    partner_capable_query_template: str = '!"{name}" is:partner'
    default_background_query: str = DEFAULT_BG_QUERY
    random_commander_query: str = "game:paper is:commander -t:background"


class UISettings(BaseModel):
    default_bg_zoom: float = DEFAULT_BG_ZOOM
    commander_bg_zoom: float = COMMANDER_BG_ZOOM


class APISettings(BaseModel):
    suggest_min_chars: int = Field(default=SUGGEST_MIN_CHARS, ge=1, le=10)
    suggest_limit: int = Field(default=SUGGEST_LIMIT, ge=1, le=100)


class VotingSettings(BaseModel):
    scheme_type: str = "top3_fixed"
    points_scheme: dict[str, int] = Field(default_factory=lambda: {"first": 3, "second": 2, "third": 1})


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


DEFAULT_SETTINGS = EventSettings()


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
    "ui.default_bg_zoom": SettingsLockLevel.ALWAYS,
    "ui.commander_bg_zoom": SettingsLockLevel.ALWAYS,
    "api.suggest_min_chars": SettingsLockLevel.ALWAYS,
    "api.suggest_limit": SettingsLockLevel.ALWAYS,
    "voting.scheme_type": SettingsLockLevel.UNTIL_VOTING_START,
    "voting.points_scheme": SettingsLockLevel.UNTIL_VOTING_START,
}


def _to_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()


def load_event_settings(path: Path = EVENT_CONFIG_FILE_PATH) -> tuple[EventSettings, dict[str, Any]]:
    """
    Returns parsed settings + metadata.
    Metadata contains source and optional validation error string.
    """
    if not path.exists():
        return DEFAULT_SETTINGS, {"source": "defaults", "path": str(path), "error": None}

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        return DEFAULT_SETTINGS, {"source": "defaults", "path": str(path), "error": f"read_error: {exc}"}

    try:
        parsed = EventSettings.model_validate(payload)
        return parsed, {"source": "file", "path": str(path), "error": None}
    except ValidationError as exc:
        return DEFAULT_SETTINGS, {"source": "defaults", "path": str(path), "error": f"validation_error: {exc}"}


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
