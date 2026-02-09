from flask import Flask

def create_app():
    app = Flask(__name__)
    # later: config, secrets, etc.
    from .db import init_db, load_job
    init_db()

    from research_crew.memory.db import init_db as init_memory_db
    init_memory_db()

    from research_crew.memory.episodes import cleanup_old_episodes, reconcile_running_episodes
    from research_crew.memory.profile import ensure_profile

    reconcile_running_episodes(load_job)
    cleanup_old_episodes(retention_days=180)
    ensure_profile()

    from .routes import bp
    app.register_blueprint(bp)

    return app
