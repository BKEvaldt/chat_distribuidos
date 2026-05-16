# Chat Distribuido no Render

Aplicacao de chat em tempo real com Flask, Flask-SocketIO, login, PostgreSQL e failover entre dois web services no Render.

Este projeto esta configurado para rodar no Render via `render.yaml`. Ele nao depende mais de SQLite nem de execucao local com `python app.py`.

## Arquitetura

O Blueprint cria:

- `chat-distribuido-primary`: servidor principal.
- `chat-distribuido-backup`: servidor secundario.
- `chat-distribuido-db`: PostgreSQL compartilhado.
- `chat-distribuido-shared`: variaveis compartilhadas entre os dois servicos.

O banco esta com `plan: free` no `render.yaml`. No Render, bancos PostgreSQL gratuitos expiram depois de 30 dias; para manter dados por mais tempo, troque o plano do banco no Blueprint ou no dashboard.

O navegador acessa a URL publica do primario. O primario replica mensagens para o backup pela rede privada do Render. O backup monitora o primario por heartbeat e assume quando o primario falha.

## Estrutura

```text
.
├── app.py
├── auth.py
├── backup_server.py
├── backup_wsgi.py
├── chat_storage.py
├── extensions.py
├── gunicorn.conf.py
├── models.py
├── render.yaml
├── requirements.txt
├── server_state.py
├── socket_auth.py
├── wsgi.py
├── migrations/
├── templates/
└── static/
```

## Deploy

1. Publique este diretorio em um repositorio GitHub ou GitLab.
2. No Render, crie um novo **Blueprint** apontando para esse repositorio.
3. Confirme os recursos definidos em `render.yaml`.
4. Aguarde o deploy do PostgreSQL e dos dois web services.
5. Acesse:

```text
https://chat-distribuido-primary.onrender.com
```

O Blueprint usa:

```bash
pip install -r requirements.txt
flask --app app db upgrade
gunicorn -c gunicorn.conf.py wsgi:app
gunicorn -c gunicorn.conf.py backup_wsgi:app
```

## URLs

O `render.yaml` assume estas URLs publicas:

```text
https://chat-distribuido-primary.onrender.com
https://chat-distribuido-backup.onrender.com
```

Se o Render gerar subdominios diferentes, ajuste no dashboard:

```bash
PRIMARY_PUBLIC_URL
BACKUP_PUBLIC_URL
```

As variaveis `PRIMARY_INTERNAL_URL` e `BACKUP_INTERNAL_URL` sao preenchidas pelo Blueprint com `fromService` e `property: hostport`, usando a rede privada do Render.

## Banco

O banco e PostgreSQL no Render. As migrations ficam em `migrations/` e sao aplicadas automaticamente pelo `preDeployCommand` do servico primario:

```bash
flask --app app db upgrade
```

Tabelas:

- `users`: usuarios cadastrados e hash de senha.
- `messages`: historico de mensagens.
- `server_state`: indica se o servidor ativo atual e `primary` ou `backup`.

## Login Entre Dominios

Como primario e backup ficam em dominios diferentes, o cookie de login do primario nao acompanha automaticamente a conexao Socket.IO no backup.

Por isso, a pagina gera um token assinado em `socket_auth.py`. Esse token e enviado no `auth` do Socket.IO e permite que o backup identifique o usuario durante o failover.

## Failover

Com `ENABLE_FAILOVER_CONTROL=1`, a interface do primario mostra **Derrubar primario**.

Ao clicar:

1. o primario chama `POST /promote` no backup pela rede privada;
2. o backup grava `backup` em `server_state`;
3. o primario encerra o proprio processo;
4. o Render reinicia o servico primario;
5. como `server_state=backup`, o primario reiniciado fica em espera;
6. o navegador reconecta ao backup.

No backup aparece **Voltar ao primario**.

Ao clicar:

1. o backup grava `primary` em `server_state`;
2. espera o `/health` do primario confirmar que esta ativo;
3. volta para modo de espera;
4. manda os navegadores reconectarem ao primario.

## Variaveis

As principais variaveis sao definidas em `render.yaml`:

```bash
DATABASE_URL
SECRET_KEY
REPLICATION_TOKEN
PRIMARY_PUBLIC_URL
BACKUP_PUBLIC_URL
PRIMARY_INTERNAL_URL
BACKUP_INTERNAL_URL
ENABLE_FAILOVER_CONTROL
HEARTBEAT_INTERVAL
FAILURE_THRESHOLD
MAX_HISTORY
SOCKET_AUTH_MAX_AGE
PRIMARY_RESTORE_TIMEOUT
```

`DATABASE_URL`, `SECRET_KEY`, `REPLICATION_TOKEN`, `PRIMARY_PUBLIC_URL`, `BACKUP_PUBLIC_URL`, `PRIMARY_INTERNAL_URL` e `BACKUP_INTERNAL_URL` sao obrigatorias. A aplicacao falha ao iniciar se alguma delas estiver ausente.

## Observacao

Este projeto demonstra failover em nivel de aplicacao no Render. Para uma arquitetura de producao com varias instancias simultaneas de Socket.IO, adicione Redis como message queue/adaptador.
