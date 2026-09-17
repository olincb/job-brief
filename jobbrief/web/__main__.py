"""Serve the app: `python -m jobbrief.web`."""

from waitress import serve

from jobbrief.web.app import create_app


PORT = 8080  # what the container listens on; the deployment's proxy points at it

serve(create_app(), host="0.0.0.0", port=PORT)
