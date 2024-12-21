import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from backend.schemas import DeckSchema
import json
from pathlib import Path
#python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

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
    if FILE_PATH.exists():
        try:
            with FILE_PATH.open("r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):  # Wenn raffle.json eine Liste ist
                    for entry in content:
                        if entry.get("deck_id") == deck_id:
                            existing_entry = entry
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
        # Aktionen für den Raffle-Start (optional: hier Platz für Logik)
        return RedirectResponse(url="/CCP", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Starten des Raffles: {e}")

if __name__ == "__main__":
    uvicorn.run('main:app', port=8080, host="0.0.0.0", reload=True)
