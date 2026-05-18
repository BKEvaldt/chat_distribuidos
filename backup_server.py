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
from extensions import db, login_manager, migrate
from server_state import BACKUP_ROLE, PRIMARY_ROLE, active_role, set_active_role
from socket_auth import generate_socket_token, load_socket_user


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variavel de ambiente obrigatoria ausente: {name}")
    return value


def database_uri():
    url = required_env("DATABASE_URL")
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def normalize_service_url(url):
    if url and not url.startswith(("http://", "https://")):
        return f"http://{url}"
    return url


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
SHOW_FAILOVER_CONTROLS = os.getenv("SHOW_FAILOVER_CONTROLS", "0").lower() in {
    "1",
    "true",
    "yes",
}
PRIMARY_RESTORE_TIMEOUT = float(os.getenv("PRIMARY_RESTORE_TIMEOUT", "30"))

state_lock = threading.RLock()
restore_lock = threading.Lock()
is_active = False
failure_count = 0
connected_users = {}
authenticated_sockets = {}
replicated_primary_users = []
background_threads_started = False


def clean_text(value, limit):
    value = str(value or "").strip()
    return value[:limit]


def add_message(message):
    return store_message(message)


def notification_payload(text, level="info"):
    return {
        "id": str(uuid4()),
        "type": "notification",
        "level": level,
        "text": text,
        "timestamp": now_iso(),
    }


def primary_is_healthy():
    try:
        response = requests.get(PRIMARY_HEALTH_URL, timeout=1.5)
        return response.ok and response.json().get("active") is True
    except requests.RequestException:
        return False


def wait_for_primary(timeout=PRIMARY_RESTORE_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if primary_is_healthy():
            return True
        time.sleep(0.4)
    return primary_is_healthy()


def backup_is_active():
    global is_active
    active = active_role() == BACKUP_ROLE
    with state_lock:
        is_active = active
    return active


def demote_to_standby():
    global failure_count, is_active
    with state_lock:
        is_active = False
        failure_count = 0
        connected_users.clear()


def restore_primary():
    with restore_lock:
        set_active_role(PRIMARY_ROLE)
        if not wait_for_primary():
            set_active_role(BACKUP_ROLE)
            return False

        demote_to_standby()
        return True


def socket_user_from_auth(auth):
    if current_user.is_authenticated:
        return current_user

    token = auth.get("token") if isinstance(auth, dict) else None
    return load_socket_user(token)


def socket_identity():
    with state_lock:
        return authenticated_sockets.get(request.sid)


def users_snapshot():
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


def promote_to_active():
    global failure_count, is_active
    with state_lock:
        if is_active:
            return
        is_active = True
        failure_count = max(failure_count, FAILURE_THRESHOLD)
        connected_users.clear()

    set_active_role(BACKUP_ROLE)

    logging.warning("Backup promovido para servidor ativo.")
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
                    logging.info("Heartbeat OK do principal.")
                else:
                    failure_count += 1
                    logging.warning(
                        "Heartbeat invalido do principal. Falhas=%s",
                        failure_count,
                    )
            except requests.RequestException as exc:
                failure_count += 1
                logging.warning("Heartbeat falhou: %s. Falhas=%s", exc, failure_count)

            if failure_count >= FAILURE_THRESHOLD:
                promote_to_active()

            time.sleep(HEARTBEAT_INTERVAL)


@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        server_role="backup",
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
    with state_lock:
        return jsonify(
            {
                "ok": True,
                "role": "backup",
                "active": backup_is_active(),
                "failures": failure_count,
                "users": len(connected_users),
                "replicated_primary_users": len(replicated_primary_users),
                "time": now_iso(),
            }
        )


@app.route("/replicate", methods=["POST"])
def replicate():
    payload = request.get_json(silent=True) or {}
    if payload.get("token") != REPLICATION_TOKEN:
        return jsonify({"ok": False, "error": "token invalido"}), 403

    event = payload.get("event")
    data = payload.get("payload")

    with state_lock:
        if is_active:
            return jsonify({"ok": True, "ignored": "backup ja esta ativo"})

    if event == "message" and isinstance(data, dict):
        add_message(data)
    elif event == "users_snapshot" and isinstance(data, list):
        with state_lock:
            replicated_primary_users[:] = data
    else:
        return jsonify({"ok": False, "error": "evento desconhecido"}), 400

    return jsonify({"ok": True})


@app.route("/promote", methods=["POST"])
def promote():
    payload = request.get_json(silent=True) or {}
    if payload.get("token") != REPLICATION_TOKEN:
        return jsonify({"ok": False, "error": "token invalido"}), 403

    promote_to_active()
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
    return jsonify(latest_messages(MAX_HISTORY))


@socketio.on("connect")
def handle_connect(auth=None):
    user = socket_user_from_auth(auth)
    if not user:
        emit("chat_error", {"message": "Entre para acessar o chat."})
        return False

    active = backup_is_active()
    with state_lock:
        authenticated_sockets[request.sid] = {
            "user_id": user.id,
            "name": user.username,
        }

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
    identity = socket_identity()
    if not identity:
        emit("chat_error", {"message": "Entre para acessar o chat."})
        return

    active = backup_is_active()
    if not active:
        emit("chat_error", {"message": "Backup ainda nao esta ativo."})
        return

    name = identity["name"]
    with state_lock:
        previous = connected_users.get(request.sid)
        connected_users[request.sid] = {
            "user_id": identity["user_id"],
            "name": name,
            "joined_at": previous["joined_at"] if previous else now_iso(),
        }

    if not previous:
        socketio.emit(
            "chat_notification",
            notification_payload(f"{name} entrou no chat."),
            skip_sid=request.sid,
        )

    emit("message_history", latest_messages(MAX_HISTORY))

    socketio.emit("users_update", users_snapshot())


@socketio.on("send_message")
def handle_send_message(data):
    if not socket_identity():
        emit("chat_error", {"message": "Entre para acessar o chat."})
        return

    with state_lock:
        user = connected_users.get(request.sid)

    active = backup_is_active()
    if not active:
        emit("chat_error", {"message": "Backup ainda nao esta ativo."})
        return

    if not user:
        emit("chat_error", {"message": "Entre no chat antes de enviar mensagens."})
        return

    text = clean_text(data.get("text") if isinstance(data, dict) else "", 1000)
    if not text:
        emit("chat_error", {"message": "A mensagem nao pode estar vazia."})
        return

    message = {
        "id": str(uuid4()),
        "type": "user",
        "user": user["name"],
        "text": text,
        "timestamp": now_iso(),
    }
    message = store_message(message, user_id=user["user_id"])
    socketio.emit("chat_message", message)


@socketio.on("restore_primary")
def handle_restore_primary():
    if not socket_identity():
        emit("chat_error", {"message": "Entre para acessar o chat."})
        return

    if not ENABLE_FAILOVER_CONTROL:
        emit("chat_error", {"message": "Controle de failover desativado."})
        return

    if not backup_is_active():
        emit("chat_error", {"message": "Backup nao esta ativo."})
        return

    if not restore_primary():
        emit("chat_error", {"message": "Nao foi possivel restaurar o primario."})
        return

    logging.info("Servidor primario restaurado. Clientes serao reconectados.")
    socketio.emit("users_update", users_snapshot())
    socketio.emit(
        "primary_restored",
        {"role": "primary", "active": True, "server_url": PRIMARY_PUBLIC_URL},
    )


@socketio.on("disconnect")
def handle_disconnect():
    with state_lock:
        authenticated_sockets.pop(request.sid, None)
        user = connected_users.pop(request.sid, None)

    active = backup_is_active()
    if active and user:
        socketio.emit(
            "chat_notification",
            notification_payload(f"{user['name']} saiu do chat."),
        )
        socketio.emit("users_update", users_snapshot())


@socketio.on_error_default
def default_error_handler(exc):
    logging.exception("Erro no Socket.IO do backup: %s", exc)
    emit("chat_error", {"message": "Erro interno ao processar evento."})


def start_background_threads():
    global background_threads_started
    if background_threads_started:
        return
    background_threads_started = True
    threading.Thread(target=heartbeat_worker, daemon=True, name="heartbeat-worker").start()


if __name__ == "__main__":
    raise SystemExit(
        "Use o startCommand do Render: gunicorn -c gunicorn.conf.py backup_wsgi:app"
    )
