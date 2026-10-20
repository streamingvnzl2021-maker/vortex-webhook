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
