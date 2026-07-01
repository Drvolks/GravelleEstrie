"""Interactive one-time Strava OAuth flow.

Automates the manual "open URL → approve → copy code → curl for tokens →
paste into .env" dance: runs a tiny local HTTP server to catch Strava's OAuth
redirect, opens the consent screen in a browser, exchanges the returned code
for tokens, and writes ``STRAVA_REFRESH_TOKEN`` straight into .env.

``STRAVA_CLIENT_ID``/``STRAVA_CLIENT_SECRET`` must already be in .env (from
creating an API application at https://www.strava.com/settings/api) — that
one-time app registration step can't be automated.
"""
from __future__ import annotations

import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from dotenv import set_key

_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
_TOKEN_URL = "https://www.strava.com/oauth/token"
_TIMEOUT = 30


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the single OAuth redirect Strava sends back, then serves a
    plain confirmation page so the user knows they can return to the terminal.
    """

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.oauth_code = params.get("code", [None])[0]
        self.server.oauth_error = params.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if self.server.oauth_error:
            body = (
                f"<h1>Autorisation refusée</h1><p>{self.server.oauth_error}</p>"
                "<p>Vous pouvez fermer cette fenêtre.</p>"
            )
        else:
            body = "<h1>Autorisation reçue ✓</h1><p>Vous pouvez fermer cette fenêtre et revenir au terminal.</p>"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):  # silence default request logging
        pass


def _build_authorize_url(client_id: str, redirect_uri: str, scope: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "approval_prompt": "auto",
        }
    )
    return f"{_AUTHORIZE_URL}?{query}"


def _exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    resp = requests.post(
        _TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise CommandError(f"Échec de l'échange du code ({resp.status_code}) : {resp.text}")
    return resp.json()


class Command(BaseCommand):
    help = (
        "Interactive one-time Strava OAuth flow: opens a browser for consent, "
        "captures the redirect locally, exchanges the code for tokens, and "
        "writes STRAVA_REFRESH_TOKEN back into .env automatically."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--scope",
            default="read",
            help="OAuth scope to request (default: read — enough for public routes).",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Don't auto-open a browser; just print the URL to visit.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=300,
            help="Seconds to wait for the OAuth redirect before giving up (default: 300).",
        )
        parser.add_argument(
            "--env-file",
            default=None,
            help="Path to the .env file to update (default: BASE_DIR/.env).",
        )

    def handle(self, *args, **options):
        client_id = settings.STRAVA_CLIENT_ID
        client_secret = settings.STRAVA_CLIENT_SECRET
        if not client_id or not client_secret:
            raise CommandError(
                "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must already be set in "
                ".env (create an API application at "
                "https://www.strava.com/settings/api first)."
            )

        env_path = (
            Path(options["env_file"]) if options["env_file"] else Path(settings.BASE_DIR) / ".env"
        )
        if not env_path.exists():
            raise CommandError(f"{env_path} does not exist — copy .env.example to .env first.")

        server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        server.oauth_code = None
        server.oauth_error = None
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}/"

        authorize_url = _build_authorize_url(client_id, redirect_uri, options["scope"])

        self.stdout.write("Ouvrez cette URL et approuvez l'accès :")
        self.stdout.write(authorize_url)
        if not options["no_browser"]:
            try:
                webbrowser.open(authorize_url)
            except webbrowser.Error:
                self.stdout.write(self.style.WARNING("Impossible d'ouvrir un navigateur automatiquement."))

        self.stdout.write(
            f"En attente de la redirection Strava sur {redirect_uri} "
            f"(jusqu'à {options['timeout']}s)..."
        )
        server.timeout = options["timeout"]
        try:
            server.handle_request()
        finally:
            server.server_close()

        if server.oauth_error:
            raise CommandError(f"Strava a refusé l'autorisation : {server.oauth_error}")
        if not server.oauth_code:
            raise CommandError(
                "Aucune redirection reçue avant l'expiration du délai — réessayez, "
                "ou vérifiez le domaine de callback configuré sur "
                "strava.com/settings/api (doit inclure 'localhost')."
            )

        tokens = _exchange_code_for_tokens(client_id, client_secret, server.oauth_code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise CommandError(f"Pas de refresh_token dans la réponse Strava : {tokens}")

        set_key(str(env_path), "STRAVA_REFRESH_TOKEN", refresh_token)
        self.stdout.write(self.style.SUCCESS(f"STRAVA_REFRESH_TOKEN mis à jour dans {env_path}"))

        athlete_id = (tokens.get("athlete") or {}).get("id")
        if athlete_id:
            self.stdout.write(f"Compte autorisé : id={athlete_id}")
        if not settings.STRAVA_ROUTE_IDS:
            self.stdout.write(
                "Note : STRAVA_ROUTE_IDS est vide — c'est normal si vous comptez "
                "sur RideWithGPS comme source principale (voir README). Ajoutez-y "
                "des identifiants de parcours si vous voulez aussi lier des "
                "sorties à Strava."
            )
