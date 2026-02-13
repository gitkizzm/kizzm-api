# Deck Registrierung

Dieses Projekt registriert Decks und speichert die Daten in einer JSON-Datei.

## Features
- Dropdown-Auswahl für Deckersteller
- Textfelder für Commander und Deck-URL
- Daten werden in `raffle.json` gespeichert
- Erfolgsseite nach Registrierung

## Technologien
- **Frontend**: HTML, JavaScript
- **Backend**: Python, FastAPI, Uvicorn
- **Hosting**: Back4App

## Setup
1. Klonen Sie das Repository:
   ```bash
   git clone <repository-url>
   cd deck-registration
   ```

## Development (Codespace)
Starten Sie das Backend im Codespace mit:

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

## Ergebnisvariablen im Event-Speicher

Der Entwicklungs-Endpunkt `/ergebnisse` zeigt pro Deck eine Zeile mit den unten beschriebenen Variablen.

| Variable (Spaltenname in `/ergebnisse`) | Erfassung/Berechnung | Speicherung | Beschreibung |
|---|---|---|---|
| `deck_id` | Bei Registrierung vergeben/übernommen | `raffle.json` pro Deck-Eintrag | Eindeutige Deck-ID innerhalb des Events. |
| `deckersteller` | Bei Registrierung aus dem Formular | `raffle.json` pro Deck-Eintrag | Person, die das Deck gebaut hat. |
| `deckOwner` | Beim Raffle-Start zugewiesen | `raffle.json` pro Deck-Eintrag | Person, die das Deck im Event spielt. |
| `commander` | Bei Registrierung aus dem Formular (Commander 1) | `raffle.json` pro Deck-Eintrag | Primärer Commander-Name. |
| `commander2` | Optional bei Registrierung aus dem Formular (Commander 2) | `raffle.json` pro Deck-Eintrag | Zweiter Commander (Partner/Background/etc.). |
| `round_reports.<runde>.resolved_places[deckOwner]` | Aus Round-Report je Tisch aufgelöst (inkl. Tie-Handling) | `pairings.json` unter `round_reports` | Platzierung (Rank) eines Spielers in einer konkreten Runde. |
| `best_deck_votes.{deck_id}.1` | Top-3-Voting-Eingabe eines Deck-Owners (Platz 1) | `pairings.json` unter `best_deck_votes` | Deck-ID, die als #1 gewählt wurde. |
| `best_deck_votes.{deck_id}.2` | Top-3-Voting-Eingabe eines Deck-Owners (Platz 2) | `pairings.json` unter `best_deck_votes` | Deck-ID, die als #2 gewählt wurde. |
| `best_deck_votes.{deck_id}.3` | Top-3-Voting-Eingabe eines Deck-Owners (Platz 3) | `pairings.json` unter `best_deck_votes` | Deck-ID, die als #3 gewählt wurde. |
| `calculated.top3_received_vote_points` | Berechnet aus allen Top-3-Votes (`#1=3`, `#2=2`, `#3=1`) | Laufzeitberechnung für `/ergebnisse` (nicht persistiert) | Summe der erhaltenen Punkte eines Decks im Top-3-Voting. |
| `calculated.top3_received_rank` | Sortierung nach `calculated.top3_received_vote_points` (absteigend, Tie-Break über `deck_id`) | Laufzeitberechnung für `/ergebnisse` (nicht persistiert) | Platzierung eines Decks im Top-3-Voting. |
| `deck_creator_guess_votes.{deck_id}` | Voting-Eingabe eines Deck-Owners im Deckraten | `pairings.json` unter `deck_creator_guess_votes` | Mapping *Deckersteller → vermutete Deck-ID* für einen Voter. |
| `calculated.deck_creator_guess_points` | +1 pro korrekter Zuordnung im Deckraten | Bereits in `pairings.json -> voting_results.data.rows[].guess_points` nach Publish, sonst Laufzeitberechnung | Erhaltene Punkte eines Spielers im Deckraten. |
| `calculated.overall_event_points` | Summe aus Spielpunkten + Top3-Bonus + Deckratenpunkten | Bereits in `pairings.json -> voting_results.data.rows[].total_points` nach Publish, sonst Laufzeitberechnung | Gesamtpunktzahl eines Spielers im Event. |

### Hinweise zur Berechnung

- **Spielpunkte**: Werden aus `round_reports.*.*.resolved_places` abgeleitet (Platz 1–4 ⇒ 4/3/2/1 Punkte).
- **Top-3-Deckpunkte**: Werden deck-bezogen über alle abgegebenen Top-3-Votes summiert.
- **Deckratenpunkte**: Werden voter-bezogen aus korrekten Treffer-Zuordnungen gezählt.
- **Overall**: `game_points + top3_overall_bonus + guess_points`.

