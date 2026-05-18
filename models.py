"""Modelos SQLAlchemy usados pelo chat e pelo controle de failover."""

from datetime import datetime, timezone
from uuid import uuid4

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utc_now():
    """SQLAlchemy chama esta funcao ao criar registros com timestamp."""
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    """Usuario autenticado do chat."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), nullable=False, unique=True, index=True)
    username_key = db.Column(db.String(32), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    messages = db.relationship("Message", back_populates="author")

    def set_password(self, password):
        """Nunca salvamos senha pura: apenas o hash gerado pelo Werkzeug."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Compara a senha digitada com o hash armazenado."""
        return check_password_hash(self.password_hash, password)


class Message(db.Model):
    """Mensagem persistida no historico compartilhado pelos servidores."""

    __tablename__ = "messages"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    type = db.Column(db.String(16), nullable=False, default="user")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    username = db.Column(db.String(32), nullable=False)
    text = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )

    author = db.relationship("User", back_populates="messages")

    def to_payload(self):
        """Converte o registro do banco para o formato enviado ao frontend."""
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return {
            "id": self.id,
            "type": self.type,
            "user": self.username,
            "text": self.text,
            "timestamp": created_at.isoformat(timespec="microseconds"),
        }


class ServerState(db.Model):
    """Armazena valores globais do cluster, como o servidor ativo."""

    __tablename__ = "server_state"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    @classmethod
    def set_value(cls, key, value):
        """Atualiza ou cria uma chave de estado em uma unica operacao."""
        item = db.session.get(cls, key)
        if item is None:
            item = cls(key=key, value=value)
            db.session.add(item)
        else:
            item.value = value
        db.session.commit()
        return item
