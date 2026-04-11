from __future__ import annotations

from flask import Flask, redirect, url_for

from app.routes.audio import audio_bp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.register_blueprint(audio_bp)

    @app.get("/")
    def root_redirect():
        return redirect(url_for("audio.audio_index"))

    # Backward compatibility with previous routes
    @app.get("/index")
    def index_redirect():
        return redirect(url_for("audio.audio_index"))

    @app.get("/info")
    def info_redirect():
        return redirect(url_for("audio.audio_index", expert="1"))

    return app
