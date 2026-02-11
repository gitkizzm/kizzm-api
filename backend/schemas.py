from pydantic import BaseModel, HttpUrl, Field
from typing import Optional

class DeckSchema(BaseModel):
    deckersteller: str

    commander: Optional[str] = Field(default=None, description="Commander 1 name")
    commander_id: Optional[str] = Field(default=None, description="Scryfall card id for commander 1")

    commander2: Optional[str] = Field(default=None, description="Commander 2 name (Partner/Background/etc.)")
    commander2_id: Optional[str] = Field(default=None, description="Scryfall card id for commander 2")

    deckUrl: Optional[HttpUrl] = Field(default=None, description="Optional URL for the deck")
