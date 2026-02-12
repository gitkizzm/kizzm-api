from pathlib import Path


SCRYFALL_BASE = "https://api.scryfall.com"
SUGGEST_MIN_CHARS = 3
SUGGEST_LIMIT = 15
SCRYFALL_TIMEOUT = 2.0

CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ENTRIES = 1000

DEFAULT_BG_QUERY = "t:basic t:snow e:SLD"
DEFAULT_BG_ZOOM = 1.12
COMMANDER_BG_ZOOM = 1.00

SCRYFALL_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "CommanderRaffle/1.0 (contact: you@example.com)",
}

RAFFLE_FILE_PATH = Path("raffle.json")
PAIRINGS_FILE_PATH = Path("pairings.json")
START_FILE_PATH = Path("start.txt")
PARTICIPANTS_FILE_PATH = Path("teilnehmer.txt")
EVENT_CONFIG_FILE_PATH = Path("event_config.json")

STATIC_DIR = "frontend"
TEMPLATES_DIR = "frontend"
ASSETS_DIR = Path("assets")

MAX_ROUNDS = 7
