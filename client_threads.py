import logging
import queue
import threading


class ClientSession:
    """Representa uma conexao ativa de um cliente no servidor."""

    def __init__(self, sid, user_id, name):
        # O sid e o identificador unico da conexao Socket.IO daquele cliente.
        self.sid = sid
        self.user_id = user_id
        self.name = name

        # Cada cliente tem uma fila propria. Os handlers Socket.IO colocam
        # eventos aqui, e a thread dedicada processa um evento por vez.
        self.queue = queue.Queue()

        # Sinal usado para pedir o encerramento seguro da thread do cliente.
        self.stop_event = threading.Event()
        self.thread = None


class ClientThreadManager:
    """
    Gerencia as threads dedicadas dos clientes conectados.

    Cada conexao cria uma thread
    propria. Essa thread fica viva enquanto o cliente estiver conectado e
    processa os eventos recebidos pela fila da sua ClientSession.
    """

    def __init__(self, app, role, dispatch_event, error_handler=None):
        self.app = app
        self.role = role
        self.dispatch_event = dispatch_event
        self.error_handler = error_handler

        # sessions guarda sid -> ClientSession para localizar a thread/fila de
        # cada conexao ativa.
        self.sessions = {}

        # O lock protege o dicionario de sessoes porque Flask-SocketIO/Gunicorn
        # pode chamar handlers em threads diferentes.
        self.lock = threading.RLock()

    def start(self, sid, user_id, name):
        """Cria e inicia uma thread dedicada para uma nova conexao."""
        session = ClientSession(sid=sid, user_id=user_id, name=name)
        session.thread = threading.Thread(
            target=self._run_session,
            args=(session,),
            daemon=True,
            name=f"{self.role}-client-{sid[:8]}",
        )

        with self.lock:
            self.sessions[sid] = session

        session.thread.start()
        return session

    def enqueue(self, sid, event):
        """Envia um evento para a thread do cliente identificado pelo sid."""
        with self.lock:
            session = self.sessions.get(sid)

        if not session:
            return False

        session.queue.put(event)
        return True

    def stop(self, sid):
        """Solicita o encerramento da thread dedicada de um cliente."""
        with self.lock:
            session = self.sessions.get(sid)

        if not session:
            return False

        session.stop_event.set()

        # Ao desconectar, descartamos eventos pendentes e mantemos apenas o
        # evento final de disconnect para limpar o estado do usuario.
        with session.queue.mutex:
            session.queue.queue.clear()
        session.queue.put(
            {
                "type": "disconnect",
                "reason": "cliente desconectou ou reconectou",
            }
        )
        return True

    def count(self):
        """Retorna quantas threads de clientes estao registradas."""
        with self.lock:
            return len(self.sessions)

    def _run_session(self, session):
        """
        Loop executado dentro da thread dedicada do cliente.

        Ele cria contexto Flask para permitir acesso seguro a recursos da app,
        espera eventos na fila e delega o processamento para o callback
        especifico do servidor primario ou backup.
        """
        logging.info(
            "THREAD_CLIENTE_INICIADA role=%s sid=%s usuario=%s",
            self.role,
            session.sid,
            session.name,
        )

        with self.app.app_context():
            close_reason = "encerramento solicitado"
            while True:
                try:
                    event = session.queue.get(timeout=1)
                except queue.Empty:
                    if session.stop_event.is_set():
                        break
                    continue

                try:
                    event_type = event.get("type")
                    logging.info(
                        "THREAD_CLIENTE_EVENTO role=%s sid=%s usuario=%s evento=%s",
                        self.role,
                        session.sid,
                        session.name,
                        event_type,
                    )
                    self.dispatch_event(session, event)
                    if event_type == "disconnect":
                        close_reason = event.get("reason") or "disconnect recebido"
                        break
                except Exception as exc:
                    logging.exception(
                        "THREAD_CLIENTE_ERRO role=%s sid=%s usuario=%s",
                        self.role,
                        session.sid,
                        session.name,
                    )
                    if self.error_handler:
                        self.error_handler(session, exc)
                finally:
                    session.queue.task_done()

        with self.lock:
            self.sessions.pop(session.sid, None)

        logging.info(
            "THREAD_CLIENTE_ENCERRADA role=%s sid=%s usuario=%s motivo=%s",
            self.role,
            session.sid,
            session.name,
            close_reason,
        )
