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

Der Entwicklungs-Endpunkt `/results` zeigt pro Deck eine Zeile mit den unten beschriebenen Variablen.

| Variable (Spaltenname in `/results`) | Bezugsperson | Erfassung/Berechnung | Speicherung | Beschreibung |
|---|---|---|---|---|
| `deck_id` | Deck | Bei Registrierung vergeben/übernommen | `raffle.json` pro Deck-Eintrag | Eindeutige Deck-ID innerhalb des Events. |
| `deckersteller` | Deck-Ersteller | Bei Registrierung aus dem Formular | `raffle.json` pro Deck-Eintrag | Person, die das Deck gebaut hat. |
| `deckOwner` | Deck-Owner | Beim Raffle-Start zugewiesen | `raffle.json` pro Deck-Eintrag | Person, die das Deck im Event spielt. |
| `commander` | Deck | Bei Registrierung aus dem Formular (Commander 1) | `raffle.json` pro Deck-Eintrag | Primärer Commander-Name. |
| `commander2` | Deck | Optional bei Registrierung aus dem Formular (Commander 2) | `raffle.json` pro Deck-Eintrag | Zweiter Commander (Partner/Background/etc.). |
| `round_reports.<runde>.resolved_places[deckOwner]` | Deck-Owner | Aus Round-Report je Tisch aufgelöst (inkl. Tie-Handling) | `pairings.json` unter `round_reports` | Platzierung (Rank) eines Spielers in einer konkreten Runde. |
| `best_deck_votes.{deck_id}.1` | Votender Deck-Owner | Top-3-Voting-Eingabe eines Deck-Owners (Platz 1) | `pairings.json` unter `best_deck_votes` | Deck-ID, die als #1 gewählt wurde. |
| `best_deck_votes.{deck_id}.2` | Votender Deck-Owner | Top-3-Voting-Eingabe eines Deck-Owners (Platz 2) | `pairings.json` unter `best_deck_votes` | Deck-ID, die als #2 gewählt wurde. |
| `best_deck_votes.{deck_id}.3` | Votender Deck-Owner | Top-3-Voting-Eingabe eines Deck-Owners (Platz 3) | `pairings.json` unter `best_deck_votes` | Deck-ID, die als #3 gewählt wurde. |
| `calculated.top3_received_vote_points` | Deck-Ersteller (über dessen Deck) | Berechnet aus allen Top-3-Votes (konfigurierbar über `event_config.json -> voting.points_scheme.best_deck_voting`) | Laufzeitberechnung für `/results` (nicht persistiert) | Summe der erhaltenen Punkte eines Decks im Top-3-Voting. |
| `calculated.top3_received_rank` | Deck-Ersteller (über dessen Deck) | Sortierung nach `calculated.top3_received_vote_points` (absteigend, Tie-Break über `deck_id`) | Laufzeitberechnung für `/results` (nicht persistiert) | Platzierung eines Decks im Top-3-Voting. |
| `calculated.top3_rank_points_used_for_overall` | Deck-Ersteller | Aus `calculated.top3_received_rank` über die konfigurierbare Mapping-Tabelle (`event_config.json -> voting.points_scheme.best_deck_overall`) abgeleitet | Bereits in `pairings.json -> voting_results.data.rows[].deck_voting_points` nach Publish, sonst Laufzeitberechnung | Die Deck-Voting-Punkte, die in die Gesamtwertung eingehen. |
| `deck_creator_guess_votes.{deck_id}` | Votender Deck-Owner | Voting-Eingabe eines Deck-Owners im Deckraten | `pairings.json` unter `deck_creator_guess_votes` | Mapping *Deckersteller → vermutete Deck-ID* für einen Voter. |
| `calculated.round_phase_points` | Deck-Owner | Summe der Rundenpunkte aus allen erfassten `resolved_places` (konfigurierbar über `event_config.json -> voting.points_scheme.play_phase`) | Bereits in `pairings.json -> voting_results.data.rows[].game_points` nach Publish, sonst Laufzeitberechnung | Gesamtpunkte eines Spielers nur aus der Rundenphase. |
| `calculated.deck_creator_guess_points` | Deck-Owner | Konfigurierbare Punkte pro korrekter Zuordnung im Deckraten (`event_config.json -> voting.points_scheme.deck_creator_guess.correct_guess`) | Bereits in `pairings.json -> voting_results.data.rows[].guess_points` nach Publish, sonst Laufzeitberechnung | Erhaltene Punkte eines Spielers im Deckraten. |
| `calculated.overall_event_points` | Deck-Owner (mit creator-basiertem Top3-Anteil) | Summe aus Spielpunkten + Top3-Bonus + Deckratenpunkten | Bereits in `pairings.json -> voting_results.data.rows[].total_points` nach Publish, sonst Laufzeitberechnung | Gesamtpunktzahl eines Spielers im Event. |

### Hinweise zur Berechnung

- **Spielpunkte**: Werden aus `round_reports.*.*.resolved_places` abgeleitet; die Punktetabelle ist über `voting.points_scheme.play_phase` konfigurierbar.
- **Top-3-Deckpunkte**: Werden deck-bezogen über alle abgegebenen Top-3-Votes summiert; die Punkte pro Rang sind über `voting.points_scheme.best_deck_voting` konfigurierbar.
- **Deckratenpunkte**: Werden voter-bezogen aus korrekten Treffer-Zuordnungen gezählt; Punkte pro Treffer sind über `voting.points_scheme.deck_creator_guess.correct_guess` konfigurierbar.
- **Overall**: `game_points + top3_overall_bonus + guess_points`.


### `/results` als PDF exportieren

- Standard: `GET /results` liefert die Ergebnistabelle als **transponierte HTML-Tabelle** (Spaltennamen als erste Spalte/Index).
- PDF-Download: `GET /results?PDF=true` liefert die Ergebnistabelle als PDF-Datei (`results_<YYYY-MM-DDTHH-MM>.pdf`) zum Download.
- Für bessere Lesbarkeit wird die Tabelle im PDF ebenfalls **transponiert** (Spaltennamen werden zur ersten Spalte/Index), im **Portrait-Format** gerendert, mit **Zeilenumbrüchen in Zellen** sowie **automatischen Seitenumbrüchen**.

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
