import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from backend.schemas import DeckSchema
import json
from pathlib import Path
import pandas as pd
from random import shuffle
import time 
import re 
from collections import OrderedDict
from urllib.parse import quote_plus
import httpx
#python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# --- Scryfall commander suggest config ---
SCRYFALL_BASE = "https://api.scryfall.com"
SUGGEST_MIN_CHARS = 3          # 2 oder 3 – du wolltest 2–3; Default: 3
SUGGEST_LIMIT = 10             # Smartphone-freundlich
SCRYFALL_TIMEOUT = 2.0         # Sekunden

CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ENTRIES = 1000

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

# JSON-Datei
FILE_PATH = Path("raffle.json")

# FastAPI-App erstellen
app = FastAPI()

# Templates für HTML-Seiten
templates = Jinja2Templates(directory="frontend")


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
                if isinstance(content, list):  # Wenn raffle.json eine Liste ist
                    for entry in content:
                        if entry.get("deck_id") == deck_id:
                            existing_entry = entry
                            deckOwner = entry.get("deckOwner")  # Wert für deckOwner laden
                            break
        except (json.JSONDecodeError, ValueError):
            pass

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
        # Weiterleitung zurück zum Customer Control Panel
        return RedirectResponse(url="/CCP", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Löschen der Dateien: {e}")

@app.get("/CCP", response_class=HTMLResponse)
async def customer_control_panel(request: Request):
    """
    Zeigt die Customer Control Panel Seite an, überprüft den Status von start.txt und raffle.json.
    """
    # Prüfen, ob start.txt existiert
    start_file_exists = Path("start.txt").exists()

    # Prüfen, ob raffle.json existiert und Anzahl der DeckIDs ermitteln
    deck_count = -1
    if FILE_PATH.exists():
        try:
            with FILE_PATH.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    deck_count = len({entry.get("deck_id") for entry in content if "deck_id" in entry})
        except (json.JSONDecodeError, ValueError):
            pass

    return templates.TemplateResponse(
        "CustomerControlPanel.html",
        {
            "request": request,
            "start_file_exists": start_file_exists,
            "deck_count": deck_count,
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
        cOrder, gOrder=shuffle_decks( deckersteller_list )
        for creator, new_owner in zip( cOrder, gOrder ):
            update_deck_owner( creator, new_owner )

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



if __name__ == "__main__":
    uvicorn.run('main:app', port=8080, host="0.0.0.0", reload=True)
