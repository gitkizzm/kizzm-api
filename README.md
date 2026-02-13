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
| `calculated.round_phase_points` | Summe der Rundenpunkte aus allen erfassten `resolved_places` (`1→4`, `2→3`, `3→2`, `4→1`) | Bereits in `pairings.json -> voting_results.data.rows[].game_points` nach Publish, sonst Laufzeitberechnung | Gesamtpunkte eines Spielers nur aus der Rundenphase. |
| `calculated.deck_creator_guess_points` | +1 pro korrekter Zuordnung im Deckraten | Bereits in `pairings.json -> voting_results.data.rows[].guess_points` nach Publish, sonst Laufzeitberechnung | Erhaltene Punkte eines Spielers im Deckraten. |
| `calculated.overall_event_points` | Summe aus Spielpunkten + Top3-Bonus + Deckratenpunkten | Bereits in `pairings.json -> voting_results.data.rows[].total_points` nach Publish, sonst Laufzeitberechnung | Gesamtpunktzahl eines Spielers im Event. |

### Hinweise zur Berechnung

- **Spielpunkte**: Werden aus `round_reports.*.*.resolved_places` abgeleitet (Platz 1–4 ⇒ 4/3/2/1 Punkte).
- **Top-3-Deckpunkte**: Werden deck-bezogen über alle abgegebenen Top-3-Votes summiert.
- **Deckratenpunkte**: Werden voter-bezogen aus korrekten Treffer-Zuordnungen gezählt.
- **Overall**: `game_points + top3_overall_bonus + guess_points`.

## Debug-Automation (`/debug`)

Der Endpoint `/debug` führt die Event-State-Machine automatisiert aus.

### Nutzung

- `GET /debug` oder `POST /debug`: führt **genau einen** nächsten sinnvollen Schritt aus.
- `GET /debug?skip_to=<n>` oder `POST /debug?skip_to=<n>`: springt nur **vorwärts** bis zum Zielschritt.
- Zulässige Werte für `skip_to`:
  - `1..9`: springt bis zu diesem Schrittindex.
  - `0`: führt intern ein Reset aus (entspricht `/clear`).
  - `-1`: springt bis ans Ende (Ergebnisse veröffentlicht, Schritt `9`).

Wenn das Ziel bereits erreicht ist, liefert `/debug?skip_to=<n>` einen No-Op (kein Rücksprung).

### Event-Schritte der Debug-State-Machine

| Schritt-Nr. (`skip_to`) | Schrittname (interner Zustand) | Kurzbeschreibung |
|---|---|---|
| `0` | `reset_to_start` | Sonderfall: Eventdaten werden zurückgesetzt (`raffle.json`, `start.txt`, `pairings.json`). |
| `1` | `registration_prepared` | Test-Registrierungen sind vorhanden (mind. erforderliche Decks). |
| `2` | `raffle_started` | Raffle wurde gestartet, Decks wurden Besitzern zugewiesen. |
| `3` | `all_confirmed` | Alle registrierten Decks sind als erhalten bestätigt. |
| `4` | `pairings_round_1_started` | Pairings gestartet, aktive Runde ist 1. |
| `5` | `pairings_round_2_started` | Runde 2 läuft. |
| `6` | `pairings_round_3_started` | Runde 3 läuft. |
| `7` | `pairings_round_4_started` | Runde 4 läuft. |
| `8` | `voting_phase` | Spielphase beendet, Voting-Phase erreicht (Ergebnisse noch nicht veröffentlicht). |
| `9` | `voting_results_published` | Voting abgeschlossen und Ergebnisdaten veröffentlicht. |
