import os
import logging
from flask import Flask, redirect, url_for, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import LoginManager
from flask_wtf import CSRFProtect

# Set up logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
login_manager = LoginManager()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # Configure SQLite database
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///inventory.db"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }
    app.config["UPLOAD_FOLDER"] = "uploads"
    app.config["PUBLIC_FOLDER"] = "public"
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  


    # Create upload directories
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["PUBLIC_FOLDER"], exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    csrf.init_app(app)

    with app.app_context():
        # Import models to ensure tables are created
        import models  # noqa: F401
        db.create_all()

        from models import User, Setting

        @login_manager.user_loader
        def load_user(user_id: str):
            return User.query.get(int(user_id))

    @app.before_request
    def check_initialization():
        if request.endpoint and not request.endpoint.startswith('onboarding') and not request.endpoint.startswith('static'):
            if not Setting.get('app_initialized'):
                return redirect(url_for('onboarding.onboarding_step1'))

    from routes.auth import auth_bp
    from routes.items import items_bp
    from routes.onboarding import onboarding_bp
    from routes import register_routes
    app.register_blueprint(auth_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(onboarding_bp)
    register_routes(app)

    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
