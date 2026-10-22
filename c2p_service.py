import os
import requests
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
