"""Tokens assinados para autenticar Socket.IO entre dominios diferentes."""

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from extensions import db
from models import User


def serializer():
    """Cria o serializador usando a SECRET_KEY da aplicacao Flask atual."""
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="chat-socket-auth",
    )


def generate_socket_token(user):
    """Gera um token curto que identifica o usuario no WebSocket."""
    return serializer().dumps({"user_id": user.id})


def load_socket_user(token):
    """Valida o token recebido no Socket.IO e devolve o usuario do banco."""
    if not token:
        return None

    # O max_age permite expirar tokens antigos sem depender de cookie entre
    # dominios, que nao funciona bem entre primary e backup.
    max_age = int(current_app.config.get("SOCKET_AUTH_MAX_AGE", 43200))

    try:
        payload = serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        return None

    return db.session.get(User, user_id)
