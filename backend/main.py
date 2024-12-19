from fastapi import FastAPI, HTTPException
from backend.schemas import DeckSchema  # Import des Schemas
import json
from pathlib import Path


# Pfad zur Datei
FILE_PATH = Path("raffle.json")

# FastAPI-App
app = FastAPI()

@app.post("/save")
async def save_deck(data: DeckSchema):
    """
    Route zum Speichern eines Decks.
    Erwartet ein JSON-Objekt, das dem DeckSchema entspricht.
    """
    try:
        # Daten in JSON-Datei speichern
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data.dict(), f, ensure_ascii=False, indent=4)
        return {"message": "Daten erfolgreich gespeichert."}
    except Exception as e:
        # Fehlerbehandlung bei Problemen mit dem Speichern
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern der Daten: {e}")

@app.get("/")
async def root():
    """
    Root-Endpunkt. Kann verwendet werden, um die Verfügbarkeit der API zu testen.
    """
    return {"message": "Willkommen bei der Deck-Registrierungs-API!"}
