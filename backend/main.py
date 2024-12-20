from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from backend.schemas import DeckSchema
import json
from pathlib import Path

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
        }
    )

@app.post("/submit", response_class=HTMLResponse)
async def submit_form(
    request: Request,
    deckersteller: str = Form(...),
    commander: str = Form(...),
    deckUrl: str = Form(None),
    deck_id: int = Form(...)
):
    """
    Verarbeitet das Formular, prüft den Deckersteller und fügt neue Datensätze hinzu.
    """
    try:
        # Konvertiere leere Strings zu None
        deckUrl = deckUrl or None

# Laden bestehender Daten
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
                        "error": f"Der Deckersteller '{deckersteller}' ist bereits registriert.",
                        "values": {"commander": commander, "deckUrl": deckUrl},
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
    Löscht die raffle.json-Datei, wenn sie existiert, und leitet den Benutzer zurück zum CCP.
    """
    try:
        if FILE_PATH.exists():
            FILE_PATH.unlink()  # Datei löschen
        # Nach Abschluss auf /CCP umleiten
        return RedirectResponse(url="/CCP", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Löschen der Datei: {e}")


@app.get("/CCP", response_class=HTMLResponse)
async def customer_control_panel(request: Request):
    """
    Zeigt die Customer Control Panel Seite an und prüft, ob die raffle.json-Datei existiert.
    """
    # Prüfen, ob die Datei existiert
    file_exists = FILE_PATH.exists()
    return templates.TemplateResponse(
        "CustomerControlPanel.html",
        {
            "request": request,
            "file_exists": file_exists,  # Ergebnis der Prüfung an die HTML-Seite übergeben
        }
    )
