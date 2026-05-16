from urllib.parse import urlsplit

from flask import redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from extensions import db, login_manager
from models import User


@login_manager.user_loader
def load_user(user_id):
    if not str(user_id).isdigit():
        return None
    return db.session.get(User, int(user_id))


def clean_username(value):
    return str(value or "").strip()[:32]


def is_safe_next(target):
    if not target:
        return False
    parsed = urlsplit(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")


def register_auth_routes(app):
    login_manager.login_view = "login"
    login_manager.login_message = "Entre para acessar o chat."

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        error = None
        if request.method == "POST":
            username = clean_username(request.form.get("username"))
            password = request.form.get("password") or ""
            user = User.query.filter_by(username_key=username.casefold()).first()

            if user and user.check_password(password):
                login_user(user, remember=bool(request.form.get("remember")))
                next_page = request.args.get("next")
                return redirect(next_page if is_safe_next(next_page) else url_for("index"))

            error = "Usuario ou senha invalidos."

        return render_template("login.html", error=error)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        error = None
        if request.method == "POST":
            username = clean_username(request.form.get("username"))
            password = request.form.get("password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if len(username) < 3:
                error = "O usuario precisa ter pelo menos 3 caracteres."
            elif len(password) < 6:
                error = "A senha precisa ter pelo menos 6 caracteres."
            elif password != confirm_password:
                error = "As senhas nao conferem."
            elif User.query.filter_by(username_key=username.casefold()).first():
                error = "Este usuario ja existe."
            else:
                user = User(username=username, username_key=username.casefold())
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                login_user(user)
                return redirect(url_for("index"))

        return render_template("register.html", error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        logout_user()
        return redirect(url_for("login"))
