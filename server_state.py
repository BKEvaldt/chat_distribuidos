from extensions import db
from models import ServerState

ACTIVE_ROLE_KEY = "active_role"
PRIMARY_ROLE = "primary"
BACKUP_ROLE = "backup"


def active_role(default=PRIMARY_ROLE):
    item = db.session.get(ServerState, ACTIVE_ROLE_KEY)
    if not item or item.value not in {PRIMARY_ROLE, BACKUP_ROLE}:
        return default
    return item.value


def set_active_role(role):
    if role not in {PRIMARY_ROLE, BACKUP_ROLE}:
        raise ValueError(f"papel ativo invalido: {role}")

    ServerState.set_value(ACTIVE_ROLE_KEY, role)
