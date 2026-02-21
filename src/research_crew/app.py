#!/usr/bin/env python
"""Compatibility module kept for older imports.

Use the Flask app (`run_flask.py`) for the UI.
Core generation/email functions now live in `research_crew.newsletter_service`.
"""

from research_crew.newsletter_service import generate_newsletter, send_newsletter_email


__all__ = ["generate_newsletter", "send_newsletter_email"]


def main():
    raise RuntimeError(
        "Legacy UI entrypoint has been removed. Start the web app with: python run_flask.py"
    )


if __name__ == "__main__":
    main()
