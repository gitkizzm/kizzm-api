from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from backend.schemas import DeckSchema
import json
from pathlib import Path

# Pfad zur JSON-Datei, in der die Daten gespeichert werden
FILE_PATH = Path("raffle.json")

# FastAPI-App erstellen
app = FastAPI()

# Statische Dateien und Templates einrichten
app.mount("/static", StaticFiles(directory="frontend"), name="static")
templates = Jinja2Templates(directory="frontend")


@app.get("/", response_class=HTMLResponse)
async def get_form(request: Request):
    """
    Endpoint für das HTML-Formular.
    """
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/submit", response_class=RedirectResponse)
async def submit_form(
    deckersteller: str = Form(...),
    commander: str = Form(...),
    deckUrl: str = Form(None)
):
    """
    Endpoint zum Verarbeiten von Formularen.
    """
    try:
        # Daten validieren und speichern
        data = DeckSchema(deckersteller=deckersteller, commander=commander, deckUrl=deckUrl)
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data.dict(), f, ensure_ascii=False, indent=4)
        
        # Erfolgreich weiterleiten
        return RedirectResponse(url="/success", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern der Daten: {e}")


@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    """
    Erfolgsseite nach dem Absenden des Formulars.
    """
    return templates.TemplateResponse("success.html", {"request": request})
