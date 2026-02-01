from fastapi import FastAPI

from core.api.router import router

from core.api.routes import audio



def create_app() -> FastAPI:
    app = FastAPI(title="Headlines Backend")
    app.include_router(router)
    app.include_router(audio.router)
    return app


app = create_app()