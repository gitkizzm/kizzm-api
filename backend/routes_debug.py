from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


def register_debug_routes(app: FastAPI, apply_step, notify_state_change) -> None:
    @app.get("/debug", response_class=HTMLResponse)
    async def debug_get():
        """
        Browser-friendly debug step runner.
        """
        result = await apply_step()
        await notify_state_change()

        lines = [
            "<html><head><meta charset='utf-8'><title>/debug</title></head><body style='font-family: system-ui; padding: 16px;'>",
            "<h2>/debug</h2>",
            f"<p><strong>phase:</strong> {result.get('phase')}</p>",
            f"<p><strong>action:</strong> {result.get('action')}</p>",
        ]

        if result.get("action") == "filled_decks_1_to_8":
            lines.append(f"<p>created_count: {result.get('created_count')}</p>")
            lines.append("<ul>")
            for e in (result.get("created") or []):
                lines.append(f"<li>deck_id {e.get('deck_id')}: {e.get('deckersteller')} — {e.get('commander')}</li>")
            lines.append("</ul>")

        if result.get("action") == "confirmed_all_pending":
            lines.append(f"<p>updated_count: {result.get('updated_count')}</p>")
            lines.append(f"<p>updated_deck_ids: {result.get('updated_deck_ids')}</p>")

        if result.get("action") == "raffle_started":
            lines.append(f"<p>assigned_count: {result.get('assigned_count')}</p>")

        if result.get("action") == "pairings_started":
            lines.append(f"<p>pods: {result.get('pods')}</p>")
            lines.append(f"<p>active_round: {result.get('active_round')}</p>")
            lines.append(f"<p>phase: {result.get('phase')}</p>")

        if result.get("action") in ("started_next_round", "started_round_5_and_ended_play_phase", "ended_play_phase"):
            if result.get("active_round") is not None:
                lines.append(f"<p>active_round: {result.get('active_round')}</p>")
            if result.get("phase"):
                lines.append(f"<p>phase: {result.get('phase')}</p>")

        if result.get("action") in ("filled_missing_voting_participants", "completed_voting_and_published_results"):
            lines.append(f"<p>top3_filled_for: {result.get('top3_filled_for') or []}</p>")
            lines.append(f"<p>deckraten_filled_for: {result.get('deckraten_filled_for') or []}</p>")
            lines.append(f"<p>pending_voters: {result.get('pending_voters') or []}</p>")
            lines.append(f"<p>published: {bool(result.get('published'))}</p>")

        if result.get("message"):
            lines.append(f"<p>{result.get('message')}</p>")

        lines.append("<p style='margin-top:16px; opacity:0.7;'>Reload this page to advance the next step.</p>")
        lines.append("</body></html>")
        return HTMLResponse("\n".join(lines))

    @app.post("/debug")
    async def debug_post():
        """
        JSON-friendly debug step runner (keeps compatibility with your current setup).
        """
        result = await apply_step()
        await notify_state_change()
        return JSONResponse(result)
