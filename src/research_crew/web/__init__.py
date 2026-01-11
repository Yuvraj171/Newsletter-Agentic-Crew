from flask import Flask 

def create_app():
    app = Flask(__name__)
    # later: config, secrets, etc.
    from .routes import bp
    app.register_blueprint(bp)

    return app