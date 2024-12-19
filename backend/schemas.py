from pydantic import BaseModel, HttpUrl
from typing import Optional

class DeckSchema(BaseModel):
    """Schema für die Daten eines Decks."""
    deckersteller: str
    commander: str
    deckUrl: Optional[HttpUrl] = None  # Optionaler Parameter für die Deck-URL

