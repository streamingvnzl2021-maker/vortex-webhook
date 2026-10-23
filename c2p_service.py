import os
import requests
import random
import smtplib
import boto3
from botocore.exceptions import ClientError
from flask import request, jsonify
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

# RUTA: Enviar código de verificación de 6 dígitos con AWS SES (HTTPS - Puerto 443)
@c2p_bp.route('/api/auth/send-code', methods=['POST', 'OPTIONS'])
def enviar_codigo_correo():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    datos = request.get_json() or {}
    email = str(datos.get("email", "")).strip().lower()

    if not email or "@" not in email:
        return jsonify({"status": "error", "message": "Correo electrónico inválido"}), 400

    # Generar código real de 6 dígitos
    code = f"{random.randint(0, 999999):06d}"
    VERIFICATION_CODES[email] = code

    aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    sender_email = os.environ.get("MAIL_SENDER")

    if not aws_access_key or not aws_secret_key or not sender_email:
        return jsonify({
            "status": "error",
            "message": "Configuración de AWS SES incompleta en las variables de entorno de Render"
        }), 500

    html_content = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 30px; background-color: #0b0f19; color: #ffffff;">
        <div style="max-width: 500px; margin: 0 auto; background: #151b2b; padding: 40px; border-radius: 12px; border: 1px solid #c5a059; box-shadow: 0 8px 24px rgba(0,0,0,0.5);">
            <div style="text-align: center; margin-bottom: 25px;">
                <h1 style="color: #f3c653; font-size: 24px; margin: 0; text-transform: uppercase; letter-spacing: 2px;">Vortex Play</h1>
                <p style="color: #a0aec0; font-size: 14px; margin: 5px 0 0 0; font-weight: bold;">Bingo El Dorado</p>
            </div>
            <hr style="border: none; border-top: 1px solid #2d3748; margin: 20px 0;">
            <p style="font-size: 16px; color: #e2e8f0; line-height: 1.5;">¡Hola!</p>
            <p style="font-size: 15px; color: #cbd5e0; line-height: 1.5;">Has solicitado un acceso o registro en nuestra plataforma de sorteos y apuestas en vivo. Utiliza el siguiente código de seguridad de 6 dígitos para continuar:</p>
            <div style="text-align: center; margin: 35px 0;">
                <div style="display: inline-block; font-size: 36px; font-weight: 800; background: #1a2238; color: #f3c653; padding: 15px 30px; border-radius: 8px; letter-spacing: 8px; border: 1px dashed #c5a059;">
                    {code}
                </div>
            </div>
            <p style="font-size: 13px; color: #a0aec0; text-align: center; line-height: 1.4;">Este código es de uso personal e intransferible. Si no realizaste esta solicitud, por favor ignora este mensaje.</p>
            <hr style="border: none; border-top: 1px solid #2d3748; margin: 25px 0;">
            <p style="font-size: 11px; color: #718096; text-align: center; margin: 0;">
                © 2026 Vortex Play - Bingo El Dorado. Todos los derechos reservados.
            </p>
        </div>
    </div>
    """

    try:
        ses_client = boto3.client(
            'ses',
            region_name=aws_region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )

        ses_client.send_email(
            Source=sender_email,
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': 'Código de Verificación - Vortex Play Bingo El Dorado', 'Charset': 'UTF-8'},
                'Body': {
                    'Html': {'Data': html_content, 'Charset': 'UTF-8'},
                    'Text': {'Data': f'Tu código de verificación de Vortex Play es: {code}', 'Charset': 'UTF-8'}
                }
            }
        )
        print(f"[AWS SES] Correo enviado exitosamente a {email} con código {code}", flush=True)
        return jsonify({"status": "success", "message": "Código enviado exitosamente al correo"}), 200

    except ClientError as e:
        error_msg = e.response['Error']['Message']
        print(f"[AWS SES ERROR] {error_msg}", flush=True)
        return jsonify({"status": "error", "message": f"Error de AWS SES: {error_msg}"}), 500
    except Exception as e:
        print(f"[ERROR GENERAL] {str(e)}", flush=True)
        return jsonify({"status": "error", "message": "Error interno al enviar el correo"}), 500
