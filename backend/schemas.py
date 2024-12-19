from pydantic import BaseModel, HttpUrl, Field
from typing import Optional

class DeckSchema(BaseModel):
    deckersteller: str
    commander: str
    deckUrl: Optional[HttpUrl] = Field(default=None, description="Optional URL for the deck")
