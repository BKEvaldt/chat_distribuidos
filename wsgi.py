"""Ponto de entrada do Gunicorn para o servidor primario."""

from app import app, start_background_threads


# No Render, o Gunicorn importa este arquivo. Esse start garante que as
# threads de replicacao e monitoramento nascam junto com o processo web.
start_background_threads()
