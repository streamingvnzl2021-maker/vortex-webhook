import os
from flask import Flask
from flask_cors import CORS

# Importamos los dos módulos independientes
from webhook_vexpay import webhook_bp
from c2p_service import c2p_bp

app = Flask(__name__)
CORS(app) # Habilita comunicación segura con Google AI Studio

# Registramos ambos servicios por separado
app.register_blueprint(webhook_bp)
app.register_blueprint(c2p_bp)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)