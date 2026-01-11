# run_flask.py
import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parent / "src"))

from research_crew.web import create_app

app = create_app()
if __name__ == "__main__":
    app.run(debug=True)
