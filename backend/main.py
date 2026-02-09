import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from backend.schemas import DeckSchema
import json
import hashlib
import asyncio
from pathlib import Path
import pandas as pd
from random import shuffle, randint, choice
import time 
import re 
from collections import OrderedDict
from urllib.parse import quote_plus, unquote_plus
import httpx
import os
#python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# --- Scryfall commander suggest config ---
SCRYFALL_BASE = "https://api.scryfall.com"
SUGGEST_MIN_CHARS = 3          # 2 oder 3 – du wolltest 2–3; Default: 3
SUGGEST_LIMIT = 15             # Smartphone-freundlich
SCRYFALL_TIMEOUT = 2.0         # Sekunden

CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ENTRIES = 1000

# Default Background: Snow Basics aus Secret Lair Drop (dein Query)
DEFAULT_BG_QUERY = 't:basic t:snow e:SLD'
DEFAULT_BG_ZOOM = 1.12   # <- “Rand weg gecropped” zusätzlich per Zoom (einstellbar)
COMMANDER_BG_ZOOM = 1.00 # <- kein zusätzlicher Zoom

SCRYFALL_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "CommanderRaffle/1.0 (contact: you@example.com)",
}

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
    # key: "art_crop", "border_crop", "large", ...
    iu = card.get("image_uris")
    if isinstance(iu, dict) and iu.get(key):
        return iu[key]

    faces = card.get("card_faces")
    if isinstance(faces, list) and len(faces) > 0:
        fu = faces[0].get("image_uris")
        if isinstance(fu, dict) and fu.get(key):
            return fu[key]

    return None


# JSON-Datei
FILE_PATH = Path("raffle.json")

PAIRINGS_PATH = Path("pairings.json")
MAX_ROUNDS = 7

# Serialisiert alle Zugriffe auf raffle.json innerhalb dieses Prozesses
RAFFLE_LOCK = asyncio.Lock()

def _atomic_write_json(path: Path, data) -> None:
    """
    Schreibt JSON atomisch:
    - erst in temp-Datei schreiben
    - dann os.replace (atomarer rename) auf Ziel
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp_path, path)

# FastAPI-App erstellen
app = FastAPI()
app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Optional: lokale Assets (z.B. assets/backgrounds/*.png)
_assets_dir = Path("assets")
if _assets_dir.exists() and _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# Templates für HTML-Seiten
templates = Jinja2Templates(directory="frontend")

# =========================================================
# WebSocket live updates (no polling)
# =========================================================

class WSManager:
    """
    Groups:
      - "ccp"  : Customer Control Panel (reload on any relevant change)
      - "home" : "/" (reload on any relevant change)
      - "deck:<id>" : "/?deck_id=<id>" (reload only if that deck's state changes)
    """
    def __init__(self):
        self.groups: dict[str, set[WebSocket]] = {
            "ccp": set(),
            "home": set(),
            # "deck:<id>": set()
        }

    def connect_existing(self, ws: WebSocket, group: str):
        if group not in self.groups:
            self.groups[group] = set()
        self.groups[group].add(ws)


    def disconnect(self, ws: WebSocket, group: str):
        if group in self.groups:
            self.groups[group].discard(ws)
            # optional cleanup empty deck groups
            if group.startswith("deck:") and len(self.groups[group]) == 0:
                self.groups.pop(group, None)

    def active_deck_ids(self) -> set[int]:
        ids = set()
        for k in self.groups.keys():
            if k.startswith("deck:"):
                try:
                    ids.add(int(k.split(":", 1)[1]))
                except ValueError:
                    pass
        return ids

    async def broadcast_group(self, group: str, payload: dict):
        conns = list(self.groups.get(group, set()))
        if not conns:
            return
        dead = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        # remove dead sockets
        for ws in dead:
            self.groups.get(group, set()).discard(ws)

ws_manager = WSManager()

_last_global_sig: str | None = None
_last_deck_sig: dict[int, str] = {}

def _load_raffle_list() -> list[dict]:
    if not FILE_PATH.exists():
        return []
    try:
        with FILE_PATH.open("r", encoding="utf-8") as f:
            content = json.load(f)
            if isinstance(content, list):
                return content
            if isinstance(content, dict):
                return [content]
    except (json.JSONDecodeError, ValueError):
        pass
    return []

def _load_pairings() -> dict | None:
    if not PAIRINGS_PATH.exists():
        return None
    try:
        with PAIRINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _atomic_write_pairings(data: dict) -> None:
    _atomic_write_json(PAIRINGS_PATH, data)


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

from itertools import combinations

def _pod_sizes(n_players: int, num_pods: int) -> list[int]:
    # Gleichmäßig verteilen: z.B. 7 Spieler, 2 Pods => [4,3]
    k = max(1, int(num_pods))
    k = min(k, n_players)
    base = n_players // k
    rest = n_players % k
    sizes = [base + (1 if i < rest else 0) for i in range(k)]
    sizes.sort(reverse=True)
    return sizes

def _pairs_in_group(group: list[int]) -> list[tuple[int,int]]:
    g = sorted(group)
    return [(g[i], g[j]) for i in range(len(g)) for j in range(i+1, len(g))]

def _counts_key(counts: list[list[int]]) -> tuple:
    # upper triangle flatten
    n = len(counts)
    out = []
    for i in range(n):
        for j in range(i+1, n):
            out.append(counts[i][j])
    return tuple(out)

def _counts_from_key(key: tuple, n: int) -> list[list[int]]:
    counts = [[0]*n for _ in range(n)]
    idx = 0
    for i in range(n):
        for j in range(i+1, n):
            v = int(key[idx])
            idx += 1
            counts[i][j] = v
            counts[j][i] = v
    return counts

def _missing_pairs(counts: list[list[int]]) -> int:
    n = len(counts)
    m = 0
    for i in range(n):
        for j in range(i+1, n):
            if counts[i][j] == 0:
                m += 1
    return m

def _max_count(counts: list[list[int]]) -> int:
    n = len(counts)
    mx = 0
    for i in range(n):
        for j in range(i+1, n):
            if counts[i][j] > mx:
                mx = counts[i][j]
    return mx

def _sum_sq(counts: list[list[int]]) -> int:
    n = len(counts)
    s = 0
    for i in range(n):
        for j in range(i+1, n):
            v = counts[i][j]
            s += v*v
    return s

def _apply_partition(counts: list[list[int]], pods: list[list[int]]) -> list[list[int]]:
    n = len(counts)
    newc = [row[:] for row in counts]
    for pod in pods:
        for (i, j) in _pairs_in_group(pod):
            newc[i][j] += 1
            newc[j][i] += 1
    return newc

def _gen_partitions(indices: list[int], sizes: list[int]) -> list[list[list[int]]]:
    """
    Erzeugt alle Partitionen von indices in Gruppen der gegebenen sizes.
    Größen sind z.B. [4,4] oder [4,3] etc.
    Für n<=8 ist das gut machbar.
    """
    sizes = list(sizes)
    sizes.sort(reverse=True)

    res = []
    indices = sorted(indices)

    def rec(remaining: list[int], si: int, acc: list[list[int]]):
        if si >= len(sizes):
            if not remaining:
                res.append([g[:] for g in acc])
            return
        size = sizes[si]
        if len(remaining) < size:
            return

        # symmetry breaking: first element in remaining must be in the next group
        first = remaining[0]
        for comb in combinations(remaining[1:], size-1):
            group = [first] + list(comb)
            group_set = set(group)
            new_remaining = [x for x in remaining if x not in group_set]
            acc.append(sorted(group))
            rec(new_remaining, si+1, acc)
            acc.pop()

    rec(indices, 0, [])
    return res

def _build_rounds(players: list[str], num_pods: int, max_rounds: int = MAX_ROUNDS) -> list[list[list[str]]]:
    """
    1) BFS: finde minimale Rundenzahl, bis alle Paare mindestens einmal zusammen gespielt haben.
    2) Danach greedily weitere Runden bis max_rounds zum Ausgleich (min max_count, min sum_sq).
    """
    n = len(players)
    sizes = _pod_sizes(n, num_pods)
    partitions = _gen_partitions(list(range(n)), sizes)

    # BFS nach minimaler Tiefe, die missing_pairs == 0 erreicht
    start_counts = [[0] * n for _ in range(n)]
    start_key = _counts_key(start_counts)

    from collections import deque

    best_depth_solution = None  # (depth, path_partitions_as_indices, counts_key)
    visited = set([(start_key, 0)])

    q = deque()
    q.append((start_key, 0, []))  # counts_key, depth, path

    target_depth = None

    while q:
        key, depth, path = q.popleft()
        counts = _counts_from_key(key, n)

        miss = _missing_pairs(counts)
        if miss == 0:
            target_depth = depth
            best_depth_solution = (depth, path, key)
            break

        # nicht über max_rounds hinaus suchen
        if depth >= max_rounds:
            continue

        # expand
        best_candidates = []
        for pods in partitions:
            newc = _apply_partition(counts, pods)
            newkey = _counts_key(newc)
            # prune by visited at (newkey, depth+1)
            st = (newkey, depth + 1)
            if st in visited:
                continue
            visited.add(st)

            cost = (_missing_pairs(newc), _max_count(newc), _sum_sq(newc))
            best_candidates.append((cost, pods, newkey))

        # sort to guide BFS "best-first inside same depth"
        best_candidates.sort(key=lambda x: x[0])

        # push some best options (n small; keep all is ok, but limit to keep it snappy)
        for (cost, pods, newkey) in best_candidates[:60]:
            q.append((newkey, depth + 1, path + [pods]))

    if best_depth_solution is None:
        # Fallback: greedy build max_rounds
        rounds_idx = []
        counts = start_counts
        for _ in range(max_rounds):
            best = None
            for pods in partitions:
                newc = _apply_partition(counts, pods)
                cost = (_missing_pairs(newc), _max_count(newc), _sum_sq(newc))
                if best is None or cost < best[0]:
                    best = (cost, pods, newc)
            rounds_idx.append(best[1])
            counts = best[2]
    else:
        depth, rounds_idx, key = best_depth_solution
        counts = _counts_from_key(key, n)

        # fill remaining rounds greedily for balancing
        while len(rounds_idx) < max_rounds:
            best = None
            for pods in partitions:
                newc = _apply_partition(counts, pods)
                # primary now: minimize max_count, then sum_sq, keep missing already 0
                cost = (_max_count(newc), _sum_sq(newc))
                if best is None or cost < best[0]:
                    best = (cost, pods, newc)
            rounds_idx.append(best[1])
            counts = best[2]

    # convert indices -> names
    rounds_named = []
    for pods in rounds_idx:
        round_pods = []
        for pod in pods:
            round_pods.append([players[i] for i in pod])
        rounds_named.append(round_pods)
    return rounds_named

def _apply_round_to_raffle(raffle_list: list[dict], state: dict, round_no: int):
    """
    Schreibt in jede raffle.json-Entry:
      - pairing_round
      - pairing_table
      - pairing_players (Liste der Spielernamen am Tisch)
      - pairing_phase
    Mapping erfolgt über deckOwner (Spieleridentität).
    """
    rounds = state.get("rounds") or []
    phase = state.get("phase") or "ready"
    if round_no < 1 or round_no > len(rounds):
        return

    pods = rounds[round_no - 1]  # 0-indexed
    # player -> (table_no, players_at_table)
    assign = {}
    for t, group in enumerate(pods, start=1):
        for p in group:
            assign[p] = (t, group)

    for e in raffle_list:
        if e.get("deck_id") is None:
            continue
        owner = (e.get("deckOwner") or "").strip()
        if not owner:
            continue
        if owner in assign:
            t, group = assign[owner]
            e["pairing_round"] = round_no
            e["pairing_table"] = t
            e["pairing_players"] = group
        else:
            # falls irgendwas nicht zuordenbar ist
            e["pairing_round"] = round_no
            e["pairing_table"] = None
            e["pairing_players"] = []
        e["pairing_phase"] = phase

def _global_signature(start_file_exists: bool, raffle_list: list[dict]) -> str:
    deck_ids = {e.get("deck_id") for e in raffle_list if "deck_id" in e}
    confirmed = 0
    total = 0
    for e in raffle_list:
        if "deck_id" in e:
            total += 1
            if e.get("received_confirmed") is True:
                confirmed += 1

    pair = _load_pairings() or {}
    obj = {
        "start_file_exists": start_file_exists,
        "deck_count": len(deck_ids),
        "total_decks": total,
        "confirmed_count": confirmed,
        "pairings_phase": pair.get("phase"),
        "active_round": pair.get("active_round"),
    }
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

def _deck_signature(deck_id: int, start_file_exists: bool, raffle_list: list[dict]) -> str:
    entry = None
    for e in raffle_list:
        if e.get("deck_id") == deck_id:
            entry = e
            break

    registered = entry is not None
    deck_owner = entry.get("deckOwner") if entry else None

    # This captures exactly what can change the rendered page for that deck_id:
    # - raffle started or not
    # - whether this deck_id is registered
    # - who the deckOwner is (after start)
    received_confirmed = entry.get("received_confirmed") if entry else None

    obj = {
        "deck_id": deck_id,
        "start_file_exists": start_file_exists,
        "registered": registered,
        "deckOwner": deck_owner,
        "received_confirmed": received_confirmed,
        "pairing_round": entry.get("pairing_round") if entry else None,
        "pairing_table": entry.get("pairing_table") if entry else None,
        "pairing_phase": entry.get("pairing_phase") if entry else None,
    }
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

async def notify_state_change():
    """
    Called after any write to raffle.json or start.txt.
    Sends WS events only to groups whose signature changed.
    """
    global _last_global_sig, _last_deck_sig

    start_file_exists = Path("start.txt").exists()
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
    participants_file = Path("teilnehmer.txt")
    if participants_file.exists():
        with participants_file.open("r", encoding="utf-8") as f:
            participants = [line.strip() for line in f.readlines() if line.strip()]  # Entferne leere Zeilen

    # Status von start.txt prüfen
    start_file_exists = Path("start.txt").exists()

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
    card_id = (card_id or "").strip()
    if not card_id:
        return None
    url = f"{SCRYFALL_BASE}/cards/{quote_plus(card_id)}"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception:
        return None

async def _scryfall_random_commander(exclude_card_ids: set[str] | None = None, max_tries: int = 25) -> dict | None:
    """
    Picks a random commander card from Scryfall.
    Returns the full card JSON or None.

    exclude_card_ids: avoids duplicates by Scryfall card id
    """
    exclude_card_ids = exclude_card_ids or set()

    # Avoid Backgrounds (they are not valid "single commander" in your UI logic anyway)
    # Keep it simple: random commander-legal card, paper, not background
    scry_q = "game:paper is:commander -t:background"
    url = f"{SCRYFALL_BASE}/cards/random?q={quote_plus(scry_q)}"

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            for _ in range(max_tries):
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                card = r.json()
                cid = (card.get("id") or "").strip()
                name = (card.get("name") or "").strip()
                if not cid or not name:
                    continue
                if cid in exclude_card_ids:
                    continue
                return card
    except Exception:
        return None

    return None

def _t(card: dict) -> str:
    return (card.get("type_line") or "").lower()


def _o(card: dict) -> str:
    # oracle_text can be empty for some layouts; that's fine for our checks
    return (card.get("oracle_text") or "").lower()


def _is_background(card: dict) -> bool:
    return "background" in _t(card)


def _has_choose_a_background(card: dict) -> bool:
    return "choose a background" in _o(card)


def _has_friends_forever(card: dict) -> bool:
    return "friends forever" in _o(card)


def _partner_with_target_name(card: dict) -> str | None:
    # canonical oracle text: "Partner with <Name>"
    m = re.search(r"partner with ([^\n\(]+)", card.get("oracle_text") or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


async def _scryfall_is_partner_exact_name(name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    scry_q = f'!"{name}" is:partner'
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return False
            payload = r.json()
            total = int(payload.get("total_cards") or 0)
            return total > 0
    except Exception:
        return False


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
        if Path("start.txt").exists():
            Path("start.txt").unlink()
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

@app.post("/debug")
async def debug_fill_decks():
    """
    DEV-Helfer: Befüllt testweise deck_ids 1..8 mit zufälligen Commandern via Scryfall
    und nutzt dafür die Namen aus teilnehmer.txt als deckersteller.

    - überschreibt nur deck_ids 1..8 (andere Einträge bleiben erhalten)
    - commander2 bleibt leer (None)
    - received_confirmed = False
    - deckOwner = None
    """
    participants_file = Path("teilnehmer.txt")
    if not participants_file.exists():
        raise HTTPException(status_code=400, detail="teilnehmer.txt nicht gefunden.")

    with participants_file.open("r", encoding="utf-8") as f:
        names = [line.strip() for line in f.readlines() if line.strip()]

    if len(names) < 8:
        raise HTTPException(
            status_code=400,
            detail=f"teilnehmer.txt enthält nur {len(names)} Namen, benötigt werden mindestens 8."
        )

    # randomize which participant gets which deck_id
    shuffle(names)
    selected_names = names[:8]
    deck_ids = list(range(1, 9))

    created_entries: list[dict] = []
    seen_card_ids: set[str] = set()

    # Pull 8 random commanders from Scryfall
    for deck_id, deckersteller in zip(deck_ids, selected_names):
        card = await _scryfall_random_commander(exclude_card_ids=seen_card_ids)
        if not card:
            raise HTTPException(status_code=502, detail="Konnte keinen zufälligen Commander von Scryfall laden.")

        commander_name = card.get("name")
        commander_id = card.get("id")
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
            "received_confirmed": False,
        })

    # Write atomically + serialized (consistent with /submit)
    async with RAFFLE_LOCK:
        existing = _load_raffle_list()
        existing = [e for e in existing if e.get("deck_id") not in deck_ids]
        existing.extend(created_entries)
        _atomic_write_json(FILE_PATH, existing)

    await notify_state_change()

    return JSONResponse({
        "ok": True,
        "filled_deck_ids": deck_ids,
        "created": created_entries,
    })

@app.get("/CCP", response_class=HTMLResponse)
async def customer_control_panel(request: Request):
    """
    Zeigt die Customer Control Panel Seite an, überprüft den Status von start.txt und raffle.json.
    """
    start_file_exists = Path("start.txt").exists()

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
        }
    )

@app.post("/startRaffle")
async def start_raffle():
    """
    Führt den Raffle-Start durch und leitet den Benutzer zurück zum CCP.
    """
    try:
         # Leere start.txt erstellen
        start_file = Path("start.txt")
        with start_file.open("w", encoding="utf-8") as f:
            f.write("")  # Leere Datei erstellen
        # Aktionen für den Raffle-Start (optional: hier Platz für Logik)
        
        deckersteller_list = []
        if FILE_PATH.exists():
            try:
                with FILE_PATH.open("r", encoding="utf-8") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        # Sammle alle Deckersteller
                        deckersteller_list = [entry.get("deckersteller") for entry in content if "deckersteller" in entry]
            except (json.JSONDecodeError, ValueError):
                # Fehler beim Einlesen von raffle.json
                raise HTTPException(status_code=500, detail="Fehler beim Einlesen der raffle.json-Datei.")
                # Mindestanzahl Decks prüfen
        unique_decks = list(dict.fromkeys(deckersteller_list))  # dedupe, Reihenfolge egal
        if len(unique_decks) < 3:
            raise HTTPException(
                status_code=400,
                detail="Raffle kann erst ab 3 registrierten Decks gestartet werden."
            )
        cOrder, gOrder=shuffle_decks( unique_decks )
        for creator, new_owner in zip( cOrder, gOrder ):
            update_deck_owner( creator, new_owner )

        await notify_state_change()

        return RedirectResponse(url="/CCP", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Starten des Raffles: {e}")

def shuffle_decks(deckCreators):
    creatorOrder = deckCreators[:]
    giftOrder = deckCreators[:]
    shuffleCount = 0

    # Shuffle, bis kein Deckersteller sein eigenes Deck erhält
    while any([i == j for i, j in zip(giftOrder, creatorOrder)]):
        shuffle(creatorOrder)
        shuffle(giftOrder)
        shuffleCount += 1
        print('Shuffle Count is {}'.format(shuffleCount))
    else:
        return giftOrder, creatorOrder

def update_deck_owner(deckersteller, new_deck_owner):
    """
    Aktualisiert das Feld 'deckOwner' für einen bestimmten 'deckersteller' in der raffle.json.
    """
    try:
        # Prüfen, ob die Datei existiert
        if not FILE_PATH.exists():
            print("Die Datei raffle.json existiert nicht.")
            return

        # Datei einlesen
        with FILE_PATH.open("r", encoding="utf-8") as f:
            content = json.load(f)

        # Sicherstellen, dass der Inhalt eine Liste ist
        if not isinstance(content, list):
            print("Ungültiges Format in raffle.json: Erwartet wird eine Liste.")
            return

        # Den Eintrag für den angegebenen deckersteller finden
        entry_found = False
        for entry in content:
            if entry.get("deckersteller") == deckersteller:
                entry["deckOwner"] = new_deck_owner  # Feld aktualisieren
                entry["received_confirmed"] = False
                entry_found = True
                break

        if not entry_found:
            print(f"Kein Eintrag für den Deckersteller '{deckersteller}' gefunden.")
            return

        # Aktualisierte Daten zurück in die Datei schreiben
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=4)

        print(f"Der Eintrag für '{deckersteller}' wurde erfolgreich aktualisiert.")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Fehler beim Einlesen der raffle.json: {e}")
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")

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
    if len(q) < SUGGEST_MIN_CHARS:
        return JSONResponse([])

    key = f"cmd::{q.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return JSONResponse(cached)

    scry_q = f"game:paper is:commander name:{q}"
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
                if len(items) >= SUGGEST_LIMIT:
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
    if len(q) < SUGGEST_MIN_CHARS:
        return JSONResponse([])

    key = f"partner::{q.lower()}"
    cached = _cache_get(key)
    if cached is not None:
        return JSONResponse(cached)

    scry_q = f"game:paper is:partner name:{q}"
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
                if len(items) >= SUGGEST_LIMIT:
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
    name = (name or "").strip()
    if not name:
        return JSONResponse({"partner_capable": False})

    # exact name match + is:partner
    # Scryfall exact-name search uses !"Card Name"
    scry_q = f'!"{name}" is:partner'
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards"

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return JSONResponse({"partner_capable": False})

            payload = r.json()
            total = payload.get("total_cards") or 0
            return JSONResponse({"partner_capable": bool(total)})

    except Exception:
        return JSONResponse({"partner_capable": False})

async def _scryfall_named_exact(name: str) -> dict | None:
    """
    Resolve a card by exact name via Scryfall.
    Returns card JSON or None.
    """
    name = (name or "").strip()
    if not name:
        return None

    url = f"{SCRYFALL_BASE}/cards/named?exact={quote_plus(name)}"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception:
        return None


def _type_line(card: dict) -> str:
    return (card.get("type_line") or "").lower()


def _oracle_text(card: dict) -> str:
    return (card.get("oracle_text") or "").lower()


def _is_background(card: dict) -> bool:
    # Background is a subtype; appears in type_line like "Enchantment — Background"
    return "background" in _type_line(card)


def _has_choose_a_background(card: dict) -> bool:
    return "choose a background" in _oracle_text(card)


def _has_friends_forever(card: dict) -> bool:
    return "friends forever" in _oracle_text(card)


def _partner_with_target_name(card: dict) -> str | None:
    # canonical oracle text: "Partner with <Name>"
    m = re.search(r"partner with ([^\n\(]+)", card.get("oracle_text") or "", flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


async def _scryfall_is_partner_exact_name(name: str) -> bool:
    """
    True if Scryfall search finds exact name and is:partner.
    """
    name = (name or "").strip()
    if not name:
        return False

    scry_q = f'!"{name}" is:partner'
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return False
            payload = r.json()
            total = int(payload.get("total_cards") or 0)
            return total > 0
    except Exception:
        return False

@app.get("/api/background/default")
async def background_default():
    """
    Default Background:
    - Prefer local PNG from assets/backgrounds/ (random choice)
    - Fallback: current Scryfall-based default
    """
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
                    {"url": f"/assets/backgrounds/{picked.name}", "zoom": DEFAULT_BG_ZOOM}
                )
    except Exception:
        # any filesystem weirdness -> fall back to Scryfall
        pass

    # 2) Fallback: Scryfall default (existing logic)
    q = DEFAULT_BG_QUERY
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(q)}&unique=cards&order=name"

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            first = await client.get(url)
            if first.status_code != 200:
                return JSONResponse({"url": None, "zoom": DEFAULT_BG_ZOOM})

            payload = first.json()
            total = int(payload.get("total_cards") or 0)
            if total <= 0:
                return JSONResponse({"url": None, "zoom": DEFAULT_BG_ZOOM})

            per_page = len(payload.get("data") or [])
            if per_page <= 0:
                return JSONResponse({"url": None, "zoom": DEFAULT_BG_ZOOM})

            max_page = max(1, (total + per_page - 1) // per_page)
            page = randint(1, max_page)

            page_url = url + f"&page={page}"
            resp = await client.get(page_url)
            if resp.status_code != 200:
                return JSONResponse({"url": None, "zoom": DEFAULT_BG_ZOOM})

            data = (resp.json().get("data") or [])
            if not data:
                return JSONResponse({"url": None, "zoom": DEFAULT_BG_ZOOM})

            card = data[randint(0, len(data) - 1)]
            img = _get_image_url(card, "art_crop")
            return JSONResponse({"url": img, "zoom": DEFAULT_BG_ZOOM})

    except Exception:
        return JSONResponse({"url": None, "zoom": DEFAULT_BG_ZOOM})

@app.get("/api/background/commander")
async def background_commander(name: str = ""):
    name = (name or "").strip()
    if not name:
        return JSONResponse({"url": None, "zoom": COMMANDER_BG_ZOOM})

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
                return JSONResponse({"url": None, "zoom": COMMANDER_BG_ZOOM})

            data = (r.json().get("data") or [])
            if not data:
                return JSONResponse({"url": None, "zoom": COMMANDER_BG_ZOOM})

            newest = data[0]
            img = _get_image_url(newest, "border_crop")
            # Fallback, falls border_crop fehlt
            if not img:
                img = _get_image_url(newest, "large")
            return JSONResponse({"url": img, "zoom": COMMANDER_BG_ZOOM})

    except Exception:
        return JSONResponse({"url": None, "zoom": COMMANDER_BG_ZOOM})

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """
    Client connects with:
      - /ws?channel=ccp
      - /ws?channel=home
      - /ws?deck_id=<int>
    IMPORTANT: accept() MUST happen before any other logic, otherwise Starlette returns 403.
    """
    await websocket.accept()  # <- CRITICAL: accept immediately

    # Now it's safe to parse params / assign groups without risking 403.
    q = websocket.query_params
    channel = (q.get("channel") or "").strip().lower()
    deck_id_raw = (q.get("deck_id") or "").strip()

    group = "home"
    deck_id = None

    if channel == "ccp":
        group = "ccp"
    elif channel == "home":
        group = "home"
    elif deck_id_raw:
        try:
            deck_id = int(deck_id_raw)
            group = f"deck:{deck_id}"
        except ValueError:
            group = "home"

    # register socket into group (NO accept here!)
    ws_manager.connect_existing(websocket, group)

    # send initial signature (baseline)
    try:
        start_file_exists = Path("start.txt").exists()
        raffle_list = _load_raffle_list()

        if group in ("ccp", "home"):
            sig = _global_signature(start_file_exists, raffle_list)
            await websocket.send_json({"type": "hello", "scope": "global", "signature": sig})
        else:
            sig = _deck_signature(deck_id, start_file_exists, raffle_list)  # deck_id is int
            await websocket.send_json({"type": "hello", "scope": "deck", "deck_id": deck_id, "signature": sig})

        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        pass
    except Exception:
        # keep it quiet; optionally log
        pass
    finally:
        ws_manager.disconnect(websocket, group)

@app.post("/startPairings")
async def start_pairings(num_pods: int = Form(...)):
    async with RAFFLE_LOCK:
        raffle_list = _load_raffle_list()

        if not Path("start.txt").exists():
            raise HTTPException(status_code=400, detail="Raffle noch nicht gestartet.")

        if not _all_received_confirmed(raffle_list):
            raise HTTPException(status_code=400, detail="Nicht alle Decks wurden bestätigt.")

        players = _deckowners(raffle_list)
        if len(players) < 3:
            raise HTTPException(status_code=400, detail="Zu wenige Spieler.")

        rounds = _build_rounds(players, int(num_pods), MAX_ROUNDS)

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