import os
import requests
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("VEXPAY_API_KEY")

c2p_bp = Blueprint('c2p_service', __name__)

HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json"
}

# RUTA 1: Crear la intención de pago (Paso 1)
@c2p_bp.route('/api/c2p/request', methods=['POST'])
def solicitar_intencion():
    datos = request.get_json() or {}
    
    # Formateo inteligente del teléfono (remueve el 0 y antepone 58)
    telefono_raw = str(datos.get("debtorCellPhone", "")).strip()
    telefono_limpio = "".join(filter(str.isdigit, telefono_raw))
    phone_formatted = telefono_limpio if telefono_limpio.startswith("58") else f"58{telefono_limpio.lstrip('0')}"

    # Formateo de Cédula (Ej. V12345678)
    tipo_doc = str(datos.get("docType", "V")).upper()
    num_doc = str(datos.get("docNumber", "")).strip()
    debtor_id = f"{tipo_doc}{num_doc}"

    payload = {
        "vesAmount": float(datos.get("vesAmount", 0)),
        "debtorId": debtor_id,
        "debtorCellPhone": phone_formatted,
        "debtorBankCode": int(datos.get("debtorBankCode", 102)),
        "externalRef": datos.get("externalRef")
    }

    url = "https://api.pay.vexwallet.co/v1/payments/c2p/request"
    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": "Error de conexión con la pasarela"}), 500


# RUTA 2: Ejecutar el cobro con el Token OTP (Paso 3)
@c2p_bp.route('/api/c2p/execute', methods=['POST'])
def ejecutar_cobro():
    datos = request.get_json() or {}
    
    telefono_raw = str(datos.get("debtorCellPhone", "")).strip()
    telefono_limpio = "".join(filter(str.isdigit, telefono_raw))
    phone_formatted = telefono_limpio if telefono_limpio.startswith("58") else f"58{telefono_limpio.lstrip('0')}"

    tipo_doc = str(datos.get("docType", "V")).upper()
    num_doc = str(datos.get("docNumber", "")).strip()
    debtor_id = f"{tipo_doc}{num_doc}"

    payload = {
        "intentId": datos.get("intentId"),
        "vesAmount": float(datos.get("vesAmount", 0)),
        "debtorId": debtor_id,
        "debtorCellPhone": phone_formatted,
        "debtorBankCode": int(datos.get("debtorBankCode", 102)),
        "token": str(datos.get("token", "")).strip(),
        "externalRef": datos.get("externalRef")
    }

    url = "https://api.pay.vexwallet.co/v1/payments/c2p"
    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": "Error de conexión con la pasarela"}), 500


# RUTA 3: Obtener datos de la cuenta receptora de Pago Movil
@c2p_bp.route('/api/pago-movil/account', methods=['GET'])
def obtener_cuenta_pago_movil():
    url = "https://api.pay.vexwallet.co/v1/payments/pago-movil/receiving-account"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": "Error al obtener cuenta receptora"}), 500


# RUTA 4: Verificar la transferencia de Pago Movil por referencia
@c2p_bp.route('/api/pago-movil/verify', methods=['POST'])
def verificar_pago_movil():
    datos = request.get_json() or {}

    payload = {
        "usdAmount": float(datos.get("usdAmount", 0)),
        "reference": str(datos.get("reference", "")).strip(),
        "externalRef": datos.get("externalRef")
    }

    if datos.get("dateMovement"):
        payload["dateMovement"] = str(datos.get("dateMovement")).strip()

    url = "https://api.pay.vexwallet.co/v1/payments/pago-movil/verify"
    try:
        response = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": "Error de conexión con la pasarela"}), 500


# RUTA 5: Obtener tasa oficial del dolar directamente desde Vex Pay
@c2p_bp.route('/api/quote', methods=['GET'])
def obtener_tasa_vexpay():
    url = "https://api.pay.vexwallet.co/v1/quote?usdAmount=1"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": "Error al obtener tasa de Vex Pay"}), 500


# Entorno Encargado de Verificacion con "OTP" por Correo a los Usuarios.
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

# Memoria temporal para almacenar los códigos de verificación
VERIFICATION_CODES = {}

# RUTA: Enviar código de verificación de 6 dígitos al correo del usuario con diseño corporativo
@c2p_bp.route('/api/auth/send-code', methods=['POST'])
def enviar_codigo_correo():
    datos = request.get_json() or {}
    email = str(datos.get("email", "")).strip().lower()
    
    if not email or "@" not in email:
        return jsonify({"status": "error", "message": "Correo electrónico inválido"}), 400
    
    # Generar código aleatorio de 6 dígitos
    code = f"{random.randint(0, 999999):06d}"
    VERIFICATION_CODES[email] = code
    
    # Configuración del servidor SMTP de Gmail
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = os.environ.get("MAIL_USERNAME")
    sender_password = os.environ.get("MAIL_PASSWORD")
    
    if not sender_email or not sender_password:
        return jsonify({"status": "error", "message": "Servidor de correo no configurado en el backend"}), 500
    
    # Construir el mensaje del correo con diseño corporativo de Vortex Play
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Código de Verificación - Vortex Play Bingo El Dorado"
    msg["From"] = sender_email
    msg["To"] = email
    
    html_content = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 30px; background-color: #0b0f19; color: #ffffff;">
        <div style="max-width: 500px; margin: 0 auto; background: #151b2b; padding: 40px; border-radius: 12px; border: 1px solid #c5a059; box-shadow: 0 8px 24px rgba(0,0,0,0.5);">
            
            <!-- Logo / Título de la Marca -->
            <div style="text-align: center; margin-bottom: 25px;">
                <h1 style="color: #f3c653; font-size: 24px; margin: 0; text-transform: uppercase; letter-spacing: 2px;">Vortex Play</h1>
                <p style="color: #a0aec0; font-size: 14px; margin: 5px 0 0 0; font-weight: bold;">Bingo El Dorado</p>
            </div>
            
            <hr style="border: none; border-top: 1px solid #2d3748; margin: 20px 0;">
            
            <!-- Cuerpo del Mensaje -->
            <p style="font-size: 16px; color: #e2e8f0; line-height: 1.5;">¡Hola!</p>
            <p style="font-size: 15px; color: #cbd5e0; line-height: 1.5;">Has solicitado un acceso o registro en nuestra plataforma de sorteos y apuestas en vivo. Utiliza el siguiente código de seguridad de 6 dígitos para continuar:</p>
            
            <!-- Caja del Código de Verificación -->
            <div style="text-align: center; margin: 35px 0;">
                <div style="display: inline-block; font-size: 36px; font-weight: 800; background: #1a2238; color: #f3c653; padding: 15px 30px; border-radius: 8px; letter-spacing: 8px; border: 1px dashed #c5a059;">
                    {code}
                </div>
            </div>
            
            <p style="font-size: 13px; color: #a0aec0; text-align: center; line-height: 1.4;">Este código es de uso personal e intransferible. Si no realizaste esta solicitud, por favor ignora este mensaje.</p>
            
            <hr style="border: none; border-top: 1px solid #2d3748; margin: 25px 0;">
            
            <!-- Pie de página -->
            <p style="font-size: 11px; color: #718096; text-align: center; margin: 0;">
                © 2026 Vortex Play - Bingo El Dorado. Todos los derechos reservados.
            </p>
        </div>
    </div>
    """
    msg.attach(MIMEText(html_content, "html"))
    
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, email, msg.as_string())
        server.quit()
        return jsonify({"status": "success", "message": "Código enviado exitosamente al correo"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": "Error al enviar el correo electrónico"}), 500


# RUTA: Validar el código ingresado por el usuario
@c2p_bp.route('/api/auth/verify-code', methods=['POST'])
def verificar_codigo_correo():
    datos = request.get_json() or {}
    email = str(datos.get("email", "")).strip().lower()
    user_code = str(datos.get("code", "")).strip()
    
    saved_code = VERIFICATION_CODES.get(email)
    
    if not saved_code or saved_code != user_code:
        return jsonify({"status": "error", "message": "Código incorrecto o expirado"}), 400
    
    del VERIFICATION_CODES[email]
    return jsonify({"status": "success", "message": "Correo verificado exitosamente"}), 200
