import uvicorn
import html
from fastapi import Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from backend.schemas import DeckSchema
from backend.app_factory import create_app
from backend.repositories.json_store import atomic_write_json
from backend.repositories.pairings_repository import load_pairings, write_pairings
from backend.repositories.raffle_repository import load_raffle_list
from backend.services.card_rules import (
    get_image_url,
    has_choose_a_background,
    has_friends_forever,
    is_background,
    partner_with_target_name,
)
from backend.services.pairings_service import (
    apply_round_to_raffle,
    build_rounds,
    first_round_with_hosts,
    pod_sizes,
)
from backend.services.raffle_service import (
    RaffleStartError,
    assign_deck_owners,
    shuffle_decks as raffle_shuffle_decks,
    start_raffle as start_raffle_service,
)
from backend.routes_debug import register_debug_routes
from backend.routes_ws import register_ws_routes
from backend.services.ws_state_service import (
    WSManager,
    deck_signature,
    global_signature,
)
from backend.services.scryfall_service import (
    get_card_by_id,
    is_partner_exact_name,
    named_exact,
    random_commander,
)
from backend.services.event_config_service import (
    detect_event_state,
    load_event_settings,
    settings_as_dict,
    settings_editability,
    apply_settings_patch,
    save_event_settings,
    SettingsUpdateError,
    reset_settings_with_locks,
)
from backend.config import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    COMMANDER_BG_ZOOM,
    DEFAULT_BG_QUERY,
    DEFAULT_BG_ZOOM,
    MAX_ROUNDS,
    PAIRINGS_FILE_PATH,
    PARTICIPANTS_FILE_PATH,
    RAFFLE_FILE_PATH,
    SCRYFALL_BASE,
    SCRYFALL_HEADERS,
    SCRYFALL_TIMEOUT,
    START_FILE_PATH,
    SUGGEST_LIMIT,
    SUGGEST_MIN_CHARS,
)
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from random import shuffle, randint, choice
import time 
import io
from collections import OrderedDict
from urllib.parse import quote_plus, unquote_plus
import httpx
#python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

_suggest_cache = OrderedDict()  # key -> (timestamp, result_list)

def _cache_get(key: str):
    now = time.time()
    item = _suggest_cache.get(key)
    if not item:
        return None
    ts, value = item
    if now - ts > CACHE_TTL_SECONDS:
        # expired
        _suggest_cache.pop(key, None)
        return None
    # LRU refresh
    _suggest_cache.move_to_end(key)
    return value

def _cache_set(key: str, value):
    now = time.time()
    _suggest_cache[key] = (now, value)
    _suggest_cache.move_to_end(key)
    # enforce size
    while len(_suggest_cache) > CACHE_MAX_ENTRIES:
        _suggest_cache.popitem(last=False)

def _get_image_url(card: dict, key: str) -> str | None:
    return get_image_url(card, key)


# JSON-Datei
FILE_PATH = RAFFLE_FILE_PATH
PAIRINGS_PATH = PAIRINGS_FILE_PATH

# Serialisiert alle Zugriffe auf raffle.json innerhalb dieses Prozesses
RAFFLE_LOCK = asyncio.Lock()

def _atomic_write_json(path: Path, data) -> None:
    """
    Schreibt JSON atomisch:
    - erst in temp-Datei schreiben
    - dann os.replace (atomarer rename) auf Ziel
    """
    atomic_write_json(path, data)

# FastAPI-App erstellen
app, templates = create_app()

# =========================================================
# WebSocket live updates (no polling)
# =========================================================

ws_manager = WSManager()

_last_global_sig: str | None = None
_last_deck_sig: dict[int, str] = {}

def _load_raffle_list() -> list[dict]:
    return load_raffle_list(FILE_PATH)

def _load_pairings() -> dict | None:
    return load_pairings(PAIRINGS_PATH)


def _atomic_write_pairings(data: dict) -> None:
    write_pairings(PAIRINGS_PATH, data)


def _all_received_confirmed(raffle_list: list[dict]) -> bool:
    # nur für registrierte Decks
    decks = [e for e in raffle_list if e.get("deck_id") is not None]
    if not decks:
        return False
    return all(e.get("received_confirmed") is True for e in decks)


def _deckowners(raffle_list: list[dict]) -> list[str]:
    # Spieler sind deckOwner (nach Raffle-Start gesetzt)
    players = []
    for e in raffle_list:
        o = (e.get("deckOwner") or "").strip()
        if o:
            players.append(o)
    # dedupe (sollte ohnehin eindeutig sein)
    return sorted(list(dict.fromkeys(players)))


def _pod_sizes(n_players: int, num_pods: int) -> list[int]:
    return pod_sizes(n_players, num_pods)


def _first_round_with_hosts(players: list[str], num_pods: int, hosts: list[str]) -> list[list[str]]:
    return first_round_with_hosts(players, num_pods, hosts)


def _build_rounds(players: list[str], num_pods: int, max_rounds: int = MAX_ROUNDS, fixed_first_round: list[list[str]] | None = None) -> list[list[list[str]]]:
    return build_rounds(players, num_pods, max_rounds=max_rounds, fixed_first_round=fixed_first_round)


def _apply_round_to_raffle(raffle_list: list[dict], state: dict, round_no: int):
    apply_round_to_raffle(raffle_list, state, round_no)


def _resolve_round_places(raw_places: dict[str, list[str]]) -> dict[str, int]:
    """
    Normalisiert Platzierungen mit Gleichständen.

    Beispiel (4 Spieler):
      Platz 4: [A]
      Platz 3: [B, C]
      -> Platz 2 wird übersprungen
      -> verbleibender Spieler kann nur noch Platz 1 sein
    """
    resolved: dict[str, int] = {}
    occupied = set()
    for place in sorted(raw_places.keys(), key=lambda x: int(x), reverse=True):
        players = [p for p in (raw_places.get(place) or []) if p]
        if not players:
            continue
        p = int(place)
        for player in players:
            if player in occupied:
                continue
            resolved[player] = p
            occupied.add(player)
    return resolved


def _pairings_reports_bucket(state: dict, round_no: int) -> dict:
    reports = state.setdefault("round_reports", {})
    round_key = str(round_no)
    return reports.setdefault(round_key, {})


def _round_tables(state: dict, round_no: int) -> list[list[str]]:
    rounds = (state.get("rounds") or []) if isinstance(state, dict) else []
    if round_no <= 0 or len(rounds) < round_no:
        return []
    return rounds[round_no - 1] or []


def _round_report_status(state: dict, round_no: int) -> dict:
    tables = _round_tables(state, round_no)
    table_count = len(tables)
    reports_for_round = ((state.get("round_reports") or {}).get(str(round_no), {})) if isinstance(state, dict) else {}
    reported_tables: list[int] = []
    for table_no in range(1, table_count + 1):
        if reports_for_round.get(str(table_no)):
            reported_tables.append(table_no)

    missing_tables = [table_no for table_no in range(1, table_count + 1) if table_no not in reported_tables]
    return {
        "table_count": table_count,
        "reported_count": len(reported_tables),
        "reported_tables": reported_tables,
        "missing_tables": missing_tables,
        "all_tables_reported": table_count > 0 and len(missing_tables) == 0,
    }


def _sync_round_completion_marker(state: dict, round_no: int) -> dict:
    status = _round_report_status(state, round_no)
    completion = state.get("round_completion") if isinstance(state.get("round_completion"), dict) else {}
    round_key = str(round_no)
    prev_completed_at = (completion.get(round_key) or {}).get("completed_at")

    completed_at = None
    if status["all_tables_reported"]:
        completed_at = prev_completed_at or datetime.now(timezone.utc).isoformat()

    completion[round_key] = {
        "table_count": status["table_count"],
        "reported_count": status["reported_count"],
        "missing_tables": status["missing_tables"],
        "all_tables_reported": status["all_tables_reported"],
        "completed_at": completed_at,
    }
    state["round_completion"] = completion
    return completion[round_key]

def _global_signature(start_file_exists: bool, raffle_list: list[dict]) -> str:
    return global_signature(
        start_file_exists,
        raffle_list,
        pairings_loader=_load_pairings,
        settings_loader=lambda: settings_as_dict(_current_settings()),
    )

def _deck_signature(deck_id: int, start_file_exists: bool, raffle_list: list[dict]) -> str:
    return deck_signature(
        deck_id,
        start_file_exists,
        raffle_list,
        pairings_loader=_load_pairings,
        settings_loader=lambda: settings_as_dict(_current_settings()),
    )


def _current_settings():
    settings, _meta = load_event_settings()
    return settings


def _current_event_state():
    raffle_list = _load_raffle_list()
    pairings = _load_pairings()
    state = detect_event_state(START_FILE_PATH.exists(), raffle_list, pairings)
    return state, raffle_list, pairings

async def notify_state_change():
    """
    Called after any write to raffle.json or start.txt.
    Sends WS events only to groups whose signature changed.
    """
    global _last_global_sig, _last_deck_sig

    start_file_exists = START_FILE_PATH.exists()
    raffle_list = _load_raffle_list()

    # global (CCP + home)
    gsig = _global_signature(start_file_exists, raffle_list)
    if _last_global_sig is None:
        _last_global_sig = gsig
        payload = {"type": "state_changed", "scope": "global", "signature": gsig}
        await ws_manager.broadcast_group("ccp", payload)
        await ws_manager.broadcast_group("home", payload)
    elif gsig != _last_global_sig:
        _last_global_sig = gsig
        payload = {"type": "state_changed", "scope": "global", "signature": gsig}
        await ws_manager.broadcast_group("ccp", payload)
        await ws_manager.broadcast_group("home", payload)

    # per connected deck_id
    for did in ws_manager.active_deck_ids():
        dsig = _deck_signature(did, start_file_exists, raffle_list)
        prev = _last_deck_sig.get(did)
        if prev is None:
            _last_deck_sig[did] = dsig
            payload = {"type": "state_changed", "scope": "deck", "deck_id": did, "signature": dsig}
            await ws_manager.broadcast_group(f"deck:{did}", payload)
            continue
        if dsig != prev:
            _last_deck_sig[did] = dsig
            payload = {"type": "state_changed", "scope": "deck", "deck_id": did, "signature": dsig}
            await ws_manager.broadcast_group(f"deck:{did}", payload)

@app.get("/", response_class=HTMLResponse)
async def get_form(
    request: Request,
    deck_id: int = 0,
    error: str | None = None,
    field_errors: str | None = None,
    deckersteller: str | None = None,
    commander: str | None = None,
    commander2: str | None = None,
    deckUrl: str | None = None,
):
    """
    Zeigt die Startseite mit dem Formular an und verarbeitet Bedingungen basierend auf deck_id, raffle.json und start.txt.
    """
    # Prüfen, ob teilnehmer.txt existiert und Namen laden
    participants = []
    settings = _current_settings()
    if settings.participants:
        participants = settings.participants
    else:
        participants_file = PARTICIPANTS_FILE_PATH
        if participants_file.exists():
            with participants_file.open("r", encoding="utf-8") as f:
                participants = [line.strip() for line in f.readlines() if line.strip()]  # Entferne leere Zeilen

    # Status von start.txt prüfen
    start_file_exists = START_FILE_PATH.exists()
    raffle_list = _load_raffle_list()
    all_confirmed = _all_received_confirmed(raffle_list) if start_file_exists else False
    pairings = _load_pairings() or {}
    pairings_phase = (pairings.get("phase") or "").strip().lower()
    active_round = int(pairings.get("active_round") or 0) if pairings else 0
    pairings_started = bool(pairings) and active_round > 0

        # Prüfen, ob raffle.json existiert und die deck_id enthalten ist
    existing_entry = None
    deckOwner = None
    pairing_player_meta: dict[str, dict[str, str]] = {}

    if FILE_PATH.exists():
        try:
            with FILE_PATH.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    for entry in content:
                        if entry.get("deck_id") == deck_id:
                            existing_entry = entry
                            deckOwner = entry.get("deckOwner")
                            break
        except (json.JSONDecodeError, ValueError):
            pass

    glasscard_title = "Deckregistrierung"
    if start_file_exists:
        glasscard_title = "Deckverteilung"

        entry_pairing_phase = (existing_entry.get("pairing_phase") or "").strip().lower() if existing_entry else ""
        entry_pairing_round = int(existing_entry.get("pairing_round") or 0) if existing_entry else 0

        if pairings_phase in {"pre_voting", "voting"} or entry_pairing_phase in {"pre_voting", "voting"}:
            glasscard_title = "Warten auf das Voting" if pairings_phase == "pre_voting" or entry_pairing_phase == "pre_voting" else "Best-Deck-Voting"
            if existing_entry and glasscard_title != "Warten auf das Voting":
                vote_key = str(deck_id)
                top3_vote = ((pairings.get("best_deck_votes") or {}) if isinstance(pairings, dict) else {}).get(vote_key)
                deckraten_vote = ((pairings.get("deck_creator_guess_votes") or {}) if isinstance(pairings, dict) else {}).get(vote_key)
                if top3_vote and not deckraten_vote:
                    glasscard_title = "Deckraten"
                elif top3_vote and deckraten_vote:
                    glasscard_title = "Warten auf Ergebnisse"
        elif pairings_phase == "playing" and active_round > 0:
            glasscard_title = f"Spielphase – Runde {active_round}"
        elif entry_pairing_round > 0:
            glasscard_title = f"Spielphase – Runde {entry_pairing_round}"
        elif all_confirmed and not pairings_started:
            glasscard_title = "Warten auf Pairings"

    if existing_entry and isinstance(existing_entry.get("pairing_players"), list):
        players = [str(player).strip() for player in (existing_entry.get("pairing_players") or []) if str(player).strip()]
        for player in players:
            owner_entry = next((e for e in raffle_list if (e.get("deckOwner") or "").strip() == player), None)
            commander_name = (owner_entry or {}).get("commander") or ""
            commander2_name = (owner_entry or {}).get("commander2") or ""
            pairing_player_meta[player] = {
                "commander": str(commander_name).strip(),
                "commander2": str(commander2_name).strip(),
            }

    # 🔴 NEU: Raffle gestartet, aber Deck ID nicht registriert
    if start_file_exists and deck_id != 0 and existing_entry is None:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "deck_id": deck_id,
                "start_file_exists": start_file_exists,
                "error": error or (
                    "Der Raffle wurde schon gestartet, "
                    "diese Deck ID wurde aber nicht registriert. "
                    "Bitte sprich mit dem Host."
                ),
                "participants": [],
                "values": None,
                "glasscard_title": glasscard_title,
            }
        )

    decoded_field_errors = None
    if field_errors:
        try:
            decoded_field_errors = json.loads(unquote_plus(field_errors))
            if not isinstance(decoded_field_errors, dict):
                decoded_field_errors = None
        except Exception:
            decoded_field_errors = None

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "deck_id": deck_id,
            "start_file_exists": start_file_exists,
            "existing_entry": existing_entry,
            "deckOwner": deckOwner,
            "participants": participants,
            "glasscard_title": glasscard_title,
            "pairing_player_meta": pairing_player_meta,

            # NEU: PRG-Fehler + Prefill aus Query-Parametern
            "error": error,
            "field_errors": decoded_field_errors,
            "values": {
                "deckersteller": deckersteller,
                "commander": commander,
                "commander_id": request.query_params.get("commander_id") or None,
                "commander2": commander2,
                "commander2_id": request.query_params.get("commander2_id") or None,
                "deckUrl": deckUrl,
            } if (deckersteller or commander or commander2 or deckUrl or request.query_params.get("commander_id") or request.query_params.get("commander2_id")) else None,
        }
    )

async def _scryfall_get_card_by_id(card_id: str) -> dict | None:
    return await get_card_by_id(card_id)

async def _scryfall_random_commander(exclude_card_ids: set[str] | None = None, max_tries: int = 25) -> dict | None:
    """
    Picks a random commander card from Scryfall.
    Returns the full card JSON or None.

    exclude_card_ids: avoids duplicates by Scryfall card id
    """
    return await random_commander(exclude_card_ids=exclude_card_ids, max_tries=max_tries, query_template=_current_settings().scryfall.random_commander_query)


async def _scryfall_random_commander_with_query(
    query_template: str,
    exclude_card_ids: set[str] | None = None,
    max_tries: int = 25,
) -> dict | None:
    return await random_commander(
        exclude_card_ids=exclude_card_ids,
        max_tries=max_tries,
        query_template=query_template,
    )

def _is_background(card: dict) -> bool:
    return is_background(card)


def _has_choose_a_background(card: dict) -> bool:
    return has_choose_a_background(card)


def _has_friends_forever(card: dict) -> bool:
    return has_friends_forever(card)


def _partner_with_target_name(card: dict) -> str | None:
    return partner_with_target_name(card)


async def _scryfall_is_partner_exact_name(name: str) -> bool:
    return await is_partner_exact_name(name, query_template=_current_settings().scryfall.partner_capable_query_template)


async def _validate_commander_combo(c1: dict, c2: dict | None) -> str | None:
    """
    Returns None if ok, else a German error message.
    """
    if c2 is None:
        if _is_background(c1):
            return "Ein Background kann nicht alleine Commander sein. Wähle zuerst eine Kreatur mit 'Choose a Background'."
        return None

    c1_bg = _is_background(c1)
    c2_bg = _is_background(c2)

    # Background pairing
    if c1_bg or c2_bg:
        if c1_bg and c2_bg:
            return "Zwei Backgrounds zusammen sind nicht erlaubt. Wähle eine Kreatur mit 'Choose a Background' + genau einen Background."
        non_bg = c2 if c1_bg else c1
        if not _has_choose_a_background(non_bg):
            return "Backgrounds funktionieren nur zusammen mit einem Commander, der 'Choose a Background' hat."
        return None

    # Partner with pairing (must match exactly)
    p1 = _partner_with_target_name(c1)
    p2 = _partner_with_target_name(c2)
    if p1 or p2:
        if not (p1 and p2):
            return "Diese Kombination ist nicht gültig: 'Partner with' funktioniert nur mit der jeweils angegebenen Partnerkarte."
        if p1.lower() != (c2.get("name") or "").lower() or p2.lower() != (c1.get("name") or "").lower():
            return "Diese Kombination ist nicht gültig: 'Partner with' erlaubt nur das spezifisch genannte Paar."
        return None

    # Friends forever (must be both)
    ff1 = _has_friends_forever(c1)
    ff2 = _has_friends_forever(c2)
    if ff1 != ff2:
        return "Diese Kombination ist nicht gültig: 'Friends forever' kann nur mit einer anderen 'Friends forever'-Karte kombiniert werden."
    if ff1 and ff2:
        return None

    # Generic partner-like (both must be is:partner)
    c1_partner = await _scryfall_is_partner_exact_name(c1.get("name") or "")
    c2_partner = await _scryfall_is_partner_exact_name(c2.get("name") or "")
    if not (c1_partner and c2_partner):
        return "Diese Kombination ist nicht Commander-legal: Beide Karten müssen kompatible Partner-Commander sein (oder 'Choose a Background' + Background)."

    return None


async def _debug_pick_legal_partner_combo(exclude_card_ids: set[str]) -> tuple[dict, dict] | None:
    partner_creature_query = "game:paper is:commander t:creature is:partner -t:background"

    for _ in range(16):
        commander1 = await _scryfall_random_commander_with_query(
            query_template=partner_creature_query,
            exclude_card_ids=exclude_card_ids,
            max_tries=35,
        )
        if not commander1:
            continue

        commander1_id = str(commander1.get("id") or "").strip()
        commander1_name = str(commander1.get("name") or "").strip()
        if not commander1_id or not commander1_name:
            continue

        if not await _scryfall_is_partner_exact_name(commander1_name):
            continue

        local_exclude = set(exclude_card_ids)
        local_exclude.add(commander1_id)

        for _ in range(40):
            commander2 = await _scryfall_random_commander_with_query(
                query_template=partner_creature_query,
                exclude_card_ids=local_exclude,
                max_tries=35,
            )
            if not commander2:
                continue

            commander2_id = str(commander2.get("id") or "").strip()
            commander2_name = str(commander2.get("name") or "").strip()
            if not commander2_id or not commander2_name:
                continue

            combo_err = await _validate_commander_combo(commander1, commander2)
            if combo_err is None:
                return commander1, commander2

    return None

@app.post("/submit", response_class=HTMLResponse)
async def submit_form(
    request: Request,
    deckersteller: str = Form(...),
    commander: str = Form(...),
    commander_id: str = Form(None),
    commander2: str = Form(None),
    commander2_id: str = Form(None),
    deckUrl: str = Form(None),
    deck_id: int = Form(...)
):
    """
    Verarbeitet das Formular, prüft die DeckID und den Deckersteller, und fügt neue Datensätze hinzu.
    """
    try:
        # Konvertiere leere Strings zu None
        deckUrl = deckUrl or None

        # commander can be string or (buggy) dict from frontend; normalize robustly
        if isinstance(commander, dict):
            commander = commander.get("name") or commander.get("value") or ""
        commander = str(commander or "").strip() or None

        # commander_id can be string or (buggy) dict from frontend; normalize robustly
        if isinstance(commander_id, dict):
            commander_id = commander_id.get("id") or commander_id.get("value") or ""
        commander_id = str(commander_id or "").strip() or None

        # commander2 can be string or (buggy) dict from frontend; normalize robustly
        if isinstance(commander2, dict):
            commander2 = commander2.get("name") or commander2.get("value") or ""
        commander2 = str(commander2 or "").strip() or None

        if isinstance(commander2_id, dict):
            commander2_id = commander2_id.get("id") or commander2_id.get("value") or ""
        commander2_id = str(commander2_id or "").strip() or None

        def _redirect_back(err_msg: str, field_errors_dict: dict | None = None):
            fe = ""
            if field_errors_dict:
                fe = f"&field_errors={quote_plus(json.dumps(field_errors_dict, ensure_ascii=False))}"

            params = (
                f"deck_id={deck_id}"
                f"&error={quote_plus(err_msg)}"
                f"&deckersteller={quote_plus(deckersteller or '')}"
                f"&commander={quote_plus(commander or '')}"
                f"&commander_id={quote_plus(commander_id or '')}"
                f"&commander2={quote_plus(commander2 or '')}"
                f"&commander2_id={quote_plus(commander2_id or '')}"
                f"&deckUrl={quote_plus(deckUrl or '')}"
                f"{fe}"
            )
            return RedirectResponse(url=f"/?{params}", status_code=303)

        # Optional: falls commander2 == commander, wegwerfen (ohne zusätzliche strip() Calls)
        if commander2 and commander and commander2.lower() == commander.lower():
            commander2 = None
            commander2_id = None

        print("DEBUG types:", type(commander), type(commander_id), type(commander2), type(commander2_id))

        # ---------------------------------------------------------
        # Field-Errors: Commander-Kombinationen (Partner/Background/...)
        # ---------------------------------------------------------
        field_errors = {}

        # Basis-Konsistenz: commander2 ohne commander ist nicht erlaubt
        if commander2 and not commander:
            field_errors["commander2"] = "Commander 2 ist nur möglich, wenn zuerst ein erster Commander ausgewählt wurde."

        # IDs sollten gesetzt sein, wenn die Namen aus der Suggest-Liste kommen
        # (sonst können wir nicht robust validieren)
        if commander and not commander_id:
            field_errors["commander"] = "Bitte Commander 1 aus der Vorschlagsliste auswählen (ID fehlt)."

        if commander2 and not commander2_id:
            field_errors["commander2"] = "Bitte Commander 2 aus der Vorschlagsliste auswählen (ID fehlt)."

        # Wenn wir valide IDs haben: Scryfall laden und Kombi-Regeln prüfen
        if commander_id and (not commander2 or commander2_id):
            c1 = await _scryfall_get_card_by_id(commander_id)
            if not c1:
                field_errors["commander"] = "Commander 1 konnte bei Scryfall nicht geladen werden. Bitte erneut auswählen."

            c2 = None
            if commander2_id:
                c2 = await _scryfall_get_card_by_id(commander2_id)
                if not c2:
                    field_errors["commander2"] = "Commander 2 konnte bei Scryfall nicht geladen werden. Bitte erneut auswählen."

            # Nur wenn beide (falls benötigt) geladen wurden, Kombi prüfen
            if c1 and (c2 is not None or not commander2_id):
                combo_err = await _validate_commander_combo(c1, c2)
                if combo_err:
                    # In der Praxis ist es fast immer commander2, aber zur Sicherheit:
                    field_errors["commander2"] = combo_err

        if field_errors:
            return _redirect_back("Bitte korrigiere die markierten Felder.", field_errors)

        # Read-Check-Write muss atomar/serialisiert sein ---
        async with RAFFLE_LOCK:
            # Neu laden (wichtig gegen Race Conditions)
            data_list = []
            if FILE_PATH.exists():
                try:
                    with FILE_PATH.open("r", encoding="utf-8") as f:
                        content = json.load(f)
                        if isinstance(content, list):
                            data_list = content
                        else:
                            data_list = [content]
                except (json.JSONDecodeError, ValueError):
                    data_list = []

            # Duplikatchecks müssen innerhalb des Locks passieren
            for entry in data_list:
                if entry.get("deckersteller") == deckersteller:
                    return _redirect_back(
                        f"'{deckersteller}' hat bereits ein Deck registriert. Bitte überprüfe deine Namens auswahl"
                    )

            for entry in data_list:
                if entry.get("deck_id") == deck_id:
                    return _redirect_back("Diese Deck ID ist bereits registriert.")

            # Neuen Datensatz hinzufügen
            new_entry = DeckSchema(
                deckersteller=deckersteller,
                commander=commander,
                commander_id=commander_id,
                commander2=commander2,
                commander2_id=commander2_id,
                deckUrl=deckUrl
            )
            serializable_data = new_entry.dict()
            serializable_data["deckUrl"] = str(serializable_data["deckUrl"]) if serializable_data["deckUrl"] else None
            serializable_data["deck_id"] = deck_id
            serializable_data["deckOwner"] = None
            serializable_data["received_confirmed"] = False

            data_list.append(serializable_data)

            # Atomisch schreiben
            _atomic_write_json(FILE_PATH, data_list)

        await notify_state_change()

        # Erfolgsseite anzeigen
        return RedirectResponse(url="/success", status_code=303)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern der Daten: {e}")

@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    """
    Erfolgsseite nach dem Absenden des Formulars.
    """
    return templates.TemplateResponse("success.html", {"request": request})

async def _clear_event_data_in_memory() -> None:
    # Löschen von raffle.json, falls sie existiert
    if FILE_PATH.exists():
        FILE_PATH.unlink()
    # Löschen von start.txt, falls sie existiert
    if START_FILE_PATH.exists():
        START_FILE_PATH.unlink()
    # Löschen von pairings.json, falls sie existiert
    if PAIRINGS_PATH.exists():
        PAIRINGS_PATH.unlink()
    # Erstellen einer leeren raffle.json
    with FILE_PATH.open("w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=4)


@app.post("/clear")
async def clear_data():
    """
    Löscht die Dateien raffle.json und start.txt, falls vorhanden, und erstellt eine leere raffle.json.
    Leitet den Benutzer anschließend zurück zum CCP.
    """
    try:
        async with RAFFLE_LOCK:
            await _clear_event_data_in_memory()

        await notify_state_change()

        # Weiterleitung zurück zum Customer Control Panel
        return RedirectResponse(url="/CCP", status_code=303)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Löschen der Dateien: {e}")

def _debug_has_minimal_registrations(raffle_list: list[dict], min_decks: int) -> bool:
    """
    True if we have at least min_decks entries that look like a valid registration
    (start.txt not yet present).
    """
    decks = [e for e in raffle_list if e.get("deck_id") is not None]
    if len(decks) < min_decks:
        return False
    for e in decks:
        if not (e.get("deckersteller") and e.get("commander")):
            return False
    return True


def _debug_pick_num_pods(n_players: int) -> int:
    # Simple heuristic: pods of up to 4 players.
    return max(1, (n_players + 3) // 4)


def _debug_detect_phase(start_file_exists: bool, raffle_list: list[dict], pair_state: dict | None) -> str:
    """
    Next-step state machine for /debug.
    Each call advances exactly ONE step.

    Current phases:
      - registration_needed
      - raffle_start_needed
      - confirm_needed
      - pairings_start_needed
      - next_round_or_end_needed
      - start_voting_needed
      - voting_needed
      - idle
    """
    if not start_file_exists:
        # before raffle: either fill test registrations or start raffle if possible
        if _debug_has_minimal_registrations(raffle_list, min_decks=_current_settings().min_decks_to_start):
            return "raffle_start_needed"
        return "registration_needed"

    # raffle already started
    if not _all_received_confirmed(raffle_list):
        return "confirm_needed"

    # all confirmed -> pairings/rounds
    if not pair_state:
        return "pairings_start_needed"

    phase = (pair_state.get("phase") or "").strip().lower()
    if phase == "playing":
        return "next_round_or_end_needed"
    if phase == "pre_voting":
        return "start_voting_needed"
    if phase == "voting":
        return "voting_needed"

    return "idle"


_DEBUG_TERMINAL_STEP = 9


def _debug_current_step_index(start_file_exists: bool, raffle_list: list[dict], pair_state: dict | None) -> int:
    """
    Returns the current sequential debug step index.

    Step model:
      0 = event reset / before registration helper run
      1 = registrations prepared
      2 = raffle started
      3 = all decks confirmed
      4 = pairings started (round 1)
      5 = round 2 started
      6 = round 3 started
      7 = round 4 started
      8 = voting phase reached
      9 = voting results published
    """
    if not start_file_exists:
        return 1 if _debug_has_minimal_registrations(raffle_list, min_decks=_current_settings().min_decks_to_start) else 0

    if not _all_received_confirmed(raffle_list):
        return 2

    if not pair_state:
        return 3

    phase = (pair_state.get("phase") or "").strip().lower()
    active_round = int(pair_state.get("active_round") or 0)

    if phase == "playing":
        if active_round <= 1:
            return 4
        if active_round == 2:
            return 5
        if active_round == 3:
            return 6
        if active_round == 4:
            return 7
        return 8

    if phase == "voting":
        return 9 if _published_voting_results(pair_state) else 8

    return 9 if _published_voting_results(pair_state) else 8


def _debug_read_current_step_index() -> int:
    return _debug_current_step_index(
        START_FILE_PATH.exists(),
        _load_raffle_list(),
        _load_pairings(),
    )


def _debug_complete_voting_and_publish_in_memory(raffle_list: list[dict], state: dict) -> dict:
    """
    Completes missing voting steps for all players and publishes results.

    Behavior:
      - Fill missing Top-3 votes with random valid deck choices.
      - Fill missing deck-creator guess mappings with a random full assignment.
      - Publish voting results once all players have both votes.

    Assumes RAFFLE_LOCK is held by caller and state.phase == "voting".
    """
    if (state.get("phase") or "").strip().lower() != "voting":
        return {"ok": True, "action": "noop", "message": "Voting-Phase ist nicht aktiv."}

    if _published_voting_results(state):
        return {
            "ok": True,
            "action": "noop",
            "message": "Voting-Ergebnisse wurden bereits veröffentlicht.",
            "phase": "voting",
        }

    owners = sorted({(e.get("deckOwner") or "").strip() for e in raffle_list if (e.get("deckOwner") or "").strip()})
    top3_votes = _best_deck_votes_bucket(state)
    deckraten_votes = _deck_creator_guess_votes_bucket(state)

    top3_filled_for: list[int] = []
    deckraten_filled_for: list[int] = []

    for owner in owners:
        owner_entry = next((e for e in raffle_list if (e.get("deckOwner") or "").strip() == owner), None)
        owner_deck_id = int((owner_entry or {}).get("deck_id") or 0)
        if owner_deck_id <= 0:
            continue

        key = str(owner_deck_id)
        candidates = _best_deck_candidates_for_owner(raffle_list, owner)
        candidate_ids = [int(item.get("deck_id") or 0) for item in candidates if int(item.get("deck_id") or 0) > 0]

        if not top3_votes.get(key) and len(candidate_ids) >= 3:
            shuffled_ids = list(candidate_ids)
            shuffle(shuffled_ids)
            top3_votes[key] = {
                "1": shuffled_ids[0],
                "2": shuffled_ids[1],
                "3": shuffled_ids[2],
                "voted_by": owner,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
            top3_filled_for.append(owner_deck_id)

        if not deckraten_votes.get(key) and candidate_ids:
            creator_ids = [str(item.get("deckersteller") or "").strip() for item in candidates]
            creator_ids = [creator for creator in creator_ids if creator]
            assigned_ids = list(candidate_ids)
            shuffle(assigned_ids)
            mapping = {creator: deck_id for creator, deck_id in zip(creator_ids, assigned_ids)}
            deckraten_votes[key] = {
                **mapping,
                "voted_by": owner,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
            deckraten_filled_for.append(owner_deck_id)

    pending_voters: list[int] = []
    for owner in owners:
        owner_entry = next((e for e in raffle_list if (e.get("deckOwner") or "").strip() == owner), None)
        owner_deck_id = int((owner_entry or {}).get("deck_id") or 0)
        if owner_deck_id <= 0:
            continue
        key = str(owner_deck_id)
        if not top3_votes.get(key) or not deckraten_votes.get(key):
            pending_voters.append(owner_deck_id)

    if pending_voters:
        _atomic_write_pairings(state)
        return {
            "ok": True,
            "action": "filled_missing_voting_participants",
            "phase": "voting",
            "top3_filled_for": sorted(top3_filled_for),
            "deckraten_filled_for": sorted(deckraten_filled_for),
            "pending_voters": sorted(pending_voters),
            "published": False,
        }

    results = _calculate_voting_results(raffle_list, state)
    bucket = _votes_results_bucket(state)
    bucket["published"] = True
    bucket["published_at"] = datetime.now(timezone.utc).isoformat()
    bucket["data"] = results
    _atomic_write_pairings(state)

    return {
        "ok": True,
        "action": "completed_voting_and_published_results",
        "phase": "voting",
        "top3_filled_for": sorted(top3_filled_for),
        "deckraten_filled_for": sorted(deckraten_filled_for),
        "pending_voters": [],
        "published": True,
    }


def _debug_start_raffle_in_memory(raffle_list: list[dict]) -> dict:
    """
    Equivalent of /startRaffle (but returns JSON result).
    Assumes RAFFLE_LOCK is held by caller.
    """
    # create start.txt
    start_file = START_FILE_PATH
    with start_file.open("w", encoding="utf-8") as f:
        f.write("")  # empty file

    try:
        assigned_count = assign_deck_owners(raffle_list)
    except RaffleStartError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _atomic_write_json(FILE_PATH, raffle_list)

    return {
        "ok": True,
        "action": "raffle_started",
        "assigned_count": assigned_count,
    }


def _debug_start_pairings_in_memory(raffle_list: list[dict]) -> dict:
    """
    Equivalent of /startPairings, but chooses num_pods automatically.
    Assumes RAFFLE_LOCK is held by caller.
    """
    if not START_FILE_PATH.exists():
        raise HTTPException(status_code=400, detail="Raffle noch nicht gestartet.")
    if not _all_received_confirmed(raffle_list):
        raise HTTPException(status_code=400, detail="Nicht alle Decks wurden bestätigt.")

    players = _deckowners(raffle_list)
    if len(players) < 3:
        raise HTTPException(status_code=400, detail="Zu wenige Spieler.")

    num_pods = _debug_pick_num_pods(len(players))
    rounds = _build_rounds(players, int(num_pods), _current_settings().max_rounds)

    state = {
        "pods": int(num_pods),
        "players": players,
        "rounds": rounds,
        "active_round": 1,
        "phase": "playing",
    }
    _atomic_write_pairings(state)

    # Runde 1 in raffle.json eintragen
    _apply_round_to_raffle(raffle_list, state, round_no=1)
    _atomic_write_json(FILE_PATH, raffle_list)

    return {
        "ok": True,
        "action": "pairings_started",
        "pods": int(num_pods),
        "active_round": 1,
        "phase": "playing",
    }


def _debug_next_round_or_end_in_memory(raffle_list: list[dict], state: dict) -> dict:
    """
    Advances rounds in 'playing' phase.

    Requested special behavior:
      - When Round 4 is completed (i.e. active_round == 4 and /debug is called),
        /debug starts Round 5 and ENDS the rounds/play phase in the SAME step (-> voting).

    Assumes RAFFLE_LOCK is held by caller.
    """
    if (state.get("phase") or "").strip().lower() != "playing":
        return {"ok": True, "action": "noop", "message": "Spielphase ist nicht aktiv."}

    rounds = state.get("rounds") or []
    active = int(state.get("active_round") or 1)

    # If no configured rounds (shouldn't happen), end to voting.
    if not rounds:
        active_round = int(state.get("active_round") or 0)
        if active_round > 0:
            _sync_round_completion_marker(state, active_round)

        state["phase"] = "voting"
        _atomic_write_pairings(state)
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"
        _atomic_write_json(FILE_PATH, raffle_list)
        return {"ok": True, "action": "ended_play_phase", "active_round": active, "phase": "voting"}

    # If already beyond last configured round -> end.
    if active >= len(rounds):
        active_round = int(state.get("active_round") or 0)
        if active_round > 0:
            _sync_round_completion_marker(state, active_round)

        state["phase"] = "voting"
        _atomic_write_pairings(state)
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"
        _atomic_write_json(FILE_PATH, raffle_list)
        return {"ok": True, "action": "ended_play_phase", "active_round": active, "phase": "voting"}

    # Normal round progression for rounds 1..3
    if active < 4:
        _sync_round_completion_marker(state, active)
        active += 1
        state["active_round"] = active
        _atomic_write_pairings(state)
        _apply_round_to_raffle(raffle_list, state, round_no=active)
        _atomic_write_json(FILE_PATH, raffle_list)
        return {"ok": True, "action": "started_next_round", "active_round": active, "phase": "playing"}

    # Special: active == 4 -> start round 5 (if available) AND end play phase (-> voting)
    if active == 4:
        next_round = 5
        if next_round <= len(rounds):
            state["active_round"] = next_round
            _apply_round_to_raffle(raffle_list, state, round_no=next_round)
        else:
            # If we don't have a round 5 configured, keep round 4 as active.
            next_round = active

        active_round = int(state.get("active_round") or 0)
        if active_round > 0:
            _sync_round_completion_marker(state, active_round)

        state["phase"] = "voting"
        _atomic_write_pairings(state)

        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"

        _atomic_write_json(FILE_PATH, raffle_list)

        return {
            "ok": True,
            "action": "started_round_5_and_ended_play_phase",
            "active_round": next_round,
            "phase": "voting",
        }

    # Fallback: end play phase
    state["phase"] = "voting"
    _atomic_write_pairings(state)
    for e in raffle_list:
        if e.get("deck_id") is not None:
            e["pairing_phase"] = "voting"
    _atomic_write_json(FILE_PATH, raffle_list)
    return {"ok": True, "action": "ended_play_phase", "active_round": active, "phase": "voting"}


def _debug_report_missing_round_results_in_memory(state: dict) -> dict:
    """
    Meldet für die aktive Runde fehlende Tisch-Ergebnisse automatisch.

    Für jeden fehlenden Tischreport werden die Tisch-Spieler zufällig verteilt und
    als Einzelplatzierungen (1-4) gespeichert.

    Assumes RAFFLE_LOCK is held by caller.
    """
    if (state.get("phase") or "").strip().lower() != "playing":
        return {"ok": True, "action": "noop", "message": "Spielphase ist nicht aktiv."}

    rounds = state.get("rounds") or []
    active_round = int(state.get("active_round") or 0)
    if active_round <= 0 or active_round > len(rounds):
        return {"ok": True, "action": "noop", "message": "Keine gültige aktive Runde gefunden."}

    tables = rounds[active_round - 1] or []
    reports_for_round = _pairings_reports_bucket(state, active_round)

    created_reports: list[dict] = []
    for idx, table_players in enumerate(tables, start=1):
        table_key = str(idx)
        if reports_for_round.get(table_key):
            continue

        shuffled_players = [p for p in (table_players or []) if p]
        shuffle(shuffled_players)

        raw_placements = {"1": [], "2": [], "3": [], "4": []}
        for place, player in zip(["1", "2", "3", "4"], shuffled_players):
            raw_placements[place] = [player]

        reports_for_round[table_key] = {
            "round": active_round,
            "table": idx,
            "players": table_players,
            "raw_placements": raw_placements,
            "resolved_places": _resolve_round_places(raw_placements),
            "reported_by": "Debug-Endfreund",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        created_reports.append({"table": idx, "players": table_players})

    if created_reports:
        _atomic_write_pairings(state)
        return {
            "ok": True,
            "action": "reported_missing_round_results",
            "active_round": active_round,
            "reported_count": len(created_reports),
            "reported_tables": created_reports,
            "phase": "playing",
        }

    return {
        "ok": True,
        "action": "noop",
        "active_round": active_round,
        "message": "Alle Tische der aktiven Runde haben bereits Ergebnisse.",
        "phase": "playing",
    }


async def _debug_apply_step() -> dict:
    """
    Executes exactly one reasonable next step depending on current event state.
    Returns a result dict for JSON/HTML output.
    """
    start_file_exists = START_FILE_PATH.exists()

    async with RAFFLE_LOCK:
        raffle_list = _load_raffle_list()
        pair_state = _load_pairings()
        phase = _debug_detect_phase(start_file_exists, raffle_list, pair_state)

        # -------------------------
        # Phase 1: Registration
        # -------------------------
        if phase == "registration_needed":
            participants_file = PARTICIPANTS_FILE_PATH
            if not participants_file.exists():
                raise HTTPException(status_code=400, detail="teilnehmer.txt nicht gefunden.")

            with participants_file.open("r", encoding="utf-8") as f:
                names = [line.strip() for line in f.readlines() if line.strip()]

            if len(names) < 8:
                raise HTTPException(
                    status_code=400,
                    detail=f"teilnehmer.txt enthält nur {len(names)} Namen, benötigt werden mindestens 8."
                )

            shuffle(names)
            selected_names = names[:8]
            deck_ids = list(range(1, 9))

            partner_deck_ids = list(deck_ids)
            shuffle(partner_deck_ids)
            partner_deck_ids = set(partner_deck_ids[: len(deck_ids) // 2])

            created_entries: list[dict] = []
            seen_card_ids: set[str] = set()

            for deck_id, deckersteller in zip(deck_ids, selected_names):
                commander_name = None
                commander_id = None
                commander2_name = None
                commander2_id = None

                if deck_id in partner_deck_ids:
                    combo = await _debug_pick_legal_partner_combo(seen_card_ids)
                    if not combo:
                        raise HTTPException(status_code=502, detail="Konnte keine gültige Partner-Kombo von Scryfall laden.")

                    card1, card2 = combo
                    commander_name = (card1.get("name") or "").strip()
                    commander_id = (card1.get("id") or "").strip()
                    commander2_name = (card2.get("name") or "").strip()
                    commander2_id = (card2.get("id") or "").strip()

                    if not commander_name or not commander_id or not commander2_name or not commander2_id:
                        raise HTTPException(status_code=502, detail="Ungültige Scryfall-Antwort für Partner-Kombo (name/id fehlt).")

                    seen_card_ids.add(commander_id)
                    seen_card_ids.add(commander2_id)
                else:
                    card = await _scryfall_random_commander(exclude_card_ids=seen_card_ids)
                    if not card:
                        raise HTTPException(status_code=502, detail="Konnte keinen zufälligen Commander von Scryfall laden.")

                    commander_name = (card.get("name") or "").strip()
                    commander_id = (card.get("id") or "").strip()
                    if not commander_name or not commander_id:
                        raise HTTPException(status_code=502, detail="Ungültige Scryfall-Antwort (name/id fehlt).")

                    seen_card_ids.add(commander_id)

                created_entries.append({
                    "deckersteller": deckersteller,
                    "commander": commander_name,
                    "commander_id": commander_id,
                    "commander2": commander2_name,
                    "commander2_id": commander2_id,
                    "deckUrl": None,
                    "deck_id": deck_id,
                    "deckOwner": None,
                    "received_confirmed": False
                })

            # überschreibt nur deck_ids 1..8
            raffle_list = [e for e in raffle_list if e.get("deck_id") not in deck_ids]
            raffle_list.extend(created_entries)
            _atomic_write_json(FILE_PATH, raffle_list)

            return {
                "ok": True,
                "phase": phase,
                "action": "filled_decks_1_to_8",
                "filled_deck_ids": deck_ids,
                "created_count": len(created_entries),
                "created": created_entries,
            }

        # -------------------------
        # Phase 1b: Start raffle
        # -------------------------
        if phase == "raffle_start_needed":
            result = _debug_start_raffle_in_memory(raffle_list)
            result["phase"] = phase
            return result

        # -------------------------
        # Phase 2: Confirm received
        # -------------------------
        if phase == "confirm_needed":
            updated_ids: list[int] = []
            for e in raffle_list:
                did = e.get("deck_id")
                if did is None:
                    continue
                if e.get("received_confirmed") is True:
                    continue
                e["received_confirmed"] = True
                updated_ids.append(int(did))

            if updated_ids:
                _atomic_write_json(FILE_PATH, raffle_list)

            return {
                "ok": True,
                "phase": phase,
                "action": "confirmed_all_pending",
                "updated_deck_ids": sorted(updated_ids),
                "updated_count": len(updated_ids),
            }

        # -------------------------
        # Phase 3: Start pairings / round 1
        # -------------------------
        if phase == "pairings_start_needed":
            result = _debug_start_pairings_in_memory(raffle_list)
            result["phase"] = phase
            return result

        # -------------------------
        # Phase 4: Advance rounds / end play phase after round 4
        # -------------------------
        if phase == "next_round_or_end_needed":
            st = _load_pairings()
            if not st:
                return {"ok": True, "phase": phase, "action": "noop", "message": "Pairings-State fehlt."}
            report_result = _debug_report_missing_round_results_in_memory(st)
            if report_result.get("action") == "reported_missing_round_results":
                report_result["phase"] = phase
                return report_result
            result = _debug_next_round_or_end_in_memory(raffle_list, st)
            result["phase"] = phase
            return result

        # -------------------------
        # Phase 5: Start voting from pre-voting overview
        # -------------------------
        if phase == "start_voting_needed":
            st = _load_pairings() or {}
            st["phase"] = "voting"
            _atomic_write_pairings(st)

            for e in raffle_list:
                if e.get("deck_id") is not None:
                    e["pairing_phase"] = "voting"
            _atomic_write_json(FILE_PATH, raffle_list)

            return {
                "ok": True,
                "phase": phase,
                "action": "started_voting_phase",
            }

        # -------------------------
        # Phase 5: Complete voting + publish results
        # -------------------------
        if phase == "voting_needed":
            st = _load_pairings() or {}
            result = _debug_complete_voting_and_publish_in_memory(raffle_list, st)
            result["phase"] = phase
            return result

        # -------------------------
        # Idle (nothing to do yet)
        # -------------------------
        return {
            "ok": True,
            "phase": phase,
            "action": "noop",
            "message": "Kein weiterer Debug-Schritt definiert (aktuell: Registrierung → Raffle-Start → Bestätigen → Pairings/Runden → Voting → Ergebnisveröffentlichung).",
        }


async def _debug_apply_step_with_skip(skip_to: int | None = None) -> dict:
    if skip_to is None:
        result = await _debug_apply_step()
        result["current_step"] = _debug_read_current_step_index()
        return result

    if skip_to not in (-1, 0) and skip_to <= 0:
        raise HTTPException(status_code=400, detail="skip_to muss > 0 sein oder die Sonderwerte 0/-1 verwenden.")

    if skip_to == 0:
        async with RAFFLE_LOCK:
            await _clear_event_data_in_memory()
        return {
            "ok": True,
            "action": "reset_to_start",
            "skip_to": 0,
            "current_step": _debug_read_current_step_index(),
            "phase": "registration_needed",
            "message": "Event wurde zurückgesetzt (entspricht /clear).",
        }

    target = _DEBUG_TERMINAL_STEP if skip_to == -1 else int(skip_to)
    if target > _DEBUG_TERMINAL_STEP:
        raise HTTPException(status_code=400, detail=f"skip_to darf maximal {_DEBUG_TERMINAL_STEP} sein (oder -1).")

    current_step = _debug_read_current_step_index()
    if current_step >= target:
        return {
            "ok": True,
            "action": "skip_to_noop",
            "skip_to": skip_to,
            "current_step": current_step,
            "phase": _debug_detect_phase(START_FILE_PATH.exists(), _load_raffle_list(), _load_pairings()),
            "message": "skip_to erlaubt nur Vorwärtssprünge. Zielschritt ist bereits erreicht oder überschritten.",
        }

    last_result: dict = {
        "ok": True,
        "action": "skip_to_started",
        "phase": _debug_detect_phase(START_FILE_PATH.exists(), _load_raffle_list(), _load_pairings()),
    }

    max_iterations = 64
    for _ in range(max_iterations):
        before = _debug_read_current_step_index()
        if before >= target:
            break

        last_result = await _debug_apply_step()
        after = _debug_read_current_step_index()

        if after >= target:
            break

        # safety break to avoid endless loops if a noop/non-progress state is reached
        if after <= before and last_result.get("action") == "noop":
            break

    final_step = _debug_read_current_step_index()
    phase = _debug_detect_phase(START_FILE_PATH.exists(), _load_raffle_list(), _load_pairings())
    if final_step >= target:
        return {
            **last_result,
            "ok": True,
            "action": "skip_to_reached",
            "skip_to": skip_to,
            "target_step": target,
            "current_step": final_step,
            "phase": phase,
        }

    return {
        **last_result,
        "ok": True,
        "action": "skip_to_stopped",
        "skip_to": skip_to,
        "target_step": target,
        "current_step": final_step,
        "phase": phase,
        "message": "skip_to konnte nicht vollständig ausgeführt werden (kein weiterer Fortschritt möglich).",
    }


register_debug_routes(app, _debug_apply_step_with_skip, notify_state_change)

@app.get("/CCP", response_class=HTMLResponse)
async def customer_control_panel(request: Request):
    """
    Zeigt die Customer Control Panel Seite an, überprüft den Status von start.txt und raffle.json.
    """
    start_file_exists = START_FILE_PATH.exists()
    settings = _current_settings()

    deck_count = -1
    deckersteller: list[str] = []
    tooltip_items: list[dict] = []
    confirmed_count = 0
    total_decks = 0
    voting_done_count = 0
    voting_total_count = 0

    raffle_list = _load_raffle_list()
    all_confirmed = _all_received_confirmed(raffle_list) if start_file_exists else False
    pair = _load_pairings() or {}
    pairings_phase = pair.get("phase") or ("ready" if all_confirmed else None)
    active_round = int(pair.get("active_round") or 0) if pair else 0
    pairings_started = bool(pair) and active_round > 0

    published_results = _published_voting_results(pair) if isinstance(pair, dict) else None
    voting_results_published = bool(published_results)
    voting_results_rows = (published_results or {}).get("rows") or []

    active_round_status = _round_report_status(pair, active_round) if pairings_phase == "playing" and active_round > 0 else {
        "table_count": 0,
        "reported_count": 0,
        "reported_tables": [],
        "missing_tables": [],
        "all_tables_reported": False,
    }
    completion_map = (pair.get("round_completion") or {}) if isinstance(pair, dict) else {}
    completion_entry = completion_map.get(str(active_round)) or {}
    active_round_reports_persisted = bool(
        active_round_status.get("all_tables_reported") and completion_entry.get("completed_at")
    )

    players = _deckowners(raffle_list) if start_file_exists else []
    selected_hosts = (pair.get("hosts") or []) if isinstance(pair, dict) else []

    round_tables = []
    if pairings_phase == "playing" and active_round > 0:
        rounds = pair.get("rounds") or []
        if len(rounds) >= active_round:
            active_tables = rounds[active_round - 1] or []
            reports = ((pair.get("round_reports") or {}).get(str(active_round), {}))
            for idx, table_players in enumerate(active_tables, start=1):
                report = reports.get(str(idx)) or {}
                raw_places = report.get("raw_placements") or {}
                placement_items: list[dict] = []
                for place in ["1", "2", "3", "4"]:
                    for player in (raw_places.get(place) or []):
                        placement_items.append({"place": int(place), "player": player})

                placement_summary = ""
                if placement_items:
                    placement_summary = " · ".join([f"{it['place']}. {it['player']}" for it in placement_items])

                round_tables.append({
                    "table": idx,
                    "players": table_players,
                    "has_report": bool(report),
                    "placement_items": placement_items,
                    "placement_summary": placement_summary,
                })

    if FILE_PATH.exists():
        try:
            with FILE_PATH.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    if start_file_exists:
                        total_decks = len([e for e in content if e.get("deck_id") is not None])
                        deck_count = total_decks
                        confirmed_count = sum(1 for e in content if e.get("received_confirmed") is True)

                        if pairings_phase == "voting":
                            top3_votes = (pair.get("best_deck_votes") or {}) if isinstance(pair, dict) else {}
                            deckraten_votes = (pair.get("deck_creator_guess_votes") or {}) if isinstance(pair, dict) else {}
                            owners = sorted({(e.get("deckOwner") or "").strip() for e in content if (e.get("deckOwner") or "").strip()})
                            voting_total_count = len(owners)
                            done_by_owner = {}
                            for owner in owners:
                                owner_entry = next((e for e in content if (e.get("deckOwner") or "").strip() == owner), None)
                                owner_deck_id = int((owner_entry or {}).get("deck_id") or 0)
                                key = str(owner_deck_id)
                                done_by_owner[owner] = bool(top3_votes.get(key)) and bool(deckraten_votes.get(key))
                            voting_done_count = sum(1 for v in done_by_owner.values() if v)
                            tooltip_items = [
                                {"name": owner, "received_confirmed": bool(done_by_owner.get(owner))}
                                for owner in owners
                            ]
                        else:
                            tooltip_items = sorted(
                                [
                                    {
                                        "name": (e.get("deckOwner") or "(noch kein Owner)"),
                                        "received_confirmed": bool(e.get("received_confirmed")),
                                    }
                                    for e in content
                                    if e.get("deck_id") is not None
                                ],
                                key=lambda x: x["name"].lower()
                            )
                    else:
                        deckersteller = sorted({
                            entry.get("deckersteller")
                            for entry in content
                            if entry.get("deckersteller")
                        })
                        deck_count = len(deckersteller)
                        tooltip_items = [{"name": n, "received_confirmed": False} for n in deckersteller]
                        confirmed_count = 0
        except (json.JSONDecodeError, ValueError):
            pass

    phase_name = "Deckregistrierung"
    if start_file_exists and not all_confirmed:
        phase_name = "Deckverteilung"
    elif start_file_exists and all_confirmed and not pairings_started:
        phase_name = "Warten auf Pairings"
    elif pairings_phase == "playing" and active_round > 0:
        phase_name = f"Spielrunde {active_round}"
    elif pairings_phase == "pre_voting":
        phase_name = "Warten auf Voting"
    elif pairings_phase == "voting" and not voting_results_published:
        phase_name = "Voting läuft"
    elif voting_results_published:
        phase_name = "Event abgeschlossen"

    if not start_file_exists:
        phase_status_text = "Deckregistrierung läuft."
    elif not all_confirmed:
        phase_status_text = f"{confirmed_count} von {deck_count if deck_count >= 0 else 0} Decks wurden bestätigt verteilt."
    elif not pairings_started:
        phase_status_text = "Alle Decks bestätigt. Pairings können gestartet werden."
    elif pairings_phase == "playing":
        if active_round_status.get("all_tables_reported"):
            phase_status_text = f"Runde {active_round}: alle Tische haben gemeldet."
        else:
            missing = ", ".join([f"Tisch {t}" for t in active_round_status.get("missing_tables") or []])
            phase_status_text = f"Runde {active_round}: es fehlen Meldungen ({missing or 'n/a'})."
    elif pairings_phase == "pre_voting":
        phase_status_text = "Vorabauswertung ist sichtbar. Voting kann manuell gestartet werden."
    elif pairings_phase == "voting" and not voting_results_published:
        if voting_total_count > 0 and voting_done_count == voting_total_count:
            phase_status_text = "Alle Teilnehmer haben gevotet."
        else:
            phase_status_text = f"{voting_done_count} von {voting_total_count} Teilnehmern haben gevotet."
    else:
        phase_status_text = "Overall-Ergebnisse sind veröffentlicht."

    primary_action = {
        "label": "Raffle starten",
        "action": "/startRaffle",
        "kind": "start_raffle",
        "disabled": bool(deck_count < settings.min_decks_to_start or start_file_exists),
    }
    if start_file_exists:
        if not all_confirmed:
            primary_action = {
                "label": "Pairings starten",
                "action": "/startPairings",
                "kind": "start_pairings",
                "disabled": True,
            }
        elif not pairings_started:
            primary_action = {
                "label": "Pairings starten",
                "action": "/startPairings",
                "kind": "start_pairings",
                "disabled": False,
            }
        elif pairings_phase == "playing":
            primary_action = {
                "label": "Nächste Runde starten",
                "action": "/nextRound",
                "kind": "next_round",
                "disabled": bool(not active_round_status.get("all_tables_reported")),
            }
        elif pairings_phase == "pre_voting":
            primary_action = {
                "label": "Votings starten",
                "action": "/startVotingPhase",
                "kind": "start_voting",
                "disabled": False,
            }
        elif pairings_phase == "voting" and not voting_results_published:
            primary_action = {
                "label": "Ergebnisse veröffentlichen",
                "action": "/publishVotingResults",
                "kind": "publish_results",
                "disabled": bool(voting_total_count == 0 or voting_done_count != voting_total_count),
            }
        else:
            primary_action = {
                "label": "Event abgeschlossen",
                "action": "",
                "kind": "done",
                "disabled": True,
            }

    end_play_disabled = bool(pairings_phase != "playing" or not pairings_started)

    return templates.TemplateResponse(
        "CustomerControlPanel.html",
        {
            "request": request,
            "start_file_exists": start_file_exists,
            "deck_count": deck_count,
            "deckersteller": deckersteller,
            "confirmed_count": confirmed_count,
            "tooltip_items": tooltip_items,
            "voting_done_count": voting_done_count,
            "voting_total_count": voting_total_count,
            "voting_results_published": voting_results_published,
            "voting_results_rows": voting_results_rows,
            "all_confirmed": all_confirmed,
            "pairings_started": pairings_started,
            "pairings_phase": pairings_phase,
            "active_round": active_round,
            "players": players,
            "selected_hosts": selected_hosts,
            "round_tables": round_tables,
            "default_num_pods": settings.default_num_pods,
            "min_decks_to_start": settings.min_decks_to_start,
            "active_round_all_tables_reported": active_round_status.get("all_tables_reported"),
            "active_round_missing_tables": active_round_status.get("missing_tables") or [],
            "active_round_reported_count": active_round_status.get("reported_count") or 0,
            "active_round_table_count": active_round_status.get("table_count") or 0,
            "active_round_reports_persisted": active_round_reports_persisted,
            "phase_name": phase_name,
            "phase_status_text": phase_status_text,
            "primary_action": primary_action,
            "end_play_disabled": end_play_disabled,
        }
    )

@app.post("/startRaffle")
async def start_raffle():
    """
    Führt den Raffle-Start durch und leitet den Benutzer zurück zum CCP.
    """
    try:
        start_raffle_service(FILE_PATH, START_FILE_PATH, min_decks=_current_settings().min_decks_to_start)
        await notify_state_change()
        return RedirectResponse(url="/CCP", status_code=303)
    except RaffleStartError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Starten des Raffles: {e}")

def shuffle_decks(deck_creators):
    return raffle_shuffle_decks(deck_creators)


def update_deck_owner(deckersteller, new_deck_owner):
    """
    Legacy helper kept for compatibility with existing call sites.
    """
    data_list = _load_raffle_list()
    updated = False
    for entry in data_list:
        if entry.get("deckersteller") == deckersteller:
            entry["deckOwner"] = new_deck_owner
            entry["received_confirmed"] = False
            updated = True
            break
    if updated:
        _atomic_write_json(FILE_PATH, data_list)

@app.post("/confirm_received")
async def confirm_received(deck_id: int = Form(...)):
    """
    Markiert für eine Deck-ID den Erhalt als bestätigt.
    """
    async with RAFFLE_LOCK:
        data_list = _load_raffle_list()

        updated = False
        for entry in data_list:
            if entry.get("deck_id") == deck_id:
                # nur sinnvoll, wenn raffle gestartet und ein Owner gesetzt ist
                entry["received_confirmed"] = True
                updated = True
                break

        if not updated:
            raise HTTPException(status_code=404, detail="Deck ID nicht gefunden.")

        _atomic_write_json(FILE_PATH, data_list)

    await notify_state_change()
    return RedirectResponse(url=f"/?deck_id={deck_id}", status_code=303)

@app.get("/api/commander_suggest")
async def commander_suggest(q: str = ""):
    """
    Returns up to SUGGEST_LIMIT objects for commander suggestions.
    Each item: {name, id, oracle_id, type_line}
    """
    q = (q or "").strip()
    settings = _current_settings()
    if len(q) < settings.api.suggest_min_chars:
        return JSONResponse([])

    key = f"cmd::{q.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return JSONResponse(cached)

    scry_q = settings.scryfall.commander_suggest_query_template.replace("{q}", q)
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards&order=name"

    headers = {
        "Accept": "application/json",
        "User-Agent": "CommanderRaffle/1.0 (contact: kizzm-commanderraffle@tri-b-oon.de)",
    }

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=headers) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return JSONResponse([])

            payload = r.json()
            data = payload.get("data") or []

            items = []
            for card in data:
                name = card.get("name")
                cid = card.get("id")
                if name and cid:
                    items.append({
                        "name": name,
                        "id": cid,
                        "oracle_id": card.get("oracle_id"),
                        "type_line": card.get("type_line"),
                    })
                if len(items) >= settings.api.suggest_limit:
                    break

            _cache_set(key, items)
            return JSONResponse(items)

    except Exception:
        return JSONResponse([])
    
@app.get("/api/partner_suggest")
async def partner_suggest(q: str = ""):
    """
    Returns up to SUGGEST_LIMIT objects matching q that are is:partner.
    Each item: {name, id, oracle_id, type_line}
    """
    q = (q or "").strip()
    settings = _current_settings()
    if len(q) < settings.api.suggest_min_chars:
        return JSONResponse([])

    key = f"partner::{q.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return JSONResponse(cached)

    scry_q = settings.scryfall.partner_suggest_query_template.replace("{q}", q)
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards&order=name"

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return JSONResponse([])

            payload = r.json()
            data = payload.get("data") or []

            items = []
            for card in data:
                name = card.get("name")
                cid = card.get("id")
                if name and cid:
                    items.append({
                        "name": name,
                        "id": cid,
                        "oracle_id": card.get("oracle_id"),
                        "type_line": card.get("type_line"),
                    })
                if len(items) >= settings.api.suggest_limit:
                    break

            _cache_set(key, items)
            return JSONResponse(items)

    except Exception:
        return JSONResponse([])

@app.get("/api/commander_partner_capable")
async def commander_partner_capable(name: str = ""):
    """
    Returns {"partner_capable": bool}
    Checks via Scryfall search: !"<exact name>" is:partner
    """
    return JSONResponse({"partner_capable": await _scryfall_is_partner_exact_name(name)})


@app.get("/api/validate_commander_combo")
async def validate_commander_combo(commander_id: str = "", commander2_id: str = ""):
    """
    Validates the currently selected commander pair.
    Returns: {"legal": bool, "error": str | None}
    """
    commander_id = (commander_id or "").strip()
    commander2_id = (commander2_id or "").strip()

    if not commander_id or not commander2_id:
        return JSONResponse(
            {"legal": False, "error": "Für die Legalitätsprüfung werden beide Commander-IDs benötigt."},
            status_code=400,
        )

    c1 = await _scryfall_get_card_by_id(commander_id)
    if not c1:
        return JSONResponse(
            {"legal": False, "error": "Commander 1 konnte bei Scryfall nicht geladen werden. Bitte erneut auswählen."},
            status_code=404,
        )

    c2 = await _scryfall_get_card_by_id(commander2_id)
    if not c2:
        return JSONResponse(
            {"legal": False, "error": "Commander 2 konnte bei Scryfall nicht geladen werden. Bitte erneut auswählen."},
            status_code=404,
        )

    combo_err = await _validate_commander_combo(c1, c2)
    return JSONResponse({"legal": combo_err is None, "error": combo_err})

async def _scryfall_named_exact(name: str) -> dict | None:
    """
    Resolve a card by exact name via Scryfall.
    Returns card JSON or None.
    """
    return await named_exact(name)


async def _round_report_avatar_art_url(commander_name: str, commander_id: str | None = None) -> str | None:
    commander_id = (commander_id or "").strip() or None
    card = None

    if commander_name:
        settings = _current_settings()
        safe_name = commander_name.replace('"', '\\"')
        query = settings.scryfall.round_report_avatar_query_template.replace('{name}', safe_name)
        url = (
            f"{SCRYFALL_BASE}/cards/search?"
            f"q={quote_plus(query)}&unique=art&order=released&dir=desc"
        )
        try:
            async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = (r.json().get('data') or [])
                    if data:
                        card = data[0]
        except Exception:
            card = None

    if not card and commander_id:
        card = await _scryfall_get_card_by_id(commander_id)

    if not card:
        return None

    img = _get_image_url(card, 'art_crop')
    if not img:
        img = _get_image_url(card, 'normal')
    return img


@app.get("/api/settings/effective")
async def settings_effective():
    settings, meta = load_event_settings()
    state, _raffle_list, _pairings = _current_event_state()

    return JSONResponse({
        "settings": settings_as_dict(settings),
        "meta": meta,
        "event_state": state.value,
        "editability": settings_editability(state),
    })


@app.patch("/api/settings")
async def settings_patch(payload: dict = Body(...)):
    current, _meta = load_event_settings()
    state, _raffle_list, _pairings = _current_event_state()

    try:
        updated, changed_keys = apply_settings_patch(current, payload, state)
    except SettingsUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    save_event_settings(updated)
    await notify_state_change()

    return JSONResponse({
        "ok": True,
        "changed_keys": changed_keys,
        "event_state": state.value,
        "settings": settings_as_dict(updated),
        "editability": settings_editability(state),
    })


@app.post("/api/settings/reset")
async def settings_reset():
    current, _meta = load_event_settings()
    state, _raffle_list, _pairings = _current_event_state()

    try:
        updated, changed_keys, skipped_locked_keys = reset_settings_with_locks(current, state)
    except SettingsUpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    save_event_settings(updated)
    await notify_state_change()

    return JSONResponse({
        "ok": True,
        "changed_keys": changed_keys,
        "skipped_locked_keys": skipped_locked_keys,
        "event_state": state.value,
        "settings": settings_as_dict(updated),
        "meta": load_event_settings()[1],
        "editability": settings_editability(state),
    })

@app.get("/api/background/default")
async def background_default():
    """
    Default Background:
    - Prefer local PNG from assets/backgrounds/ (random choice)
    - Fallback: current Scryfall-based default
    """
    settings = _current_settings()

    # 1) Local PNG backgrounds (preferred)
    bg_dir = Path("assets") / "backgrounds"
    try:
        if bg_dir.exists() and bg_dir.is_dir():
            # case-insensitive *.png
            allowed_ext = {".png", ".webp", ".jpg", ".jpeg"}
            pngs = [p for p in bg_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed_ext]
            if pngs:
                picked = choice(pngs)
                # served via app.mount("/assets", StaticFiles(directory="assets"))
                return JSONResponse(
                    {"url": f"/assets/backgrounds/{picked.name}", "zoom": settings.ui.default_bg_zoom}
                )
    except Exception:
        # any filesystem weirdness -> fall back to Scryfall
        pass

    # 2) Fallback: Scryfall default (existing logic)
    q = settings.scryfall.default_background_query
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(q)}&unique=cards&order=name"

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            first = await client.get(url)
            if first.status_code != 200:
                return JSONResponse({"url": None, "zoom": settings.ui.default_bg_zoom})

            payload = first.json()
            total = int(payload.get("total_cards") or 0)
            if total <= 0:
                return JSONResponse({"url": None, "zoom": settings.ui.default_bg_zoom})

            per_page = len(payload.get("data") or [])
            if per_page <= 0:
                return JSONResponse({"url": None, "zoom": settings.ui.default_bg_zoom})

            max_page = max(1, (total + per_page - 1) // per_page)
            page = randint(1, max_page)

            page_url = url + f"&page={page}"
            resp = await client.get(page_url)
            if resp.status_code != 200:
                return JSONResponse({"url": None, "zoom": settings.ui.default_bg_zoom})

            data = (resp.json().get("data") or [])
            if not data:
                return JSONResponse({"url": None, "zoom": settings.ui.default_bg_zoom})

            card = data[randint(0, len(data) - 1)]
            img = _get_image_url(card, "art_crop")
            return JSONResponse({"url": img, "zoom": settings.ui.default_bg_zoom})

    except Exception:
        return JSONResponse({"url": None, "zoom": settings.ui.default_bg_zoom})

async def _scryfall_query_preview_image(client: httpx.AsyncClient, query: str) -> str | None:
    query = (query or "").strip()
    if not query:
        return None

    url = (
        f"{SCRYFALL_BASE}/cards/search?"
        f"q={quote_plus(query)}&unique=prints&order=released&dir=desc"
    )

    r = await client.get(url)
    if r.status_code != 200:
        return None

    data = (r.json().get("data") or [])
    if not data:
        return None

    for card in data:
        img = _get_image_url(card, "border_crop") or _get_image_url(card, "large")
        if img:
            return img

    return None


@app.get("/api/background/commander")
async def background_commander(name: str = ""):
    name = (name or "").strip()
    settings = _current_settings()
    if not name:
        return JSONResponse({"url": None, "zoom": settings.ui.commander_bg_zoom})

    safe = name.replace('"', '\\"')
    default_q_template = (
        settings.scryfall.card_preview_query_template
        or settings.scryfall.commander_preview_query_template
        or 'game:paper is:commander !"{name}"'
    )
    fallback_q_template = settings.scryfall.card_preview_fallback_query_template or ""

    default_q = default_q_template.replace('{name}', safe)
    fallback_q = fallback_q_template.replace('{name}', safe)

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            img = await _scryfall_query_preview_image(client, default_q)
            if not img and fallback_q:
                img = await _scryfall_query_preview_image(client, fallback_q)
            return JSONResponse({"url": img, "zoom": settings.ui.commander_bg_zoom})

    except Exception:
        return JSONResponse({"url": None, "zoom": settings.ui.commander_bg_zoom})


def _best_deck_votes_bucket(state: dict) -> dict:
    votes = state.setdefault("best_deck_votes", {})
    if not isinstance(votes, dict):
        votes = {}
        state["best_deck_votes"] = votes
    return votes


def _deck_creator_guess_votes_bucket(state: dict) -> dict:
    votes = state.setdefault("deck_creator_guess_votes", {})
    if not isinstance(votes, dict):
        votes = {}
        state["deck_creator_guess_votes"] = votes
    return votes


def _votes_results_bucket(state: dict) -> dict:
    result = state.setdefault("voting_results", {})
    if not isinstance(result, dict):
        result = {}
        state["voting_results"] = result
    return result


def _commander_label(entry: dict) -> str:
    commander = (entry.get("commander") or "").strip()
    commander2 = (entry.get("commander2") or "").strip()
    if commander and commander2:
        return f"{commander} / {commander2}"
    return commander or f"Deck #{int(entry.get('deck_id') or 0)}"


def _published_voting_results(state: dict | None) -> dict | None:
    bucket = (state or {}).get("voting_results") or {}
    if not isinstance(bucket, dict):
        return None
    if not bucket.get("published"):
        return None
    data = bucket.get("data")
    return data if isinstance(data, dict) else None


def _configured_point_map(bucket: dict | None, key: str, defaults: dict[int, int]) -> dict[int, int]:
    section = (bucket or {}).get(key)
    if not isinstance(section, dict):
        return dict(defaults)

    out: dict[int, int] = {}
    for place_raw, points_raw in section.items():
        try:
            place = int(place_raw)
            points = int(points_raw)
        except (TypeError, ValueError):
            continue
        if place <= 0:
            continue
        out[place] = points

    return out or dict(defaults)


def _configured_correct_guess_points(bucket: dict | None) -> int:
    section = (bucket or {}).get("deck_creator_guess")
    if not isinstance(section, dict):
        return 1
    try:
        points = int(section.get("correct_guess", 1))
    except (TypeError, ValueError):
        return 1
    return max(points, 0)


def _calculate_play_phase_overview(raffle_list: list[dict], state: dict) -> dict:
    owners = sorted({(e.get("deckOwner") or "").strip() for e in raffle_list if (e.get("deckOwner") or "").strip()})
    gameplay_points = {owner: 0 for owner in owners}
    voting_points = (_current_settings().voting.points_scheme or {}) if _current_settings().voting else {}
    place_points = _configured_point_map(voting_points, "play_phase", {1: 4, 2: 3, 3: 2, 4: 1})

    round_reports = (state.get("round_reports") or {}) if isinstance(state, dict) else {}
    for reports_for_round in round_reports.values():
        if not isinstance(reports_for_round, dict):
            continue
        for report in reports_for_round.values():
            resolved = (report or {}).get("resolved_places") or {}
            if not isinstance(resolved, dict):
                continue
            for player, place in resolved.items():
                owner = (player or "").strip()
                if owner not in gameplay_points:
                    continue
                try:
                    p = int(place)
                except (TypeError, ValueError):
                    continue
                gameplay_points[owner] += place_points.get(p, 0)

    rows = []
    for owner in owners:
        rows.append({
            "player": owner,
            "game_points": int(gameplay_points.get(owner, 0) or 0),
        })

    rows.sort(key=lambda r: (-int(r.get("game_points") or 0), str(r.get("player") or "").lower()))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx

    return {
        "rows": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _calculate_voting_results(raffle_list: list[dict], state: dict) -> dict:
    owners = sorted({(e.get("deckOwner") or "").strip() for e in raffle_list if (e.get("deckOwner") or "").strip()})
    built_by_owner: dict[str, dict] = {}
    for owner in owners:
        built = next((e for e in raffle_list if (e.get("deckersteller") or "").strip() == owner), None)
        if built:
            built_by_owner[owner] = built

    voting_points = (_current_settings().voting.points_scheme or {}) if _current_settings().voting else {}
    gameplay_points = {owner: 0 for owner in owners}
    place_points = _configured_point_map(voting_points, "play_phase", {1: 4, 2: 3, 3: 2, 4: 1})
    best_deck_vote_points = _configured_point_map(voting_points, "best_deck_voting", {1: 3, 2: 2, 3: 1})
    best_deck_overall_points = _configured_point_map(
        voting_points,
        "best_deck_overall",
        {1: 8, 2: 5, 3: 3, 4: 2, 5: 1, 6: 0, 7: 0, 8: 0},
    )
    correct_guess_points = _configured_correct_guess_points(voting_points)
    round_reports = (state.get("round_reports") or {}) if isinstance(state, dict) else {}
    for reports_for_round in round_reports.values():
        if not isinstance(reports_for_round, dict):
            continue
        for report in reports_for_round.values():
            resolved = (report or {}).get("resolved_places") or {}
            if not isinstance(resolved, dict):
                continue
            for player, place in resolved.items():
                owner = (player or "").strip()
                if owner not in gameplay_points:
                    continue
                try:
                    p = int(place)
                except (TypeError, ValueError):
                    continue
                gameplay_points[owner] += place_points.get(p, 0)

    top3_votes = (state.get("best_deck_votes") or {}) if isinstance(state, dict) else {}
    top3_deck_points: dict[int, int] = {}
    for vote in top3_votes.values():
        if not isinstance(vote, dict):
            continue
        for place, pts in best_deck_vote_points.items():
            try:
                deck_id = int(vote.get(str(place)) or 0)
            except (TypeError, ValueError):
                deck_id = 0
            if deck_id > 0:
                top3_deck_points[deck_id] = top3_deck_points.get(deck_id, 0) + pts

    ranked_decks = sorted(top3_deck_points.items(), key=lambda x: (-x[1], x[0]))
    top3_bonus_by_owner = {owner: 0 for owner in owners}
    for idx, (deck_id, _pts) in enumerate(ranked_decks, start=1):
        bonus = best_deck_overall_points.get(idx, 0)
        deck_entry = next((e for e in raffle_list if int(e.get("deck_id") or 0) == deck_id), None)
        owner = (deck_entry.get("deckersteller") or "").strip() if deck_entry else ""
        if owner in top3_bonus_by_owner:
            top3_bonus_by_owner[owner] += bonus

    deckraten_votes = (state.get("deck_creator_guess_votes") or {}) if isinstance(state, dict) else {}
    guess_points_by_owner = {owner: 0 for owner in owners}
    for voter_deck_id, vote in deckraten_votes.items():
        if not isinstance(vote, dict):
            continue
        voter_entry = next((e for e in raffle_list if str(int(e.get("deck_id") or 0)) == str(voter_deck_id)), None)
        voter_owner = (voter_entry.get("deckOwner") or "").strip() if voter_entry else ""
        if voter_owner not in guess_points_by_owner:
            continue
        score = 0
        for creator, assigned_deck_id in vote.items():
            if creator in {"voted_by", "submitted_at"}:
                continue
            try:
                assigned = int(assigned_deck_id or 0)
            except (TypeError, ValueError):
                continue
            target_entry = next((e for e in raffle_list if (e.get("deckersteller") or "").strip() == str(creator).strip()), None)
            target_deck_id = int((target_entry or {}).get("deck_id") or 0)
            if assigned > 0 and target_deck_id > 0 and assigned == target_deck_id:
                score += correct_guess_points
        guess_points_by_owner[voter_owner] += score

    rows = []
    for owner in owners:
        built = built_by_owner.get(owner) or {}
        built_deck_id = int(built.get("deck_id") or 0)
        top3_received_vote_points = top3_deck_points.get(built_deck_id, 0)
        gameplay = gameplay_points.get(owner, 0)
        top3_bonus = top3_bonus_by_owner.get(owner, 0)
        guess_points = guess_points_by_owner.get(owner, 0)
        total = gameplay + top3_bonus + guess_points
        rows.append({
            "player": owner,
            "deck_name": _commander_label(built) if built else "-",
            "game_points": gameplay,
            "deck_voting_points": top3_bonus,
            "top3_received_vote_points": top3_received_vote_points,
            "guess_points": guess_points,
            "top3_overall_bonus": top3_bonus,
            "total_points": total,
        })

    rows.sort(key=lambda r: (-int(r.get("total_points") or 0), -(int(r.get("game_points") or 0)), str(r.get("player") or "").lower()))

    return {
        "rows": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _top3_points_and_rank_by_deck(state: dict) -> tuple[dict[int, int], dict[int, int]]:
    top3_votes = (state.get("best_deck_votes") or {}) if isinstance(state, dict) else {}
    voting_points = (_current_settings().voting.points_scheme or {}) if _current_settings().voting else {}
    best_deck_vote_points = _configured_point_map(voting_points, "best_deck_voting", {1: 3, 2: 2, 3: 1})
    points_by_deck: dict[int, int] = {}
    for vote in top3_votes.values():
        if not isinstance(vote, dict):
            continue
        for place, pts in best_deck_vote_points.items():
            try:
                deck_id = int(vote.get(str(place)) or 0)
            except (TypeError, ValueError):
                deck_id = 0
            if deck_id > 0:
                points_by_deck[deck_id] = points_by_deck.get(deck_id, 0) + pts

    ranking = sorted(points_by_deck.items(), key=lambda x: (-x[1], x[0]))
    rank_by_deck = {deck_id: rank for rank, (deck_id, _pts) in enumerate(ranking, start=1)}
    return points_by_deck, rank_by_deck


def _round_rank_by_owner(state: dict, owner: str) -> dict[int, int | None]:
    reports = (state.get("round_reports") or {}) if isinstance(state, dict) else {}
    ranks: dict[int, int | None] = {}
    for round_key, round_reports in reports.items():
        try:
            round_no = int(round_key)
        except (TypeError, ValueError):
            continue
        rank_value = None
        if isinstance(round_reports, dict):
            for report in round_reports.values():
                resolved = (report or {}).get("resolved_places") or {}
                if not isinstance(resolved, dict):
                    continue
                if owner in resolved:
                    try:
                        rank_value = int(resolved.get(owner))
                    except (TypeError, ValueError):
                        rank_value = None
                    break
        ranks[round_no] = rank_value
    return ranks


def _results_columns_and_rows() -> tuple[list[str], list[list[str]]]:
    raffle_list = _load_raffle_list()
    state = _load_pairings() or {}

    voting_results = _calculate_voting_results(raffle_list, state)
    row_by_owner = {
        str(row.get("player") or "").strip(): row
        for row in (voting_results.get("rows") or [])
        if isinstance(row, dict)
    }
    top3_points_by_deck, top3_rank_by_deck = _top3_points_and_rank_by_deck(state)
    best_deck_votes = (state.get("best_deck_votes") or {}) if isinstance(state, dict) else {}
    deck_creator_guess_votes = (state.get("deck_creator_guess_votes") or {}) if isinstance(state, dict) else {}

    max_rounds = int(state.get("active_round") or 0) if isinstance(state, dict) else 0
    for round_key in ((state.get("round_reports") or {}) if isinstance(state, dict) else {}).keys():
        try:
            max_rounds = max(max_rounds, int(round_key))
        except (TypeError, ValueError):
            pass

    columns = [
        "deck_id",
        "deckersteller",
        "deckOwner",
        "commander",
        "commander2",
    ]
    for round_no in range(1, max_rounds + 1):
        columns.append(f"round_reports.{round_no}.resolved_places[deckOwner]")
    columns.extend([
        "best_deck_votes.{deck_id}.1 (vom deckOwner abgegeben)",
        "best_deck_votes.{deck_id}.2 (vom deckOwner abgegeben)",
        "best_deck_votes.{deck_id}.3 (vom deckOwner abgegeben)",
        "calculated.top3_received_vote_points (für Deck des deckerstellers)",
        "calculated.top3_received_rank (für Deck des deckerstellers)",
        "calculated.top3_rank_points_used_for_overall (creator-basiert)",
        "deck_creator_guess_votes.{deck_id} (vom deckOwner abgegeben)",
        "calculated.round_phase_points (deckOwner-basiert)",
        "calculated.deck_creator_guess_points (deckOwner-basiert)",
        "calculated.overall_event_points (owner+creator gemischt)",
    ])

    rows: list[list[str]] = []
    sorted_entries = sorted(raffle_list, key=lambda e: int(e.get("deck_id") or 0))
    for entry in sorted_entries:
        deck_id = int(entry.get("deck_id") or 0)
        owner = (entry.get("deckOwner") or "").strip()
        round_ranks = _round_rank_by_owner(state, owner)
        top3_vote = (best_deck_votes.get(str(deck_id)) or {}) if deck_id > 0 else {}
        deckrate_vote = (deck_creator_guess_votes.get(str(deck_id)) or {}) if deck_id > 0 else {}
        owner_result = row_by_owner.get(owner, {})

        row_values: list[str] = [
            str(deck_id) if deck_id > 0 else "",
            str(entry.get("deckersteller") or ""),
            owner,
            str(entry.get("commander") or ""),
            str(entry.get("commander2") or ""),
        ]
        for round_no in range(1, max_rounds + 1):
            value = round_ranks.get(round_no)
            row_values.append("" if value is None else str(value))

        row_values.extend([
            "" if not top3_vote else str(top3_vote.get("1") or ""),
            "" if not top3_vote else str(top3_vote.get("2") or ""),
            "" if not top3_vote else str(top3_vote.get("3") or ""),
            str(top3_points_by_deck.get(deck_id, 0)),
            str(top3_rank_by_deck.get(deck_id, "")),
            str(owner_result.get("deck_voting_points") or 0),
            "" if not deckrate_vote else json.dumps(deckrate_vote, ensure_ascii=False, sort_keys=True),
            str(owner_result.get("game_points") or 0),
            str(owner_result.get("guess_points") or 0),
            str(owner_result.get("total_points") or 0),
        ])
        rows.append(row_values)

    return columns, rows


def _transpose_results_table(columns: list[str], rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    row_labels = [
        (str(r[0]).strip() or f"row_{i+1}") if isinstance(r, list) and len(r) > 0 else f"row_{i+1}"
        for i, r in enumerate(rows)
    ]
    transposed_header = ["field"] + row_labels
    transposed_rows: list[list[str]] = []
    for col_idx, col_name in enumerate(columns):
        transposed_rows.append([
            col_name,
            *[
                (str(r[col_idx]) if col_idx < len(r) else "")
                for r in rows
            ],
        ])
    return transposed_header, transposed_rows


@app.get("/results", response_class=HTMLResponse)
async def development_results_overview(PDF: bool = False):
    columns, rows = _results_columns_and_rows()
    columns, rows = _transpose_results_table(columns, rows)

    if PDF:
        def _pdf_escape(text: str) -> str:
            return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        def _wrap_text(value: str, width: float) -> list[str]:
            text = str(value or "")
            if not text:
                return [""]
            char_w = 3.6  # approx for Helvetica 7pt
            max_chars = max(1, int((width - 6.0) / char_w))
            out: list[str] = []
            for raw_line in text.splitlines() or [""]:
                words = raw_line.split(" ")
                current = ""
                for word in words:
                    if not current:
                        candidate = word
                    else:
                        candidate = current + " " + word

                    if len(candidate) <= max_chars:
                        current = candidate
                        continue

                    if current:
                        out.append(current)
                        current = ""

                    while len(word) > max_chars:
                        out.append(word[:max_chars])
                        word = word[max_chars:]
                    current = word
                out.append(current)
            return out or [""]

        def _table_pdf_bytes(header_row: list[str], body_rows: list[list[str]]) -> bytes:
            page_w, page_h = 595.0, 842.0  # A4 portrait
            margin = 20.0
            font_size = 7.0
            line_h = 9.0

            ncols = max(1, len(header_row))
            usable_w = max(100.0, page_w - (2 * margin))

            # adaptive column widths based on textual width, then scaled to fit page width
            raw_widths = []
            for c in range(ncols):
                max_len = len(str(header_row[c] if c < len(header_row) else ""))
                for row in body_rows:
                    max_len = max(max_len, len(str(row[c] if c < len(row) else "")))
                raw_widths.append(min(max(28.0, max_len * 3.2 + 8.0), 190.0))

            width_sum = sum(raw_widths) or 1.0
            col_widths = [(w / width_sum) * usable_w for w in raw_widths]

            objects: list[bytes] = []
            objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
            objects.append(b"__PAGES_PLACEHOLDER__")
            objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
            objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

            page_object_ids: list[int] = []

            def _prepare_row(row: list[str]) -> tuple[list[list[str]], float]:
                wrapped_cells: list[list[str]] = []
                max_lines = 1
                for i in range(ncols):
                    val = str(row[i] if i < len(row) else "")
                    lines = _wrap_text(val, col_widths[i])
                    wrapped_cells.append(lines)
                    max_lines = max(max_lines, len(lines))
                row_h = (max_lines * line_h) + 4.0
                return wrapped_cells, row_h

            prepared_header, header_h = _prepare_row(header_row)
            prepared_body = [_prepare_row(r) for r in body_rows]

            idx = 0
            while idx < len(prepared_body) or (len(prepared_body) == 0 and idx == 0):
                stream_ops: list[str] = []
                y_cursor = page_h - margin

                def _draw_row(prepared: list[list[str]], row_h: float, bold: bool) -> None:
                    nonlocal y_cursor
                    y_bottom = y_cursor - row_h
                    x = margin
                    for col_idx, lines in enumerate(prepared):
                        w = col_widths[col_idx]
                        stream_ops.append(f"{x:.2f} {y_bottom:.2f} {w:.2f} {row_h:.2f} re S")
                        font_name = "/F2" if bold else "/F1"
                        for li, line in enumerate(lines):
                            text_x = x + 3.0
                            text_y = y_cursor - 10.0 - (li * line_h)
                            stream_ops.append(
                                f"BT {font_name} {font_size:.1f} Tf 1 0 0 1 {text_x:.2f} {text_y:.2f} Tm ({_pdf_escape(line)}) Tj ET"
                            )
                        x += w
                    y_cursor = y_bottom

                _draw_row(prepared_header, header_h, bold=True)

                if len(prepared_body) == 0:
                    idx = 1
                else:
                    while idx < len(prepared_body):
                        row_prepared, row_h = prepared_body[idx]
                        if (y_cursor - row_h) < margin:
                            break
                        _draw_row(row_prepared, row_h, bold=False)
                        idx += 1

                stream_data = "\n".join(stream_ops).encode("latin-1", errors="replace")

                content_obj_id = len(objects) + 1
                objects.append(
                    f"<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1")
                    + stream_data
                    + b"\nendstream"
                )

                page_obj_id = len(objects) + 1
                page_object_ids.append(page_obj_id)
                page_obj = (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.0f} {page_h:.0f}] "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_obj_id} 0 R >>"
                ).encode("latin-1")
                objects.append(page_obj)

                if len(prepared_body) == 0:
                    break

            kids = " ".join(f"{pid} 0 R" for pid in page_object_ids)
            objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_ids)} >>".encode("latin-1")

            out = io.BytesIO()
            out.write(b"%PDF-1.4\n")
            offsets = [0]
            for i, obj in enumerate(objects, start=1):
                offsets.append(out.tell())
                out.write(f"{i} 0 obj\n".encode("latin-1"))
                out.write(obj)
                out.write(b"\nendobj\n")

            xref_start = out.tell()
            out.write(f"xref\n0 {len(objects)+1}\n".encode("latin-1"))
            out.write(b"0000000000 65535 f \n")
            for i in range(1, len(objects) + 1):
                out.write(f"{offsets[i]:010d} 00000 n \n".encode("latin-1"))
            out.write(
                f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode("latin-1")
            )
            return out.getvalue()

        pdf_bytes = _table_pdf_bytes(columns, rows)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
        headers = {"Content-Disposition": f"attachment; filename=results_{timestamp}.pdf"}
        return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)

    lines = [
        "<html><head><meta charset='utf-8'><title>/results</title></head><body style='font-family: system-ui; padding: 16px;'>",
        "<h2>Event-Ergebnisse (Entwicklung)</h2>",
        "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse: collapse; font-size: 14px;'>",
        "<thead><tr>",
    ]
    for col in columns:
        lines.append(f"<th>{html.escape(col)}</th>")
    lines.append("</tr></thead><tbody>")

    for row_values in rows:
        lines.append("<tr>")
        for value in row_values:
            lines.append(f"<td>{html.escape(str(value))}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table>")
    lines.append("</body></html>")
    return HTMLResponse("\n".join(lines))


def _best_deck_candidates_for_owner(raffle_list: list[dict], owner_name: str) -> list[dict]:
    owner = (owner_name or "").strip()
    own_built_entry = next(
        (e for e in raffle_list if (e.get("deckersteller") or "").strip() == owner),
        None,
    )
    own_built_deck_id = int((own_built_entry or {}).get("deck_id") or 0)

    candidates: list[dict] = []
    for entry in raffle_list:
        deck_id = int(entry.get("deck_id") or 0)
        if deck_id <= 0:
            continue
        if deck_id == own_built_deck_id:
            continue
        commander = (entry.get("commander") or "").strip()
        commander2 = (entry.get("commander2") or "").strip()
        commander_label = commander
        if commander and commander2:
            commander_label = f"{commander} / {commander2}"
        candidates.append({
            "deck_id": deck_id,
            "deckersteller": (entry.get("deckersteller") or "").strip(),
            "deck_owner": (entry.get("deckOwner") or "").strip(),
            "commander": commander_label,
            "commander1": commander,
            "commander2": commander2,
        })

    return sorted(candidates, key=lambda item: item["deck_id"])


@app.get("/api/voting/best-deck/current")
async def current_best_deck_voting(deck_id: int):
    raffle_list = _load_raffle_list()
    state = _load_pairings() or {}

    phase = (state.get("phase") or "").strip().lower()
    if not state or phase not in {"pre_voting", "voting"}:
        raise HTTPException(status_code=400, detail="Aktuell keine aktive Vorabauswertung/Voting-Phase.")

    entry = next((e for e in raffle_list if e.get("deck_id") == deck_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Deck nicht gefunden.")

    owner_name = (entry.get("deckOwner") or "").strip()
    if not owner_name:
        raise HTTPException(status_code=400, detail="Deck hat keinen zugewiesenen Owner.")

    if phase == "pre_voting":
        overview = _calculate_play_phase_overview(raffle_list, state)
        return {
            "phase_title": "Warten auf das Voting",
            "voting_kind": "pre_voting_overview",
            "status_message": "",
            "candidates": [],
            "places": [],
            "placements": {},
            "has_vote": False,
            "results": overview,
        }

    published = _published_voting_results(state)
    if published:
        return {
            "phase_title": "Ergebnisse",
            "voting_kind": "results_published",
            "status_message": "Die Ergebnisse wurden veröffentlicht.",
            "candidates": [],
            "places": [],
            "placements": {},
            "has_vote": True,
            "results": published,
        }

    candidates = _best_deck_candidates_for_owner(raffle_list, owner_name)
    raffle_by_deck_id = {
        int(e.get("deck_id") or 0): e
        for e in raffle_list
        if int(e.get("deck_id") or 0) > 0
    }
    for candidate in candidates:
        candidate_deck_id = int(candidate.get("deck_id") or 0)
        owner_entry = raffle_by_deck_id.get(candidate_deck_id) or {}
        commander_name = (owner_entry.get("commander") or "").strip()
        commander_id = owner_entry.get("commander_id")
        candidate["avatar_url"] = await _round_report_avatar_art_url(commander_name, commander_id)

    vote_key = str(deck_id)
    top3_votes = (state.get("best_deck_votes") or {}).get(vote_key) or {}
    deckraten_votes = (state.get("deck_creator_guess_votes") or {}).get(vote_key) or {}

    top3_done = bool(top3_votes)
    deckraten_done = bool(deckraten_votes)

    if top3_done and deckraten_done:
        return {
            "phase_title": "Warten auf Ergebnisse",
            "voting_kind": "waiting_results",
            "status_message": "Danke! Du hast beide Votings bestätigt. Bitte warte, bis die Ergebnisse veröffentlicht werden.",
            "candidates": [],
            "places": [],
            "placements": {},
            "has_vote": True,
        }

    if not top3_done:
        placements = {"1": None, "2": None, "3": None}
        for place in ["1", "2", "3"]:
            val = top3_votes.get(place)
            if isinstance(val, int):
                placements[place] = val

        return {
            "phase_title": "Best-Deck-Voting",
            "voting_kind": "top3_fixed",
            "status_message": "Hallo {owner}, bitte vote für deine Top 3.".format(owner=owner_name),
            "candidates": candidates,
            "places": [
                {"id": "1", "label": "Rang 1"},
                {"id": "2", "label": "Rang 2"},
                {"id": "3", "label": "Rang 3"},
            ],
            "placements": placements,
            "has_vote": all(bool(placements[k]) for k in ["1", "2", "3"]),
        }

    places = [
        {
            "id": str(item.get("deckersteller") or "").strip(),
            "label": str(item.get("deckersteller") or "").strip() or f"Deck #{int(item.get('deck_id') or 0)}",
        }
        for item in candidates
    ]
    places = [place for place in places if place["id"]]
    placements: dict[str, int | None] = {place["id"]: None for place in places}
    for place in places:
        val = deckraten_votes.get(place["id"])
        if isinstance(val, int):
            placements[place["id"]] = val

    return {
        "phase_title": "Deckraten",
        "voting_kind": "deck_creator_guess",
        "status_message": "Hallo {owner}, ordne bitte alle Decks den Deckerstellern zu.".format(owner=owner_name),
        "candidates": candidates,
        "places": places,
        "placements": placements,
        "has_vote": bool(places) and all(bool(placements[place["id"]]) for place in places),
    }


@app.post("/api/voting/best-deck/submit")
async def submit_best_deck_vote(payload: dict = Body(...)):
    deck_id = int(payload.get("deck_id") or 0)
    raw_places = payload.get("placements") or {}

    if deck_id <= 0:
        raise HTTPException(status_code=400, detail="deck_id fehlt.")
    if not isinstance(raw_places, dict):
        raise HTTPException(status_code=400, detail="placements muss ein Objekt sein.")

    async with RAFFLE_LOCK:
        raffle_list = _load_raffle_list()
        state = _load_pairings() or {}

        if not state or (state.get("phase") or "") != "voting":
            raise HTTPException(status_code=400, detail="Aktuell keine aktive Voting-Phase.")

        entry = next((e for e in raffle_list if e.get("deck_id") == deck_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Deck nicht gefunden.")

        owner_name = (entry.get("deckOwner") or "").strip()
        if not owner_name:
            raise HTTPException(status_code=400, detail="Deck hat keinen zugewiesenen Owner.")

        if _published_voting_results(state):
            raise HTTPException(status_code=409, detail="Ergebnisse wurden bereits veröffentlicht.")

        top3_votes = _best_deck_votes_bucket(state)
        deckraten_votes = _deck_creator_guess_votes_bucket(state)
        key = str(deck_id)

        top3_done = bool(top3_votes.get(key))
        deckraten_done = bool(deckraten_votes.get(key))
        if top3_done and deckraten_done:
            raise HTTPException(status_code=409, detail="Voting wurde bereits vollständig bestätigt.")

        candidates = _best_deck_candidates_for_owner(raffle_list, owner_name)
        candidate_ids = {int(item["deck_id"]) for item in candidates}

        if not top3_done:
            normalized: dict[str, int] = {}
            seen: set[int] = set()
            for place in ["1", "2", "3"]:
                val = raw_places.get(place)
                try:
                    voted_deck_id = int(val)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Bitte Rang 1 bis 3 vollständig belegen.")
                if voted_deck_id in seen:
                    raise HTTPException(status_code=400, detail="Jeder Rang muss ein anderes Deck enthalten.")
                if voted_deck_id not in candidate_ids:
                    raise HTTPException(status_code=400, detail="Ungültige Deck-Auswahl im Voting.")
                seen.add(voted_deck_id)
                normalized[place] = voted_deck_id

            top3_votes[key] = {
                "1": normalized["1"],
                "2": normalized["2"],
                "3": normalized["3"],
                "voted_by": owner_name,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_write_pairings(state)
        else:
            places = [str(item.get("deckersteller") or "").strip() for item in candidates]
            places = [place for place in places if place]

            normalized: dict[str, int] = {}
            seen: set[int] = set()
            for place in places:
                val = raw_places.get(place)
                try:
                    voted_deck_id = int(val)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Bitte alle Decks vollständig zuordnen.")
                if voted_deck_id in seen:
                    raise HTTPException(status_code=400, detail="Jede Zuordnung muss ein anderes Deck enthalten.")
                if voted_deck_id not in candidate_ids:
                    raise HTTPException(status_code=400, detail="Ungültige Deck-Auswahl im Voting.")
                seen.add(voted_deck_id)
                normalized[place] = voted_deck_id

            if len(seen) != len(candidate_ids):
                raise HTTPException(status_code=400, detail="Bitte alle Decks vollständig zuordnen.")

            deckraten_votes[key] = {
                **normalized,
                "voted_by": owner_name,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_write_pairings(state)

    await notify_state_change()
    return {"ok": True}


@app.get("/api/round-report/current")
async def current_round_report(deck_id: int):
    raffle_list = _load_raffle_list()
    state = _load_pairings() or {}

    if not state or (state.get("phase") or "") != "playing":
        raise HTTPException(status_code=400, detail="Aktuell keine aktive Spielrunde.")

    active_round = int(state.get("active_round") or 0)
    if active_round <= 0:
        raise HTTPException(status_code=400, detail="Keine aktive Runde gefunden.")

    entry = next((e for e in raffle_list if e.get("deck_id") == deck_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Deck nicht gefunden.")

    table = int(entry.get("pairing_table") or 0)
    players = entry.get("pairing_players") or []
    if table <= 0 or not players:
        raise HTTPException(status_code=400, detail="Deck hat in der aktiven Runde keinen Tisch.")

    reports_for_round = (state.get("round_reports") or {}).get(str(active_round), {})
    existing = reports_for_round.get(str(table))

    player_meta: dict[str, dict] = {}
    for player in players:
        owner_entry = next((e for e in raffle_list if (e.get("deckOwner") or "").strip() == player), None)
        commander_name = (owner_entry or {}).get("commander") or ""
        commander_id = (owner_entry or {}).get("commander_id")
        avatar_url = await _round_report_avatar_art_url(commander_name, commander_id)
        player_meta[player] = {
            "avatar_url": avatar_url,
            "commander": str((owner_entry or {}).get("commander") or "").strip(),
            "commander2": str((owner_entry or {}).get("commander2") or "").strip(),
        }

    return {
        "round": active_round,
        "table": table,
        "players": players,
        "player_meta": player_meta,
        "has_report": bool(existing),
        "report": existing,
    }


@app.post("/api/round-report/submit")
async def submit_round_report(payload: dict = Body(...)):
    deck_id = int(payload.get("deck_id") or 0)
    raw_places = payload.get("placements") or {}

    if deck_id <= 0:
        raise HTTPException(status_code=400, detail="deck_id fehlt.")
    if not isinstance(raw_places, dict):
        raise HTTPException(status_code=400, detail="placements muss ein Objekt sein.")

    async with RAFFLE_LOCK:
        raffle_list = _load_raffle_list()
        state = _load_pairings() or {}
        if not state or (state.get("phase") or "") != "playing":
            raise HTTPException(status_code=400, detail="Aktuell keine aktive Spielrunde.")

        active_round = int(state.get("active_round") or 0)
        entry = next((e for e in raffle_list if e.get("deck_id") == deck_id), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Deck nicht gefunden.")

        table = int(entry.get("pairing_table") or 0)
        players = [p for p in (entry.get("pairing_players") or []) if p]
        if table <= 0 or not players:
            raise HTTPException(status_code=400, detail="Deck hat in der aktiven Runde keinen Tisch.")

        reports_for_round = _pairings_reports_bucket(state, active_round)
        table_key = str(table)
        if reports_for_round.get(table_key):
            raise HTTPException(status_code=409, detail="Für diesen Tisch wurde bereits ein Ergebnis gemeldet.")

        normalized_raw: dict[str, list[str]] = {}
        seen = set()
        for place in ["1", "2", "3", "4"]:
            group = raw_places.get(place) or []
            if not isinstance(group, list):
                continue
            cleaned = []
            for player in group:
                if player in seen:
                    continue
                if player not in players:
                    continue
                seen.add(player)
                cleaned.append(player)
            normalized_raw[place] = cleaned

        if len(seen) != len(players):
            raise HTTPException(status_code=400, detail="Bitte alle Spieler platzieren.")

        resolved_places = _resolve_round_places(normalized_raw)
        reports_for_round[table_key] = {
            "round": active_round,
            "table": table,
            "players": players,
            "raw_placements": normalized_raw,
            "resolved_places": resolved_places,
            "reported_by": entry.get("deckOwner") or "",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        _sync_round_completion_marker(state, active_round)
        _atomic_write_pairings(state)

    await notify_state_change()
    return {"ok": True}


@app.post("/resetRoundReport")
async def reset_round_report(round_no: int = Form(...), table_no: int = Form(...)):
    async with RAFFLE_LOCK:
        state = _load_pairings()
        if not state:
            raise HTTPException(status_code=400, detail="Pairings wurden noch nicht gestartet.")

        reports = state.get("round_reports") or {}
        round_reports = reports.get(str(round_no)) or {}
        round_reports.pop(str(table_no), None)
        if round_reports:
            reports[str(round_no)] = round_reports
        else:
            reports.pop(str(round_no), None)
        state["round_reports"] = reports
        _sync_round_completion_marker(state, int(round_no))
        _atomic_write_pairings(state)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)

@app.post("/publishVotingResults")
async def publish_voting_results():
    async with RAFFLE_LOCK:
        raffle_list = _load_raffle_list()
        state = _load_pairings() or {}
        if not state or (state.get("phase") or "") != "voting":
            raise HTTPException(status_code=400, detail="Aktuell keine aktive Voting-Phase.")

        owners = sorted({(e.get("deckOwner") or "").strip() for e in raffle_list if (e.get("deckOwner") or "").strip()})
        top3_votes = _best_deck_votes_bucket(state)
        deckraten_votes = _deck_creator_guess_votes_bucket(state)

        for owner in owners:
            owner_entry = next((e for e in raffle_list if (e.get("deckOwner") or "").strip() == owner), None)
            owner_deck_id = int((owner_entry or {}).get("deck_id") or 0)
            key = str(owner_deck_id)
            if not top3_votes.get(key) or not deckraten_votes.get(key):
                raise HTTPException(status_code=400, detail="Noch nicht alle Teilnehmer haben beide Votings abgeschlossen.")

        results = _calculate_voting_results(raffle_list, state)
        bucket = _votes_results_bucket(state)
        bucket["published"] = True
        bucket["published_at"] = datetime.now(timezone.utc).isoformat()
        bucket["data"] = results
        _atomic_write_pairings(state)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)


register_ws_routes(
    app,
    ws_manager=ws_manager,
    start_file_exists_loader=lambda: START_FILE_PATH.exists(),
    raffle_loader=_load_raffle_list,
    global_signature_fn=_global_signature,
    deck_signature_fn=_deck_signature,
)

@app.post("/startPairings")
async def start_pairings(num_pods: int = Form(...), hosts: list[str] = Form(default=[])):
    async with RAFFLE_LOCK:
        raffle_list = _load_raffle_list()

        if not START_FILE_PATH.exists():
            raise HTTPException(status_code=400, detail="Raffle noch nicht gestartet.")

        if _current_settings().require_all_confirmed_before_pairings and not _all_received_confirmed(raffle_list):
            raise HTTPException(status_code=400, detail="Nicht alle Decks wurden bestätigt.")

        players = _deckowners(raffle_list)
        if len(players) < 3:
            raise HTTPException(status_code=400, detail="Zu wenige Spieler.")

        # Hosts validieren: nur bekannte Spieler, keine Duplikate
        host_clean: list[str] = []
        seen: set[str] = set()
        for h in hosts or []:
            h = (h or "").strip()
            if not h or h in seen:
                continue
            if h not in players:
                raise HTTPException(status_code=400, detail=f"Unbekannter Host: {h}")
            seen.add(h)
            host_clean.append(h)

        if len(host_clean) > int(num_pods):
            raise HTTPException(status_code=400, detail="Es können höchstens so viele Hosts gewählt werden wie Tische vorhanden sind.")

        fixed_first = _first_round_with_hosts(players, int(num_pods), host_clean) if host_clean else None

        rounds = _build_rounds(players, int(num_pods), _current_settings().max_rounds, fixed_first_round=fixed_first)

        state = {
            "pods": int(num_pods),
            "players": players,
            "rounds": rounds,
            "active_round": 1,
            "phase": "playing",
            "hosts": sorted(host_clean, key=lambda x: x.lower()),
        }
        _atomic_write_pairings(state)

        # Runde 1 in raffle.json eintragen
        _apply_round_to_raffle(raffle_list, state, round_no=1)
        _atomic_write_json(FILE_PATH, raffle_list)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)

@app.post("/nextRound")
async def next_round():
    async with RAFFLE_LOCK:
        state = _load_pairings()
        if not state:
            raise HTTPException(status_code=400, detail="Pairings wurden noch nicht gestartet.")

        if state.get("phase") != "playing":
            raise HTTPException(status_code=400, detail="Spielphase ist nicht aktiv.")

        rounds = state.get("rounds") or []
        active = int(state.get("active_round") or 1)
        current_round_status = _round_report_status(state, active)
        if current_round_status.get("table_count") > 0 and not current_round_status.get("all_tables_reported"):
            missing = ", ".join([f"Tisch {t}" for t in current_round_status.get("missing_tables") or []])
            raise HTTPException(status_code=400, detail=f"Nächste Runde kann erst gestartet werden, wenn alle Tische gemeldet haben. Fehlend: {missing}")

        if active >= len(rounds):
            # keine nächste Runde mehr -> bleibt bei letzter Runde, oder du könntest automatisch voting setzen
            return RedirectResponse(url="/CCP", status_code=303)

        _sync_round_completion_marker(state, active)
        active += 1
        state["active_round"] = active
        _atomic_write_pairings(state)

        raffle_list = _load_raffle_list()
        _apply_round_to_raffle(raffle_list, state, round_no=active)
        _atomic_write_json(FILE_PATH, raffle_list)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)

@app.post("/endPlayPhase")
async def end_play_phase():
    async with RAFFLE_LOCK:
        state = _load_pairings()
        if not state:
            raise HTTPException(status_code=400, detail="Pairings wurden noch nicht gestartet.")

        active_round = int(state.get("active_round") or 0)
        if active_round > 0:
            _sync_round_completion_marker(state, active_round)

        state["phase"] = "pre_voting"
        _atomic_write_pairings(state)

        raffle_list = _load_raffle_list()
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "pre_voting"
        _atomic_write_json(FILE_PATH, raffle_list)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)


@app.post("/startVotingPhase")
async def start_voting_phase():
    async with RAFFLE_LOCK:
        state = _load_pairings()
        if not state:
            raise HTTPException(status_code=400, detail="Pairings wurden noch nicht gestartet.")

        phase = (state.get("phase") or "").strip().lower()
        if phase != "pre_voting":
            raise HTTPException(status_code=400, detail="Vorabauswertung ist nicht aktiv.")

        state["phase"] = "voting"
        _atomic_write_pairings(state)

        raffle_list = _load_raffle_list()
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"
        _atomic_write_json(FILE_PATH, raffle_list)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)

if __name__ == "__main__":
    uvicorn.run('main:app', port=8080, host="0.0.0.0", reload=True)
