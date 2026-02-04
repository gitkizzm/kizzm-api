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
from urllib.parse import quote_plus
import httpx
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

def _global_signature(start_file_exists: bool, raffle_list: list[dict]) -> str:
    deck_ids = {e.get("deck_id") for e in raffle_list if "deck_id" in e}
    obj = {
        "start_file_exists": start_file_exists,
        "deck_count": len(deck_ids),
        # optional: could add more global UI-relevant fields
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
    obj = {
        "deck_id": deck_id,
        "start_file_exists": start_file_exists,
        "registered": registered,
        "deckOwner": deck_owner,
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
async def get_form(request: Request, deck_id: int = 0):
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
                "error": (
                    "Der Raffle wurde schon gestartet, "
                    "diese Deck ID wurde aber nicht registriert. "
                    "Bitte sprich mit dem Host."
                ),
                "participants": [],
            }
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "deck_id": deck_id,
            "start_file_exists": start_file_exists,
            "existing_entry": existing_entry,  # Übergebe den Datensatz oder None
            "deckOwner": deckOwner,  # Übergebe den deckOwner
            "participants": participants,  # Übergabe der Teilnehmernamen
        }
    )

@app.post("/submit", response_class=HTMLResponse)
async def submit_form(
    request: Request,
    deckersteller: str = Form(...),
    commander: str = Form(None),
    deckUrl: str = Form(None),
    deck_id: int = Form(...)
):
    """
    Verarbeitet das Formular, prüft die DeckID und den Deckersteller, und fügt neue Datensätze hinzu.
    """
    try:
        # Konvertiere leere Strings zu None
        deckUrl = deckUrl or None
        commander = commander or None

        # Laden bestehender Daten
        data_list = []
        if FILE_PATH.exists():
            try:
                with FILE_PATH.open("r", encoding="utf-8") as f:
                    content = json.load(f)
                    # Sicherstellen, dass der Inhalt eine Liste ist
                    if isinstance(content, list):
                        data_list = content
                    else:
                        data_list = [content]  # Einzelnes Objekt in eine Liste umwandeln
            except (json.JSONDecodeError, ValueError):
                # Wenn die Datei leer oder ungültig ist, mit leerer Liste fortfahren
                data_list = []

        # Prüfen, ob der Deckersteller bereits existiert
        for entry in data_list:
            if entry.get("deckersteller") == deckersteller:
                # Fehler: Deckersteller existiert bereits (Tooltip anzeigen)
                return templates.TemplateResponse(
                    "index.html",
                    {
                        "request": request,
                        "deck_id": deck_id,
                        "error": f"'{deckersteller}' hat bereits ein Deck registriert. Bitte überprüfe deine Namens auswahl",
                        "values": {"deckersteller": deckersteller, "commander": commander, "deckUrl": deckUrl},
                        "participants": [entry.get("deckersteller") for entry in data_list],
                    }
                )
            
        # Prüfen, ob die DeckID bereits existiert
        for entry in data_list:
            if entry.get("deck_id") == deck_id:
                # Fehler: Deck ID existiert bereits
                return templates.TemplateResponse(
                    "index.html",
                    {
                        "request": request,
                        "deck_id": deck_id,
                        "error": f"Diese Deck ID ist bereits registriert.",
                        "values": {"deckersteller": deckersteller, "commander": commander, "deckUrl": deckUrl},
                        "participants": [entry.get("deckersteller") for entry in data_list],
                    }
                )

        # Neuen Datensatz hinzufügen
        new_entry = DeckSchema(deckersteller=deckersteller, commander=commander, deckUrl=deckUrl)
        serializable_data = new_entry.dict()
        serializable_data['deckUrl'] = str(serializable_data['deckUrl']) if serializable_data['deckUrl'] else None
        serializable_data['deck_id'] = deck_id  # DeckID hinzufügen
        serializable_data['deckOwner'] = None
        data_list.append(serializable_data)

        # Daten zurück in die Datei schreiben
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data_list, f, ensure_ascii=False, indent=4)
        
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
        # Erstellen einer leeren raffle.json
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)

        await notify_state_change()

        # Weiterleitung zurück zum Customer Control Panel
        return RedirectResponse(url="/CCP", status_code=303)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Löschen der Dateien: {e}")

@app.get("/CCP", response_class=HTMLResponse)
async def customer_control_panel(request: Request):
    """
    Zeigt die Customer Control Panel Seite an, überprüft den Status von start.txt und raffle.json.
    """
    start_file_exists = Path("start.txt").exists()

    deck_count = -1
    deckersteller = []

    if FILE_PATH.exists():
        try:
            with FILE_PATH.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    deckersteller = sorted({
                        entry.get("deckersteller")
                        for entry in content
                        if entry.get("deckersteller")
                    })
                    deck_count = len(deckersteller)
        except (json.JSONDecodeError, ValueError):
            pass

    return templates.TemplateResponse(
        "CustomerControlPanel.html",
        {
            "request": request,
            "start_file_exists": start_file_exists,
            "deck_count": deck_count,
            "deckersteller": deckersteller,
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

@app.get("/api/commander_suggest")
async def commander_suggest(q: str = ""):
    """
    Returns up to 10 commander-legal (is:commander) paper card names matching prefix q.
    On any error: returns [].
    """
    q = (q or "").strip()
    if len(q) < SUGGEST_MIN_CHARS:
        return JSONResponse([])

    # Normalize cache key
    key = q.lower()
    cached = _cache_get(key)
    if cached is not None:
        return JSONResponse(cached)

    # Escape for regex inside name:/^.../i
    # re.escape makes it safe for regex special chars
    escaped = re.escape(q)

    # Build Scryfall query
    # Note: Scryfall search syntax allows regex in name:
    # scry_q = f"game:paper is:commander name:/^{escaped}/i"
    # Kein Regex: einfach name:<q> (Scryfall macht sinnvolles Matching)
    # Optional kannst du q noch in Anführungszeichen setzen, aber für Autocomplete ist ohne Quotes besser.
    scry_q = f"game:paper is:commander name:{q}"

    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards&order=name"

    headers = {
        "Accept": "application/json",
        # IMPORTANT: set a descriptive UA; adjust to your project name
        "User-Agent": "CommanderRaffle/1.0 (contact: kizzm-commanderraffle@tri-b-oon.de)",
    }

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=headers) as client:
            r = await client.get(url)
            if r.status_code != 200:
                # on any error (429, 5xx, etc.) -> return empty
                return JSONResponse([])

            payload = r.json()
            data = payload.get("data") or []
            # extract names
            names = []
            for card in data:
                name = card.get("name")
                if name:
                    names.append(name)
                if len(names) >= SUGGEST_LIMIT:
                    break

            # If nothing matches -> [] and cache it too (prevents hammering)
            _cache_set(key, names)
            return JSONResponse(names)

    except Exception:
        # timeout/network/json issues -> no suggestions
        return JSONResponse([])

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
            pngs = [p for p in bg_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
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

if __name__ == "__main__":
    uvicorn.run('main:app', port=8080, host="0.0.0.0", reload=True)
