from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import ASSETS_DIR, STATIC_DIR, TEMPLATES_DIR


def create_app() -> tuple[FastAPI, Jinja2Templates]:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if ASSETS_DIR.exists() and ASSETS_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    return app, templates
