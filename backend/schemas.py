from pydantic import BaseModel, HttpUrl, Field
from typing import Optional

class DeckSchema(BaseModel):
    deckersteller: str
    commander: str = Field(default=None, description="commander name")
    commander2: Optional[str] = Field(default=None, description="Optional second commander (Partner etc.)")
    deckUrl: Optional[HttpUrl] = Field(default=None, description="Optional URL for the deck")