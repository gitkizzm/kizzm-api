from fastapi import FastAPI, WebSocket, WebSocketDisconnect


def register_ws_routes(
    app: FastAPI,
    ws_manager,
    start_file_exists_loader,
    raffle_loader,
    global_signature_fn,
    deck_signature_fn,
):
    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        """
        Client connects with:
          - /ws?channel=ccp
          - /ws?channel=home
          - /ws?deck_id=<int>
        IMPORTANT: accept() MUST happen before any other logic, otherwise Starlette returns 403.
        """
        await websocket.accept()

        q = websocket.query_params
        channel = (q.get("channel") or "").strip().lower()
        deck_id_raw = (q.get("deck_id") or "").strip()

        group = "home"
        deck_id = None

        if channel == "ccp":
            group = "ccp"
        elif channel == "home":
            group = "home"
        elif deck_id_raw:
            try:
                deck_id = int(deck_id_raw)
                group = f"deck:{deck_id}"
            except ValueError:
                group = "home"

        ws_manager.connect_existing(websocket, group)

        try:
            start_file_exists = start_file_exists_loader()
            raffle_list = raffle_loader()

            if group in ("ccp", "home"):
                sig = global_signature_fn(start_file_exists, raffle_list)
                await websocket.send_json({"type": "hello", "scope": "global", "signature": sig})
            else:
                sig = deck_signature_fn(deck_id, start_file_exists, raffle_list)
                await websocket.send_json({"type": "hello", "scope": "deck", "deck_id": deck_id, "signature": sig})

            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            ws_manager.disconnect(websocket, group)
