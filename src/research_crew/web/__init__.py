from flask import Flask

def create_app():
    app = Flask(__name__)
    # later: config, secrets, etc.
    from .db import init_db
    init_db()
    from .routes import bp
    app.register_blueprint(bp)

    return app
