from pydantic import BaseModel, HttpUrl, Field
from typing import Optional

class DeckSchema(BaseModel):
    deckersteller: str
    commander: Optional[str] = Field(default=None, description="Optional commander name")
    deckUrl: Optional[HttpUrl] = Field(default=None, description="Optional URL for the deck")
