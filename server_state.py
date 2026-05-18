"""Leitura e escrita do papel ativo do cluster no banco compartilhado."""

from extensions import db
from models import ServerState

# Esta chave funciona como uma pequena "fonte da verdade": todos os servicos
# consultam o mesmo valor para saber quem deve aceitar mensagens.
ACTIVE_ROLE_KEY = "active_role"
PRIMARY_ROLE = "primary"
BACKUP_ROLE = "backup"


def active_role(default=PRIMARY_ROLE):
    """Retorna qual servidor deve estar ativo agora."""
    item = db.session.get(ServerState, ACTIVE_ROLE_KEY)
    if not item or item.value not in {PRIMARY_ROLE, BACKUP_ROLE}:
        return default
    return item.value


def set_active_role(role):
    """Atualiza o papel ativo, validando antes para evitar estado invalido."""
    if role not in {PRIMARY_ROLE, BACKUP_ROLE}:
        raise ValueError(f"papel ativo invalido: {role}")

    ServerState.set_value(ACTIVE_ROLE_KEY, role)
