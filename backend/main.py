import uvicorn
from fastapi import Body, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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

def _global_signature(start_file_exists: bool, raffle_list: list[dict]) -> str:
    return global_signature(start_file_exists, raffle_list, pairings_loader=_load_pairings)

def _deck_signature(deck_id: int, start_file_exists: bool, raffle_list: list[dict]) -> str:
    return deck_signature(deck_id, start_file_exists, raffle_list)


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

        if pairings_phase == "voting" or entry_pairing_phase == "voting":
            glasscard_title = "Best-Deck-Voting"
        elif pairings_phase == "playing" and active_round > 0:
            glasscard_title = f"Spielphase – Runde {active_round}"
        elif entry_pairing_round > 0:
            glasscard_title = f"Spielphase – Runde {entry_pairing_round}"
        elif all_confirmed and not pairings_started:
            glasscard_title = "Warten auf Pairings"

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

@app.post("/clear")
async def clear_data():
    """
    Löscht die Dateien raffle.json und start.txt, falls vorhanden, und erstellt eine leere raffle.json.
    Leitet den Benutzer anschließend zurück zum CCP.
    """
    try:
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

    return "idle"


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
        state["phase"] = "voting"
        _atomic_write_pairings(state)
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"
        _atomic_write_json(FILE_PATH, raffle_list)
        return {"ok": True, "action": "ended_play_phase", "active_round": active, "phase": "voting"}

    # If already beyond last configured round -> end.
    if active >= len(rounds):
        state["phase"] = "voting"
        _atomic_write_pairings(state)
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"
        _atomic_write_json(FILE_PATH, raffle_list)
        return {"ok": True, "action": "ended_play_phase", "active_round": active, "phase": "voting"}

    # Normal round progression for rounds 1..3
    if active < 4:
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

            created_entries: list[dict] = []
            seen_card_ids: set[str] = set()

            for deck_id, deckersteller in zip(deck_ids, selected_names):
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
                    "commander2": None,
                    "commander2_id": None,
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
        # Idle (nothing to do yet)
        # -------------------------
        return {
            "ok": True,
            "phase": phase,
            "action": "noop",
            "message": "Kein weiterer Debug-Schritt definiert (aktuell: Registrierung → Raffle-Start → Bestätigen → Pairings/Runden → Voting).",
        }


register_debug_routes(app, _debug_apply_step, notify_state_change)

@app.get("/CCP", response_class=HTMLResponse)
async def customer_control_panel(request: Request):
    """
    Zeigt die Customer Control Panel Seite an, überprüft den Status von start.txt und raffle.json.
    """
    start_file_exists = START_FILE_PATH.exists()
    settings = _current_settings()

    deck_count = -1

    # Behalte deckersteller-Liste explizit (für "vor Start"-UI)
    deckersteller = []

    # Neu für "nach Start"-UI
    tooltip_items = []
    confirmed_count = 0
    total_decks = 0

    raffle_list = _load_raffle_list()
    all_confirmed = _all_received_confirmed(raffle_list) if start_file_exists else False
    pair = _load_pairings() or {}
    pairings_phase = pair.get("phase") or ("ready" if all_confirmed else None)
    active_round = int(pair.get("active_round") or 0) if pair else 0
    pairings_started = bool(pair) and active_round > 0

    # für Host-Auswahl (nur sinnvoll nach Start + alle bestätigt)
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
                        # NACH Start: Owners + Status
                        total_decks = len([e for e in content if e.get("deck_id") is not None])
                        deck_count = total_decks  # Y

                        confirmed_count = sum(1 for e in content if e.get("received_confirmed") is True)  # X

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
                        # VOR Start: Deckersteller wie bisher
                        deckersteller = sorted({
                            entry.get("deckersteller")
                            for entry in content
                            if entry.get("deckersteller")
                        })
                        deck_count = len(deckersteller)

                        # optional: tooltip_items trotzdem füllen, falls du im Template vereinheitlichen willst
                        tooltip_items = [{"name": n, "received_confirmed": False} for n in deckersteller]
                        confirmed_count = 0

        except (json.JSONDecodeError, ValueError):
            pass

    return templates.TemplateResponse(
        "CustomerControlPanel.html",
        {
            "request": request,
            "start_file_exists": start_file_exists,

            # alt (bleibt erhalten)
            "deck_count": deck_count,
            "deckersteller": deckersteller,

            # neu
            "confirmed_count": confirmed_count,
            "tooltip_items": tooltip_items,

            "all_confirmed": all_confirmed,
            "pairings_started": pairings_started,
            "pairings_phase": pairings_phase,
            "active_round": active_round,
            "players": players,
            "selected_hosts": selected_hosts,
            "round_tables": round_tables,
            "default_num_pods": settings.default_num_pods,
            "min_decks_to_start": settings.min_decks_to_start,
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

@app.get("/api/background/commander")
async def background_commander(name: str = ""):
    name = (name or "").strip()
    if not name:
        return JSONResponse({"url": None, "zoom": _current_settings().ui.commander_bg_zoom})

    # exakt (mit Escape für Quotes)
    safe = name.replace('"', '\\"')
    q = f'game:paper is:commander !"{safe}"'

    url = (
        f"{SCRYFALL_BASE}/cards/search?"
        f"q={quote_plus(q)}&unique=prints&order=released&dir=desc"
    )

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return JSONResponse({"url": None, "zoom": _current_settings().ui.commander_bg_zoom})

            data = (r.json().get("data") or [])
            if not data:
                return JSONResponse({"url": None, "zoom": _current_settings().ui.commander_bg_zoom})

            newest = data[0]
            img = _get_image_url(newest, "border_crop")
            # Fallback, falls border_crop fehlt
            if not img:
                img = _get_image_url(newest, "large")
            return JSONResponse({"url": img, "zoom": _current_settings().ui.commander_bg_zoom})

    except Exception:
        return JSONResponse({"url": None, "zoom": _current_settings().ui.commander_bg_zoom})


def _best_deck_votes_bucket(state: dict) -> dict:
    votes = state.setdefault("best_deck_votes", {})
    if not isinstance(votes, dict):
        votes = {}
        state["best_deck_votes"] = votes
    return votes


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
        })

    return sorted(candidates, key=lambda item: item["deck_id"])


@app.get("/api/voting/best-deck/current")
async def current_best_deck_voting(deck_id: int):
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

    votes = (state.get("best_deck_votes") or {}).get(str(deck_id)) or {}

    placements = {"1": None, "2": None, "3": None}
    for place in ["1", "2", "3"]:
        val = votes.get(place)
        if isinstance(val, int):
            placements[place] = val

    return {
        "phase_title": "Best-Deck-Voting",
        "candidates": candidates,
        "placements": placements,
        "has_vote": all(bool(placements[k]) for k in ["1", "2", "3"]),
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

        votes = _best_deck_votes_bucket(state)
        key = str(deck_id)
        if votes.get(key):
            raise HTTPException(status_code=409, detail="Best-Deck-Voting wurde bereits bestätigt.")

        candidates = _best_deck_candidates_for_owner(raffle_list, owner_name)
        candidate_ids = {int(item["deck_id"]) for item in candidates}

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

        votes[key] = {
            "1": normalized["1"],
            "2": normalized["2"],
            "3": normalized["3"],
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

        if active >= len(rounds):
            # keine nächste Runde mehr -> bleibt bei letzter Runde, oder du könntest automatisch voting setzen
            return RedirectResponse(url="/CCP", status_code=303)

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

        state["phase"] = "voting"
        _atomic_write_pairings(state)

        # optional: in raffle.json markieren
        raffle_list = _load_raffle_list()
        for e in raffle_list:
            if e.get("deck_id") is not None:
                e["pairing_phase"] = "voting"
        _atomic_write_json(FILE_PATH, raffle_list)

    await notify_state_change()
    return RedirectResponse(url="/CCP", status_code=303)

if __name__ == "__main__":
    uvicorn.run('main:app', port=8080, host="0.0.0.0", reload=True)
