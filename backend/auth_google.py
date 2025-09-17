# auth_google.py
from flask import request, jsonify, current_app
from sqlalchemy.orm import Session
from flask_jwt_extended import create_access_token
from models import User

import requests
from requests import exceptions as req_exc
import bcrypt as pybcrypt  # ← pour générer un hash bcrypt sans dépendre de Flask-Bcrypt


def _mask(tok: str | None) -> str:
    if not tok:
        return "<empty>"
    return f"{tok[:8]}...{tok[-6:]}"


def _get_access_token_from_request() -> str | None:
    data = request.get_json(silent=True) or {}
    tok = data.get("access_token")
    if tok:
        return tok
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def register_google_auth_route(app, _SECRET_KEY, engine):
    app.logger.info("✅ register_google_auth_route() — /api/auth/google prêt")

    @app.route("/api/auth/google", methods=["POST"])
    def google_auth():
        try:
            access_token = _get_access_token_from_request()
            current_app.logger.info("[/api/auth/google] token=%s", _mask(access_token))

            if not access_token:
                return jsonify({"ok": False, "error": "Access token manquant"}), 400

            # 1) Vérifier le token auprès de Google
            try:
                google_resp = requests.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
            except req_exc.RequestException as e:
                current_app.logger.exception("[/api/auth/google] Erreur réseau Google")
                return jsonify({"ok": False, "error": f"Erreur réseau Google: {e}"}), 502

            if google_resp.status_code != 200:
                current_app.logger.warning(
                    "[/api/auth/google] google status=%s body=%s",
                    google_resp.status_code, google_resp.text[:400]
                )
                return jsonify({"ok": False, "error": "Token Google invalide ou expiré"}), 401

            info = google_resp.json() or {}
            email = info.get("email")
            name = info.get("name")
            picture = info.get("picture")

            if not email:
                return jsonify({"ok": False, "error": "Email non fourni par Google"}), 400

            # 2) DB
            session = Session(bind=engine)
            try:
                user = session.query(User).filter_by(email=email).first()

                if not user:
                    # Génère un hash bcrypt factice pour satisfaire NOT NULL
                    placeholder_pw = f"oauth-google::{email}"
                    fake_hash = pybcrypt.hashpw(
                        placeholder_pw.encode("utf-8"),
                        pybcrypt.gensalt()
                    ).decode("utf-8")

                    # Crée l'utilisateur (tolérance si la colonne "picture" n'existe pas)
                    try:
                        user = User(email=email, fullname=name, picture=picture)
                    except TypeError:
                        user = User(email=email, fullname=name)

                    # Assure password_hash non nul
                    try:
                        user.password_hash = fake_hash
                    except Exception:
                        # Si ton modèle s'appelle différemment (password, pwd_hash, etc.)
                        # adapte ici si besoin.
                        pass

                    session.add(user)
                    session.commit()
                    current_app.logger.info(
                        "[/api/auth/google] user créé id=%s email=%s",
                        user.id, user.email
                    )
                else:
                    # Mise à jour légère
                    updated = False
                    if name and getattr(user, "fullname", None) != name:
                        try:
                            user.fullname = name
                            updated = True
                        except Exception:
                            pass
                    if picture and hasattr(user, "picture"):
                        if getattr(user, "picture", None) != picture:
                            try:
                                user.picture = picture
                                updated = True
                            except Exception:
                                pass
                    # Si l’utilisateur existant a un password_hash NULL (héritage d’anciennes données), corrige-le
                    if getattr(user, "password_hash", None) in (None, ""):
                        try:
                            placeholder_pw = f"oauth-google::{email}"
                            user.password_hash = pybcrypt.hashpw(
                                placeholder_pw.encode("utf-8"),
                                pybcrypt.gensalt()
                            ).decode("utf-8")
                            updated = True
                        except Exception:
                            pass

                    if updated:
                        session.commit()

                # 3) JWT compatible avec /api/auth/me
                token = create_access_token(identity=user.id, additional_claims={"email": email})

                return jsonify({
                    "ok": True,
                    "token": token,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "fullname": getattr(user, "fullname", None),
                        "picture": picture if picture else getattr(user, "picture", None),
                    }
                }), 200

            except Exception as db_e:
                session.rollback()
                current_app.logger.exception("ERROR in auth_google: DB ERROR")
                return jsonify({"ok": False, "error": "DB error", "detail": str(db_e)}), 500
            finally:
                session.close()

        except Exception as e:
            current_app.logger.exception("[/api/auth/google] Exception non gérée")
            return jsonify({"ok": False, "error": str(e)}), 500
