import os
from flask import Flask
from flask_cors import CORS

from webhook_vexpay import webhook_bp
from c2p_service import c2p_bp

app = Flask(__name__)
CORS(app)  # Habilita la comunicación con AI Studio

# Registramos tus módulos
app.register_blueprint(webhook_bp)
app.register_blueprint(c2p_bp)

# Ruta ultra-simple para mantener despierto el servidor
@app.route('/ping', methods=['GET'])
def ping():
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
