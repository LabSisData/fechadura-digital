"""Interface web exclusivamente local; não expor na rede sem projeto de segurança."""
from collections import deque
from datetime import timedelta
import secrets
import time
from functools import wraps

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)

ATTEMPTS = deque()


def create_app(store, vision):
    app = Flask(__name__)
    app.secret_key = store.session_key
    app.config.update(
        MAX_CONTENT_LENGTH=8192, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=15),
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    @app.before_request
    def csrf_check():
        if request.method != "POST":
            return
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        expected = session.get("csrf")
        if not expected or not sent or not secrets.compare_digest(expected, sent):
            abort(403)

    @app.after_request
    def hardened_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'")
        return response

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf"]}

    def admin_only(view):
        @wraps(view)
        def inner(*args, **kwargs):
            if not session.get("admin"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return inner

    @app.get("/")
    def home():
        return render_template("index.html", page="home")

    @app.get("/video")
    def video():
        def frames():
            while not vision.stop.is_set():
                current = vision.frame()
                if current:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                           + current + b"\r\n")
                time.sleep(0.13)
        return Response(frames(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/status")
    def status():
        return jsonify(vision.snapshot())

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            now = time.monotonic()
            while ATTEMPTS and ATTEMPTS[0] < now - 300:
                ATTEMPTS.popleft()
            if len(ATTEMPTS) >= 5:
                flash("Muitas tentativas. Tente novamente em alguns minutos.")
            elif store.verify_password(request.form.get("password", "")):
                ATTEMPTS.clear()
                session.clear()
                session["admin"] = True
                session["csrf"] = secrets.token_urlsafe(32)
                session.permanent = True
                store.audit("admin_login")
                return redirect(url_for("settings"))
            else:
                ATTEMPTS.append(now)
                store.audit("login_failed")
                flash("Senha incorreta")
        return render_template("index.html", page="login")

    @app.post("/logout")
    @admin_only
    def logout():
        session.clear()
        return redirect(url_for("home"))

    @app.get("/settings")
    @admin_only
    def settings():
        return render_template("index.html", page="settings",
                               config=store.settings(),
                               people=store.list_people(),
                               events=store.recent_audit(),
                               snapshot=vision.snapshot())

    @app.post("/camera")
    @admin_only
    def camera():
        try:
            store.set_camera({
                "device": request.form["device"],
                "width": request.form["width"],
                "height": request.form["height"],
                "fps": request.form["fps"],
                "flip": "flip" in request.form,
            })
            vision.restart.set()
            flash("Configuração salva. Reiniciando câmera.")
            store.audit("camera_changed")
        except (ValueError, KeyError) as exc:
            flash("Configuração inválida: " + str(exc))
        return redirect(url_for("settings"))

    @app.post("/restart-camera")
    @admin_only
    def restart_camera():
        vision.restart.set()
        flash("Reinício de câmera solicitado.")
        return redirect(url_for("settings"))

    @app.post("/enroll")
    @admin_only
    def enroll():
        try:
            vision.begin_enroll(request.form.get("name", ""))
            flash("Cadastro iniciado. Olhe para a câmera por alguns segundos.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("settings"))

    @app.post("/person/<person_id>/permission")
    @admin_only
    def permission(person_id):
        try:
            store.set_enabled(person_id, request.form.get("enabled") == "1")
            vision.refresh()
            flash("Permissão atualizada.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("settings"))

    @app.post("/person/<person_id>/delete")
    @admin_only
    def delete(person_id):
        try:
            store.delete_person(person_id)
            vision.refresh()
            flash("Cadastro e biometria excluídos.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("settings"))

    return app
