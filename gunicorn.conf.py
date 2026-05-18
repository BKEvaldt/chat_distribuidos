"""Configuracao compartilhada pelos dois servicos Gunicorn no Render."""

import os


# O Render injeta PORT. Se a variavel nao existir, usamos 10000 para manter
# compatibilidade com a configuracao padrao do projeto.
bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"

# Mantemos um worker para preservar o estado em memoria das conexoes Socket.IO.
workers = 1

# As threads do Gunicorn atendem requisicoes simultaneas; as threads por
# cliente do trabalho ficam em client_threads.py.
threads = int(os.getenv("WEB_CONCURRENCY_THREADS", "100"))

# Logs em stdout/stderr deixam tudo visivel no painel do Render.
accesslog = "-"
errorlog = "-"
preload_app = False
