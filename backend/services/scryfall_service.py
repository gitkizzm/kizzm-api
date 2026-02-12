from urllib.parse import quote_plus

import httpx

from backend.config import SCRYFALL_BASE, SCRYFALL_HEADERS, SCRYFALL_TIMEOUT


async def get_card_by_id(card_id: str) -> dict | None:
    card_id = (card_id or "").strip()
    if not card_id:
        return None
    url = f"{SCRYFALL_BASE}/cards/{quote_plus(card_id)}"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception:
        return None


async def random_commander(exclude_card_ids: set[str] | None = None, max_tries: int = 25) -> dict | None:
    exclude_card_ids = exclude_card_ids or set()
    scry_q = "game:paper is:commander -t:background"
    url = f"{SCRYFALL_BASE}/cards/random?q={quote_plus(scry_q)}"

    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            for _ in range(max_tries):
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                card = r.json()
                cid = (card.get("id") or "").strip()
                name = (card.get("name") or "").strip()
                if not cid or not name:
                    continue
                if cid in exclude_card_ids:
                    continue
                return card
    except Exception:
        return None

    return None


async def named_exact(name: str) -> dict | None:
    name = (name or "").strip()
    if not name:
        return None

    url = f"{SCRYFALL_BASE}/cards/named?exact={quote_plus(name)}"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception:
        return None


async def is_partner_exact_name(name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False

    scry_q = f'!"{name}" is:partner'
    url = f"{SCRYFALL_BASE}/cards/search?q={quote_plus(scry_q)}&unique=cards"
    try:
        async with httpx.AsyncClient(timeout=SCRYFALL_TIMEOUT, headers=SCRYFALL_HEADERS) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return False
            payload = r.json()
            total = int(payload.get("total_cards") or 0)
            return total > 0
    except Exception:
        return False
