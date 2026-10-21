"""WSGI entry point; importing it does not download the schedule."""

import logging

from main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
