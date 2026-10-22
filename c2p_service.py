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

# Memoria temporal para almacenar los códigos de verificación (Email -> {"code": "123456", "expires": timestamp})
VERIFICATION_CODES = {}

# RUTA: Enviar código de verificación de 6 dígitos al correo del usuario
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
    
    # Construir el mensaje del correo
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Código de Verificación - Vortex Play Bingo El Dorado"
    msg["From"] = sender_email
    msg["To"] = email
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4; color: #333;">
        <div style="max-width: 500px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
            <h2 style="color: #d9534f; text-align: center;">Vortex Play - Bingo El Dorado</h2>
            <p>Hola,</p>
            <p>Has solicitado registrarte en nuestra plataforma de sorteos. Utiliza el siguiente código de verificación de 6 dígitos para completar tu acceso:</p>
            <div style="text-align: center; margin: 30px 0;">
                <span style="font-size: 32px; font-weight: bold; background: #eee; padding: 10px 20px; border-radius: 5px; letter-spacing: 5px; color: #000;">{code}</span>
            </div>
            <p style="font-size: 12px; color: #777; text-align: center;">Si no solicitaste este código, puedes ignorar este mensaje.</p>
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
    
    # Si coincide, eliminamos el código usado por seguridad
    del VERIFICATION_CODES[email]
    return jsonify({"status": "success", "message": "Correo verificado exitosamente"}), 200

# Memoria temporal para almacenar los códigos de verificación (Email -> {"code": "123456", "expires": timestamp})
VERIFICATION_CODES = {}

# RUTA: Enviar código de verificación de 6 dígitos al correo del usuario
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
    
    # Construir el mensaje del correo
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Código de Verificación - Vortex Play Bingo El Dorado"
    msg["From"] = sender_email
    msg["To"] = email
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4; color: #333;">
        <div style="max-width: 500px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
            <h2 style="color: #d9534f; text-align: center;">Vortex Play - Bingo El Dorado</h2>
            <p>Hola,</p>
            <p>Has solicitado registrarte en nuestra plataforma de sorteos. Utiliza el siguiente código de verificación de 6 dígitos para completar tu acceso:</p>
            <div style="text-align: center; margin: 30px 0;">
                <span style="font-size: 32px; font-weight: bold; background: #eee; padding: 10px 20px; border-radius: 5px; letter-spacing: 5px; color: #000;">{code}</span>
            </div>
            <p style="font-size: 12px; color: #777; text-align: center;">Si no solicitaste este código, puedes ignorar este mensaje.</p>
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
    
    # Si coincide, eliminamos el código usado por seguridad
    del VERIFICATION_CODES[email]
    return jsonify({"status": "success", "message": "Correo verificado exitosamente"}), 200
