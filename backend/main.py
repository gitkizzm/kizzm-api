from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import json
from pathlib import Path

# Datenvalidierungsschema
class DeckSchema(BaseModel):
    deckersteller: str
    commander: str
    deckUrl: HttpUrl | None = None  # Optional

# Pfad zur Datei
FILE_PATH = Path("raffle.json")

# FastAPI-App
app = FastAPI()

@app.post("/save")
async def save_deck(data: DeckSchema):
    try:
        # Daten in JSON-Datei speichern
        with FILE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data.dict(), f, ensure_ascii=False, indent=4)
        return {"message": "Daten erfolgreich gespeichert."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern der Daten: {e}")
