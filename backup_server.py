"""Servidor backup do chat distribuido.

Ele fica em espera enquanto o primario esta saudavel. Se o heartbeat detectar
falhas suficientes, este processo assume o papel ativo e passa a atender os
clientes sem expor detalhes tecnicos na interface.
"""

import logging
import os
import threading
import time
from uuid import uuid4

import requests
from flask import Flask, jsonify, render_template, request
from flask_login import current_user, login_required
from flask_socketio import SocketIO, emit

from auth import register_auth_routes
from chat_storage import latest_messages, now_iso, store_message
from client_threads import ClientThreadManager
from extensions import db, login_manager, migrate
from server_state import BACKUP_ROLE, PRIMARY_ROLE, active_role, set_active_role
from socket_auth import generate_socket_token, load_socket_user


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def required_env(name):
    """Le variavel obrigatoria e falha cedo quando a configuracao esta incompleta."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variavel de ambiente obrigatoria ausente: {name}")
    return value


def database_uri():
    """Converte a URL do Postgres para o driver psycopg usado no projeto."""
    url = required_env("DATABASE_URL")
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def normalize_service_url(url):
    """Permite informar host simples ou URL completa nas variaveis do Render."""
    if url and not url.startswith(("http://", "https://")):
        return f"http://{url}"
    return url


# O backup tem sua propria instancia Flask, mas usa o mesmo banco e as mesmas
# rotas de login/cadastro para manter a experiencia igual a do primario.
app = Flask(__name__)
app.config["SECRET_KEY"] = required_env("SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SOCKET_AUTH_MAX_AGE"] = int(os.getenv("SOCKET_AUTH_MAX_AGE", "43200"))

db.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)
register_auth_routes(app)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_interval=10,
    ping_timeout=20,
)

# URLs, tokens e limites sao controlados por variaveis de ambiente no Render.
# Assim o mesmo codigo pode rodar como primario ou backup sem hardcode.
PRIMARY_PUBLIC_URL = required_env("PRIMARY_PUBLIC_URL")
BACKUP_PUBLIC_URL = required_env("BACKUP_PUBLIC_URL")
PRIMARY_INTERNAL_URL = normalize_service_url(required_env("PRIMARY_INTERNAL_URL"))
PRIMARY_HEALTH_URL = os.getenv("PRIMARY_HEALTH_URL", f"{PRIMARY_INTERNAL_URL}/health")
REPLICATION_TOKEN = required_env("REPLICATION_TOKEN")
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "2"))
FAILURE_THRESHOLD = int(os.getenv("FAILURE_THRESHOLD", "3"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "100"))
ENABLE_FAILOVER_CONTROL = os.getenv("ENABLE_FAILOVER_CONTROL", "1").lower() not in {
    "0",
    "false",
    "no",
}
SHOW_FAILOVER_CONTROLS = os.getenv("SHOW_FAILOVER_CONTROLS", "1").lower() in {
    "1",
    "true",
    "yes",
}
PRIMARY_RESTORE_TIMEOUT = float(os.getenv("PRIMARY_RESTORE_TIMEOUT", "30"))

# Estado em memoria do processo backup. O papel ativo real tambem fica no banco,
# mas essas variaveis evitam consultas repetidas dentro de eventos muito curtos.
state_lock = threading.RLock()
restore_lock = threading.Lock()
is_active = False
failure_count = 0
connected_users = {}
replicated_primary_users = []
background_threads_started = False


def clean_text(value, limit):
    """Normaliza texto vindo do cliente antes de validar e persistir."""
    value = str(value or "").strip()
    return value[:limit]


def add_message(message):
    """Salva mensagens replicadas ou criadas pelo backup ativo."""
    return store_message(message)


def notification_payload(text, level="info"):
    """Monta uma notificacao temporaria para os clientes conectados."""
    return {
        "id": str(uuid4()),
        "type": "notification",
        "level": level,
        "text": text,
        "timestamp": now_iso(),
    }


def primary_is_healthy():
    """Consulta o /health do primario para saber se ele ainda esta ativo."""
    try:
        response = requests.get(PRIMARY_HEALTH_URL, timeout=1.5)
        return response.ok and response.json().get("active") is True
    except requests.RequestException:
        return False


def wait_for_primary(timeout=PRIMARY_RESTORE_TIMEOUT):
    """Espera o primario voltar antes de devolver o papel ativo para ele."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if primary_is_healthy():
            return True
        time.sleep(0.4)
    return primary_is_healthy()


def backup_is_active():
    """Sincroniza a memoria local com o papel ativo salvo no banco."""
    global is_active
    active = active_role() == BACKUP_ROLE
    with state_lock:
        is_active = active
    return active


def demote_to_standby():
    """Coloca o backup de volta em espera quando o primario foi restaurado."""
    global failure_count, is_active
    with state_lock:
        is_active = False
        failure_count = 0
        connected_users.clear()


def restore_primary():
    """Tenta devolver o atendimento ao primario de forma controlada."""
    with restore_lock:
        # Primeiro alteramos o papel no banco; depois confirmamos se o primario
        # realmente voltou a responder como ativo.
        set_active_role(PRIMARY_ROLE)
        if not wait_for_primary():
            set_active_role(BACKUP_ROLE)
            return False

        demote_to_standby()
        return True


def socket_user_from_auth(auth):
    """Autentica Socket.IO via sessao Flask ou token assinado vindo do Worker."""
    if current_user.is_authenticated:
        return current_user

    token = auth.get("token") if isinstance(auth, dict) else None
    return load_socket_user(token)


def users_snapshot():
    """Retorna os usuarios conectados ao backup quando ele esta ativo."""
    with state_lock:
        return [
            {
                "sid": sid,
                "user_id": data["user_id"],
                "name": data["name"],
                "joined_at": data["joined_at"],
            }
            for sid, data in sorted(
                connected_users.items(), key=lambda item: item[1]["name"].lower()
            )
        ]


def promote_to_active(reason="falhas_heartbeat"):
    """Promove o backup para servidor ativo e avisa os clientes conectados."""
    global failure_count, is_active
    with state_lock:
        if is_active:
            return
        is_active = True
        failure_count = max(failure_count, FAILURE_THRESHOLD)
        connected_users.clear()

    set_active_role(BACKUP_ROLE)

    logging.warning(
        "FAILOVER_BACKUP_ATIVO motivo=%s falhas=%s",
        reason,
        failure_count,
    )
    socketio.emit(
        "server_promoted",
        {"role": "backup", "active": True, "server_url": BACKUP_PUBLIC_URL},
    )


def heartbeat_worker():
    """
    Thread de heartbeat e failover.

    O backup consulta o endpoint /health do principal em intervalos fixos. Apos
    FAILURE_THRESHOLD falhas consecutivas, ele muda para modo ativo e passa a
    aceitar usuarios pelo WebSocket.
    """
    global failure_count

    with app.app_context():
        while True:
            if backup_is_active():
                time.sleep(HEARTBEAT_INTERVAL)
                continue

            if active_role() != PRIMARY_ROLE:
                time.sleep(HEARTBEAT_INTERVAL)
                continue

            try:
                response = requests.get(PRIMARY_HEALTH_URL, timeout=1.5)
                if response.ok and response.json().get("active") is True:
                    failure_count = 0
                    logging.info("HEARTBEAT_PRIMARIO_OK falhas=0")
                else:
                    failure_count += 1
                    logging.warning(
                        "HEARTBEAT_PRIMARIO_INVALIDO falhas=%s",
                        failure_count,
                    )
            except requests.RequestException as exc:
                failure_count += 1
                logging.warning(
                    "HEARTBEAT_PRIMARIO_FALHOU falhas=%s erro=%s",
                    failure_count,
                    exc,
                )

            if failure_count >= FAILURE_THRESHOLD:
                promote_to_active()

            time.sleep(HEARTBEAT_INTERVAL)


def emit_client_thread_error(session, exc):
    """Envia erro generico ao cliente se a thread dedicada falhar."""
    socketio.emit(
        "chat_error",
        {"message": "Erro interno ao processar evento."},
        to=session.sid,
    )


def remove_client_state(sid):
    """Remove usuario local e propaga a nova lista para os clientes."""
    with state_lock:
        user = connected_users.pop(sid, None)

    active = backup_is_active()
    if active and user:
        logging.info(
            "CHAT_USUARIO_SAIU role=backup sid=%s usuario=%s",
            sid,
            user["name"],
        )
        socketio.emit(
            "chat_notification",
            notification_payload(f"{user['name']} saiu do chat."),
        )
        socketio.emit("users_update", users_snapshot())


def handle_client_join(session):
    """Processa entrada no chat dentro da thread dedicada do cliente."""
    active = backup_is_active()
    if not active:
        socketio.emit(
            "chat_error",
            {"message": "Backup ainda nao esta ativo."},
            to=session.sid,
        )
        return

    with state_lock:
        previous = connected_users.get(session.sid)
        connected_users[session.sid] = {
            "user_id": session.user_id,
            "name": session.name,
            "joined_at": previous["joined_at"] if previous else now_iso(),
        }

    if not previous:
        logging.info(
            "CHAT_USUARIO_ENTROU role=backup sid=%s usuario=%s",
            session.sid,
            session.name,
        )
        socketio.emit(
            "chat_notification",
            notification_payload(f"{session.name} entrou no chat."),
            skip_sid=session.sid,
        )

    socketio.emit("message_history", latest_messages(MAX_HISTORY), to=session.sid)
    socketio.emit("users_update", users_snapshot())


def handle_client_send_message(session, data):
    """Valida, salva e retransmite uma mensagem quando o backup esta ativo."""
    active = backup_is_active()
    if not active:
        socketio.emit(
            "chat_error",
            {"message": "Backup ainda nao esta ativo."},
            to=session.sid,
        )
        return

    with state_lock:
        user = connected_users.get(session.sid)

    if not user:
        socketio.emit(
            "chat_error",
            {"message": "Entre no chat antes de enviar mensagens."},
            to=session.sid,
        )
        return

    text = clean_text(data.get("text") if isinstance(data, dict) else "", 1000)
    if not text:
        socketio.emit(
            "chat_error",
            {"message": "A mensagem nao pode estar vazia."},
            to=session.sid,
        )
        return

    message = {
        "id": str(uuid4()),
        "type": "user",
        "user": user["name"],
        "text": text,
        "timestamp": now_iso(),
    }
    message = store_message(message, user_id=user["user_id"])
    logging.info(
        "CHAT_MENSAGEM_RECEBIDA role=backup sid=%s usuario=%s tamanho=%s",
        session.sid,
        user["name"],
        len(text),
    )
    socketio.emit("chat_message", message)


def handle_client_restore_primary(session):
    """Aciona a restauracao do primario a partir do botao de controle."""
    if not ENABLE_FAILOVER_CONTROL:
        socketio.emit(
            "chat_error",
            {"message": "Controle de failover desativado."},
            to=session.sid,
        )
        return

    if not backup_is_active():
        socketio.emit(
            "chat_error",
            {"message": "Backup nao esta ativo."},
            to=session.sid,
        )
        return

    if not restore_primary():
        socketio.emit(
            "chat_error",
            {"message": "Nao foi possivel restaurar o primario."},
            to=session.sid,
        )
        return

    logging.info(
        "RESTAURACAO_PRIMARIO_CONCLUIDA usuario=%s sid=%s",
        session.name,
        session.sid,
    )
    socketio.emit("users_update", users_snapshot())
    socketio.emit(
        "primary_restored",
        {"role": "primary", "active": True, "server_url": PRIMARY_PUBLIC_URL},
    )


def dispatch_backup_client_event(session, event):
    """Entrega cada evento da fila para a regra de negocio correta."""
    event_type = event.get("type")

    if event_type == "join":
        handle_client_join(session)
    elif event_type == "send_message":
        handle_client_send_message(session, event.get("data") or {})
    elif event_type == "restore_primary":
        handle_client_restore_primary(session)
    elif event_type == "disconnect":
        remove_client_state(session.sid)
    else:
        socketio.emit(
            "chat_error",
            {"message": "Evento desconhecido."},
            to=session.sid,
        )


client_threads = ClientThreadManager(
    app,
    role="backup",
    dispatch_event=dispatch_backup_client_event,
    error_handler=emit_client_thread_error,
)


@app.route("/")
@login_required
def index():
    """Entrega a mesma tela do chat, apontando o Worker para primario/backup."""
    return render_template(
        "index.html",
        primary_url=PRIMARY_PUBLIC_URL,
        backup_url=BACKUP_PUBLIC_URL,
        current_username=current_user.username,
        socket_auth_token=generate_socket_token(current_user),
        failover_control_enabled=False,
        failover_url=None,
        restore_control_enabled=ENABLE_FAILOVER_CONTROL and SHOW_FAILOVER_CONTROLS,
    )


@app.route("/health")
def health():
    """Endpoint usado pelo primario, Render e logs para diagnosticar o backup."""
    with state_lock:
        return jsonify(
            {
                "ok": True,
                "role": "backup",
                "active": backup_is_active(),
                "failures": failure_count,
                "users": len(connected_users),
                "replicated_primary_users": len(replicated_primary_users),
                "client_threads": client_threads.count(),
                "time": now_iso(),
            }
        )


@app.route("/ready")
def ready():
    """Health check rapido para o Render saber que o processo subiu."""
    return jsonify({"ok": True, "role": "backup"})


@app.route("/replicate", methods=["POST"])
def replicate():
    """Recebe eventos replicados pelo primario enquanto o backup esta em espera."""
    payload = request.get_json(silent=True) or {}
    if payload.get("token") != REPLICATION_TOKEN:
        return jsonify({"ok": False, "error": "token invalido"}), 403

    event = payload.get("event")
    data = payload.get("payload")

    with state_lock:
        # Quando o backup ja assumiu, ele para de aceitar replicacao antiga para
        # nao sobrescrever seu proprio estado ativo.
        if is_active:
            return jsonify({"ok": True, "ignored": "backup ja esta ativo"})

    if event == "message" and isinstance(data, dict):
        add_message(data)
        logging.info("REPLICACAO_RECEBIDA evento=message")
    elif event == "users_snapshot" and isinstance(data, list):
        with state_lock:
            replicated_primary_users[:] = data
        logging.info("REPLICACAO_RECEBIDA evento=users_snapshot usuarios=%s", len(data))
    else:
        return jsonify({"ok": False, "error": "evento desconhecido"}), 400

    return jsonify({"ok": True})


@app.route("/promote", methods=["POST"])
def promote():
    """Permite que o primario solicite failover manualmente."""
    payload = request.get_json(silent=True) or {}
    if payload.get("token") != REPLICATION_TOKEN:
        return jsonify({"ok": False, "error": "token invalido"}), 403

    logging.warning("FAILOVER_PROMOTE_RECEBIDO origem=primario")
    promote_to_active(reason="promocao_manual")
    return jsonify(
        {
            "ok": True,
            "role": "backup",
            "active": True,
            "server_url": BACKUP_PUBLIC_URL,
        }
    )


@app.route("/messages")
@login_required
def messages():
    """Consulta simples do historico persistido no banco compartilhado."""
    return jsonify(latest_messages(MAX_HISTORY))


@socketio.on("connect")
def handle_connect(auth=None):
    """Cria a thread dedicada para a conexao Socket.IO aceita pelo backup."""
    user = socket_user_from_auth(auth)
    if not user:
        emit("chat_error", {"message": "Entre para acessar o chat."})
        return False

    active = backup_is_active()
    client_threads.start(request.sid, user.id, user.username)

    emit(
        "server_info",
        {
            "role": "backup",
            "active": active,
            "server_url": BACKUP_PUBLIC_URL,
        },
    )

    if not active:
        emit(
            "chat_error",
            {"message": "Backup em espera. Tentando servidor principal."},
        )


@socketio.on("join")
def handle_join(data=None):
    """Coloca o evento de entrada na fila da thread do cliente."""
    if not client_threads.enqueue(request.sid, {"type": "join"}):
        emit("chat_error", {"message": "Entre para acessar o chat."})


@socketio.on("send_message")
def handle_send_message(data):
    """Coloca o envio de mensagem na fila da thread dedicada."""
    if not client_threads.enqueue(
        request.sid,
        {"type": "send_message", "data": data or {}},
    ):
        emit("chat_error", {"message": "Entre para acessar o chat."})


@socketio.on("restore_primary")
def handle_restore_primary():
    """Encaminha o pedido de restauracao para a thread do solicitante."""
    if not client_threads.enqueue(request.sid, {"type": "restore_primary"}):
        emit("chat_error", {"message": "Entre para acessar o chat."})


@socketio.on("disconnect")
def handle_disconnect():
    """Encerra a thread dedicada e limpa o estado do usuario."""
    if not client_threads.stop(request.sid):
        remove_client_state(request.sid)


@socketio.on_error_default
def default_error_handler(exc):
    logging.exception("Erro no Socket.IO do backup: %s", exc)
    emit("chat_error", {"message": "Erro interno ao processar evento."})


def start_background_threads():
    """Inicia a thread global de heartbeat uma unica vez por processo."""
    global background_threads_started
    if background_threads_started:
        return
    background_threads_started = True
    threading.Thread(target=heartbeat_worker, daemon=True, name="heartbeat-worker").start()


if __name__ == "__main__":
    raise SystemExit(
        "Use o startCommand do Render: gunicorn -c gunicorn.conf.py backup_wsgi:app"
    )
