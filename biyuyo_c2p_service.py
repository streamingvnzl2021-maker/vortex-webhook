import os
import requests
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

load_dotenv()

biyuyo_c2p_bp = Blueprint('biyuyo_c2p_service', __name__, url_prefix='/biyuyo')


def get_biyuyo_headers():
    api_key = os.getenv("VEXPAY_BIYUYO_API_KEY", "").strip()
    if not api_key:
        return None
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }


def parse_vexpay_response(response):
    try:
        return jsonify(response.json()), response.status_code
    except ValueError:
        return jsonify({
            "status": "error",
            "message": response.text or "Respuesta no válida de la pasarela VexPay"
        }), response.status_code


# RUTA 1: Crear la intención de pago C2P para Biyuyo Express
@biyuyo_c2p_bp.route('/api/c2p/request', methods=['POST'])
def biyuyo_solicitar_intencion():
    headers = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    datos = request.get_json(silent=True) or {}

    telefono_raw = str(datos.get("debtorCellPhone", "")).strip()
    telefono_limpio = "".join(filter(str.isdigit, telefono_raw))
    phone_formatted = telefono_limpio if telefono_limpio.startswith("58") else f"58{telefono_limpio.lstrip('0')}"

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
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return parse_vexpay_response(response)
    except Exception:
        return jsonify({"status": "error", "message": "Error de conexión con la pasarela Biyuyo"}), 500


# RUTA 2: Ejecutar el cobro C2P con el Token OTP para Biyuyo Express
@biyuyo_c2p_bp.route('/api/c2p/execute', methods=['POST'])
def biyuyo_ejecutar_cobro():
    headers = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    datos = request.get_json(silent=True) or {}

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
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return parse_vexpay_response(response)
    except Exception:
        return jsonify({"status": "error", "message": "Error de conexión con la pasarela Biyuyo"}), 500


# RUTA 3: Obtener datos de la cuenta receptora de Pago Móvil para Biyuyo Express
@biyuyo_c2p_bp.route('/api/pago-movil/account', methods=['GET'])
def biyuyo_obtener_cuenta_pago_movil():
    headers = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    url = "https://api.pay.vexwallet.co/v1/payments/pago-movil/receiving-account"
    try:
        response = requests.get(url, headers=headers, timeout=15)
        return parse_vexpay_response(response)
    except Exception:
        return jsonify({"status": "error", "message": "Error al obtener cuenta receptora Biyuyo"}), 500


# RUTA 4: Verificar transferencia de Pago Móvil por referencia para Biyuyo Express
@biyuyo_c2p_bp.route('/api/pago-movil/verify', methods=['POST'])
def biyuyo_verificar_pago_movil():
    headers = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    datos = request.get_json(silent=True) or {}

    payload = {
        "usdAmount": float(datos.get("usdAmount", 0)),
        "reference": str(datos.get("reference", "")).strip(),
        "externalRef": datos.get("externalRef")
    }

    if datos.get("dateMovement"):
        payload["dateMovement"] = str(datos.get("dateMovement")).strip()

    url = "https://api.pay.vexwallet.co/v1/payments/pago-movil/verify"
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return parse_vexpay_response(response)
    except Exception:
        return jsonify({"status": "error", "message": "Error de conexión con la pasarela Biyuyo"}), 500


# RUTA 5: Obtener tasa oficial del dólar desde la llave de Biyuyo Express
@biyuyo_c2p_bp.route('/api/quote', methods=['GET'])
def biyuyo_obtener_tasa_vexpay():
    headers = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    url = "https://api.pay.vexwallet.co/v1/quote?usdAmount=1"
    try:
        response = requests.get(url, headers=headers, timeout=15)
        return parse_vexpay_response(response)
    except Exception:
        return jsonify({"status": "error", "message": "Error al obtener tasa de VexPay Biyuyo"}), 500
