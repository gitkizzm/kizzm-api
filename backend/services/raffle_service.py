from random import shuffle
from pathlib import Path

from backend.repositories.json_store import atomic_write_json
from backend.repositories.raffle_repository import load_raffle_list


class RaffleStartError(Exception):
    pass


def shuffle_decks(deck_creators: list[str]) -> tuple[list[str], list[str]]:
    creator_order = deck_creators[:]
    gift_order = deck_creators[:]

    while any(i == j for i, j in zip(gift_order, creator_order)):
        shuffle(creator_order)
        shuffle(gift_order)

    return gift_order, creator_order


def assign_deck_owners(raffle_list: list[dict], min_decks: int = 3) -> int:
    deckersteller_list = [
        e.get("deckersteller")
        for e in raffle_list
        if e.get("deckersteller")
    ]
    unique_decks = list(dict.fromkeys(deckersteller_list))
    if len(unique_decks) < int(min_decks):
        raise RaffleStartError(f"Raffle kann erst ab {int(min_decks)} registrierten Decks gestartet werden.")

    c_order, g_order = shuffle_decks(unique_decks)

    owner_by_creator = {creator: new_owner for creator, new_owner in zip(c_order, g_order)}

    for entry in raffle_list:
        creator = entry.get("deckersteller")
        if creator in owner_by_creator:
            entry["deckOwner"] = owner_by_creator[creator]
            entry["received_confirmed"] = False

    return len(unique_decks)


def start_raffle(file_path: Path, start_file_path: Path, min_decks: int = 3) -> int:
    raffle_list = load_raffle_list(file_path)
    assigned_count = assign_deck_owners(raffle_list, min_decks=min_decks)

    with start_file_path.open("w", encoding="utf-8") as f:
        f.write("")

    atomic_write_json(file_path, raffle_list)
    return assigned_count
