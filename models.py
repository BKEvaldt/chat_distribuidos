from datetime import datetime, timezone
from uuid import uuid4

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), nullable=False, unique=True, index=True)
    username_key = db.Column(db.String(32), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    messages = db.relationship("Message", back_populates="author")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Message(db.Model):
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
        item = db.session.get(cls, key)
        if item is None:
            item = cls(key=key, value=value)
            db.session.add(item)
        else:
            item.value = value
        db.session.commit()
        return item
