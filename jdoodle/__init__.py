import os
from flask import Flask

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ["SECRET_KEY"]

    from .routes import main_blueprint, init_db
    app.register_blueprint(main_blueprint)

    with app.app_context():
        init_db()

    return app