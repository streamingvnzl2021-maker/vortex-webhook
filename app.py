import os
import re
from flask import Flask, jsonify
from flask_cors import CORS

from backend_core import get_venezuela_time_payload
from webhook_vexpay import webhook_bp
from c2p_service import c2p_bp
from biyuyo_c2p_service import biyuyo_c2p_bp

app = Flask(__name__)


def build_allowed_origins():
    """Restringe CORS a dominios oficiales + vistas previas seguras de AI Studio."""
    origins = [
        re.compile(r"^https://[a-zA-Z0-9-]+\.run\.app$"),
        "https://aistudio.google.com",
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    raw_custom = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw_custom:
        for item in raw_custom.split(","):
            cleaned = item.strip().rstrip("/")
            if cleaned:
                origins.append(cleaned)
    return origins


CORS(
    app,
    resources={
        r"/api/*": {"origins": build_allowed_origins()},
        r"/biyuyo/*": {"origins": build_allowed_origins()},
        r"/ping": {"origins": "*"},
    },
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    methods=["GET", "POST", "OPTIONS"]
)

app.register_blueprint(webhook_bp)
app.register_blueprint(c2p_bp)
app.register_blueprint(biyuyo_c2p_bp)


@app.route('/ping', methods=['GET'])
def ping():
    return "OK", 200


@app.route('/api/time', methods=['GET'])
def server_time():
    return jsonify(get_venezuela_time_payload()), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
