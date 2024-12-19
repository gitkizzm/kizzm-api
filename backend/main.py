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
async def get_form(request: Request):
    """
    Zeigt die Startseite mit dem Formular an.
    """
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/submit", response_class=RedirectResponse)
async def submit_form(
    deckersteller: str = Form(...),
    commander: str = Form(...),
    deckUrl: str = Form(None)
):
    """
    Verarbeitet das Formular und speichert die Daten.
    """
    try:
        # Konvertiere leere Strings zu None
        deckUrl = deckUrl or None

        # Daten validieren und speichern
        data = DeckSchema(deckersteller=deckersteller, commander=commander, deckUrl=deckUrl)

        # Dictionary mit serialisierbaren Typen erstellen
        serializable_data = data.dict()
        serializable_data['deckUrl'] = str(serializable_data['deckUrl']) if serializable_data['deckUrl'] else None

        # Daten speichern: JSON-Darstellung mit json.dump()
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump(serializable_data, f, ensure_ascii=False, indent=4)
        
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
