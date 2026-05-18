import logging
import queue
import threading


class ClientSession:
    def __init__(self, sid, user_id, name):
        self.sid = sid
        self.user_id = user_id
        self.name = name
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None


class ClientThreadManager:
    def __init__(self, app, role, dispatch_event, error_handler=None):
        self.app = app
        self.role = role
        self.dispatch_event = dispatch_event
        self.error_handler = error_handler
        self.sessions = {}
        self.lock = threading.RLock()

    def start(self, sid, user_id, name):
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
        with self.lock:
            session = self.sessions.get(sid)

        if not session:
            return False

        session.queue.put(event)
        return True

    def stop(self, sid):
        with self.lock:
            session = self.sessions.get(sid)

        if not session:
            return False

        session.stop_event.set()
        with session.queue.mutex:
            session.queue.queue.clear()
        session.queue.put({"type": "disconnect"})
        return True

    def count(self):
        with self.lock:
            return len(self.sessions)

    def _run_session(self, session):
        logging.info(
            "Thread dedicada iniciada para cliente sid=%s user=%s role=%s",
            session.sid,
            session.name,
            self.role,
        )

        with self.app.app_context():
            while True:
                try:
                    event = session.queue.get(timeout=1)
                except queue.Empty:
                    if session.stop_event.is_set():
                        break
                    continue

                try:
                    event_type = event.get("type")
                    self.dispatch_event(session, event)
                    if event_type == "disconnect":
                        break
                except Exception as exc:
                    logging.exception(
                        "Erro na thread do cliente sid=%s role=%s",
                        session.sid,
                        self.role,
                    )
                    if self.error_handler:
                        self.error_handler(session, exc)
                finally:
                    session.queue.task_done()

        with self.lock:
            self.sessions.pop(session.sid, None)

        logging.info(
            "Thread dedicada encerrada para cliente sid=%s user=%s role=%s",
            session.sid,
            session.name,
            self.role,
        )
