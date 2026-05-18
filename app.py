import logging
import os
import queue
import signal
import threading
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
BACKUP_INTERNAL_URL = normalize_service_url(required_env("BACKUP_INTERNAL_URL"))
BACKUP_REPLICATION_URL = os.getenv(
    "BACKUP_REPLICATION_URL", f"{BACKUP_INTERNAL_URL}/replicate"
)
BACKUP_PROMOTE_URL = os.getenv("BACKUP_PROMOTE_URL", f"{BACKUP_INTERNAL_URL}/promote")
REPLICATION_TOKEN = required_env("REPLICATION_TOKEN")
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

state_lock = threading.RLock()
connected_users = {}
authenticated_sockets = {}
background_threads_started = False

replication_queue = queue.Queue()
stop_threads = threading.Event()


def clean_text(value, limit):
    value = str(value or "").strip()
    return value[:limit]


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


def add_message(message, user_id=None):
    return store_message(message, user_id=user_id)


def notification_payload(text, level="info"):
    return {
        "id": str(uuid4()),
        "type": "notification",
        "level": level,
        "text": text,
        "timestamp": now_iso(),
    }


def primary_is_active():
    return active_role() == PRIMARY_ROLE


def socket_user_from_auth(auth):
    if current_user.is_authenticated:
        return current_user

    token = auth.get("token") if isinstance(auth, dict) else None
    return load_socket_user(token)


def shutdown_primary_process():
    logging.warning("Primario encerrando por failover manual.")
    stop_threads.set()
    os.kill(os.getpid(), signal.SIGTERM)


def replicate_event(event, payload):
    """Coloca eventos em uma fila para replicacao assincrona ao backup."""
    replication_queue.put(
        {
            "token": REPLICATION_TOKEN,
            "event": event,
            "payload": payload,
            "created_at": now_iso(),
        }
    )


def replication_worker():
    """
    Thread de replicacao.

    Ela evita que uma falha ou lentidao do servidor de backup bloqueie o envio
    de mensagens em tempo real no servidor principal.
    """
    while not stop_threads.is_set():
        try:
            item = replication_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            response = requests.post(BACKUP_REPLICATION_URL, json=item, timeout=2)
            if response.status_code >= 400:
                logging.warning(
                    "Backup recusou replicacao %s: HTTP %s",
                    item["event"],
                    response.status_code,
                )
        except requests.RequestException as exc:
            logging.warning("Backup indisponivel para replicacao: %s", exc)
        finally:
            replication_queue.task_done()


def monitor_worker():
    """
    Thread de monitoramento do servidor.

    O Flask-SocketIO ja gerencia heartbeats dos clientes via ping/pong. Esta
    thread registra periodicamente o estado do principal e verifica se o backup
    esta acessivel, oferecendo observabilidade e tolerancia a falhas.
    """
    while not stop_threads.is_set():
        try:
            response = requests.get(f"{BACKUP_INTERNAL_URL}/health", timeout=2)
            backup_state = response.json() if response.ok else {"error": response.status_code}
        except requests.RequestException as exc:
            backup_state = {"error": str(exc)}

        logging.info(
            "Principal ativo. Usuarios=%s Backup=%s",
            len(users_snapshot()),
            backup_state,
        )
        stop_threads.wait(30)


def emit_client_thread_error(session, exc):
    socketio.emit(
        "chat_error",
        {"message": "Erro interno ao processar evento."},
        to=session.sid,
    )


def remove_client_state(sid):
    with state_lock:
        authenticated_sockets.pop(sid, None)
        user = connected_users.pop(sid, None)

    if not user:
        return

    socketio.emit(
        "chat_notification",
        notification_payload(f"{user['name']} saiu do chat."),
    )

    snapshot = users_snapshot()
    socketio.emit("users_update", snapshot)
    replicate_event("users_snapshot", snapshot)


def handle_client_join(session):
    if not primary_is_active():
        socketio.emit(
            "chat_error",
            {"message": "Servidor primario em espera."},
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
        socketio.emit(
            "chat_notification",
            notification_payload(f"{session.name} entrou no chat."),
            skip_sid=session.sid,
        )

    socketio.emit("message_history", latest_messages(MAX_HISTORY), to=session.sid)

    snapshot = users_snapshot()
    socketio.emit("users_update", snapshot)
    replicate_event("users_snapshot", snapshot)


def handle_client_send_message(session, data):
    if not primary_is_active():
        socketio.emit(
            "chat_error",
            {"message": "Servidor primario em espera."},
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

    with state_lock:
        user = connected_users.get(session.sid)

    if not user:
        socketio.emit(
            "chat_error",
            {"message": "Entre no chat antes de enviar mensagens."},
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
    message = add_message(message, user_id=user["user_id"])

    socketio.emit("chat_message", message)
    replicate_event("message", message)


def dispatch_primary_client_event(session, event):
    event_type = event.get("type")

    if event_type == "join":
        handle_client_join(session)
    elif event_type == "send_message":
        handle_client_send_message(session, event.get("data") or {})
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
    role="primary",
    dispatch_event=dispatch_primary_client_event,
    error_handler=emit_client_thread_error,
)


@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        server_role="primary",
        primary_url=PRIMARY_PUBLIC_URL,
        backup_url=BACKUP_PUBLIC_URL,
        current_username=current_user.username,
        socket_auth_token=generate_socket_token(current_user),
        failover_control_enabled=ENABLE_FAILOVER_CONTROL and SHOW_FAILOVER_CONTROLS,
        failover_url="/failover",
        restore_control_enabled=ENABLE_FAILOVER_CONTROL and SHOW_FAILOVER_CONTROLS,
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "role": "primary",
            "active": primary_is_active(),
            "users": len(users_snapshot()),
            "client_threads": client_threads.count(),
            "time": now_iso(),
        }
    )


@app.route("/messages")
@login_required
def messages():
    return jsonify(latest_messages(MAX_HISTORY))


@app.route("/failover", methods=["POST"])
@login_required
def trigger_failover():
    if not ENABLE_FAILOVER_CONTROL:
        return jsonify({"ok": False, "error": "controle de failover desativado"}), 404

    logging.warning("Failover manual solicitado por %s.", current_user.username)

    payload = {
        "token": REPLICATION_TOKEN,
        "reason": "manual_failover",
        "created_at": now_iso(),
    }

    try:
        response = requests.post(BACKUP_PROMOTE_URL, json=payload, timeout=2)
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("Nao foi possivel promover o backup manualmente: %s", exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Nao foi possivel acionar o backup.",
                }
            ),
            503,
        )

    set_active_role(BACKUP_ROLE)
    threading.Timer(0.7, shutdown_primary_process).start()
    return jsonify(
        {
            "ok": True,
            "message": "Backup promovido. Primario sera encerrado.",
            "backup_url": BACKUP_PUBLIC_URL,
        }
    )


@socketio.on("connect")
def handle_connect(auth=None):
    user = socket_user_from_auth(auth)
    if not user:
        emit("chat_error", {"message": "Entre para acessar o chat."})
        return False

    active = primary_is_active()

    with state_lock:
        authenticated_sockets[request.sid] = {
            "user_id": user.id,
            "name": user.username,
        }

    client_threads.start(request.sid, user.id, user.username)

    emit(
        "server_info",
        {
            "role": "primary",
            "active": active,
            "server_url": PRIMARY_PUBLIC_URL,
        },
    )


@socketio.on("join")
def handle_join(data=None):
    if not client_threads.enqueue(request.sid, {"type": "join"}):
        emit("chat_error", {"message": "Entre para acessar o chat."})


@socketio.on("send_message")
def handle_send_message(data):
    if not client_threads.enqueue(
        request.sid,
        {"type": "send_message", "data": data or {}},
    ):
        emit("chat_error", {"message": "Entre para acessar o chat."})


@socketio.on("disconnect")
def handle_disconnect():
    if not client_threads.stop(request.sid):
        remove_client_state(request.sid)


@socketio.on_error_default
def default_error_handler(exc):
    logging.exception("Erro no Socket.IO: %s", exc)
    emit("chat_error", {"message": "Erro interno ao processar evento."})


def start_background_threads():
    global background_threads_started
    if background_threads_started:
        return
    background_threads_started = True
    threading.Thread(target=replication_worker, daemon=True, name="replication-worker").start()
    threading.Thread(target=monitor_worker, daemon=True, name="monitor-worker").start()


if __name__ == "__main__":
    raise SystemExit("Use o startCommand do Render: gunicorn -c gunicorn.conf.py wsgi:app")
