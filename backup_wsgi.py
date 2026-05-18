"""Ponto de entrada do Gunicorn para o servidor backup."""

from backup_server import app, start_background_threads


# O backup precisa iniciar sua thread de heartbeat assim que o processo sobe.
start_background_threads()
