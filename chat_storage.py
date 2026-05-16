from datetime import datetime, timezone
from uuid import uuid4

from extensions import db
from models import Message


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_iso_datetime(value):
    if not value:
        return datetime.now(timezone.utc)

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_messages(limit):
    messages = (
        Message.query.order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
        .all()
    )
    return [message.to_payload() for message in reversed(messages)]


def store_message(payload, user_id=None):
    message_id = str(payload.get("id") or uuid4())
    existing = db.session.get(Message, message_id)
    if existing:
        return existing.to_payload()

    message = Message(
        id=message_id,
        type=str(payload.get("type") or "user")[:16],
        user_id=user_id,
        username=str(payload.get("user") or "sistema")[:32],
        text=str(payload.get("text") or "")[:1000],
        created_at=parse_iso_datetime(payload.get("timestamp")),
    )
    db.session.add(message)
    db.session.commit()
    return message.to_payload()
