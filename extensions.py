"""Extensoes Flask compartilhadas entre os modulos da aplicacao."""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy


# Criamos as extensoes sem app aqui para evitar import circular. Cada servidor
# chama init_app(...) depois de criar sua propria instancia Flask.
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
