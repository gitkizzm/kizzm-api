import hashlib
import json
from typing import Callable

from fastapi import WebSocket


class WSManager:
    def __init__(self):
        self.groups: dict[str, set[WebSocket]] = {
            "ccp": set(),
            "home": set(),
        }

    def connect_existing(self, ws: WebSocket, group: str):
        if group not in self.groups:
            self.groups[group] = set()
        self.groups[group].add(ws)

    def disconnect(self, ws: WebSocket, group: str):
        if group in self.groups:
            self.groups[group].discard(ws)
            if group.startswith("deck:") and len(self.groups[group]) == 0:
                self.groups.pop(group, None)

    def active_deck_ids(self) -> set[int]:
        ids = set()
        for k in self.groups.keys():
            if k.startswith("deck:"):
                try:
                    ids.add(int(k.split(":", 1)[1]))
                except ValueError:
                    pass
        return ids

    async def broadcast_group(self, group: str, payload: dict):
        conns = list(self.groups.get(group, set()))
        if not conns:
            return
        dead = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.groups.get(group, set()).discard(ws)


def global_signature(
    start_file_exists: bool,
    raffle_list: list[dict],
    pairings_loader: Callable[[], dict | None],
    settings_loader: Callable[[], dict] | None = None,
) -> str:
    deck_ids = {e.get("deck_id") for e in raffle_list if "deck_id" in e}
    confirmed = 0
    total = 0
    for e in raffle_list:
        if "deck_id" in e:
            total += 1
            if e.get("received_confirmed") is True:
                confirmed += 1

    pair = pairings_loader() or {}
    obj = {
        "start_file_exists": start_file_exists,
        "deck_count": len(deck_ids),
        "total_decks": total,
        "confirmed_count": confirmed,
        "pairings_phase": pair.get("phase"),
        "active_round": pair.get("active_round"),
        "pairings_hosts": pair.get("hosts"),
        "round_reports": pair.get("round_reports") or {},
        "best_deck_votes": pair.get("best_deck_votes") or {},
        "deck_creator_guess_votes": pair.get("deck_creator_guess_votes") or {},
        "voting_results": pair.get("voting_results") or {},
        "settings": settings_loader() if settings_loader else {},
    }
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def deck_signature(
    deck_id: int,
    start_file_exists: bool,
    raffle_list: list[dict],
    pairings_loader: Callable[[], dict | None] | None = None,
    settings_loader: Callable[[], dict] | None = None,
) -> str:
    entry = None
    for e in raffle_list:
        if e.get("deck_id") == deck_id:
            entry = e
            break

    registered = entry is not None
    deck_owner = entry.get("deckOwner") if entry else None
    received_confirmed = entry.get("received_confirmed") if entry else None
    pair = pairings_loader() if pairings_loader else {}
    pair = pair if isinstance(pair, dict) else {}

    pairing_round = entry.get("pairing_round") if entry else None
    pairing_table = entry.get("pairing_table") if entry else None
    round_reports = pair.get("round_reports") if isinstance(pair.get("round_reports"), dict) else {}
    round_bucket = round_reports.get(str(pairing_round)) if pairing_round is not None else None
    table_report = round_bucket.get(str(pairing_table)) if isinstance(round_bucket, dict) and pairing_table is not None else None

    obj = {
        "deck_id": deck_id,
        "start_file_exists": start_file_exists,
        "registered": registered,
        "deckOwner": deck_owner,
        "received_confirmed": received_confirmed,
        "pairing_round": pairing_round,
        "pairing_table": pairing_table,
        "pairing_phase": entry.get("pairing_phase") if entry else None,
        "table_report": table_report or {},
        "voting_results": pair.get("voting_results") or {},
        "settings": settings_loader() if settings_loader else {},
    }
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
