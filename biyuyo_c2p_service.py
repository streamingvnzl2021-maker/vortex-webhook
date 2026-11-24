import os
import requests
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

from backend_core import (
    VEXPAY_BASE_URL,
    check_rate_limit,
    extract_bank_reference_from_response,
    get_client_ip,
    get_venezuela_time_payload,
    get_webhook_status,
    is_vexpay_approved,
    normalize_bank_reference,
    normalize_doc_id,
    normalize_ve_phone,
    parse_bank_code,
    parse_positive_float,
    parse_vexpay_response,
    record_consumed_bank_reference,
    release_bank_reference_inflight,
    reserve_bank_reference_inflight,
    validate_bank_otp,
    validate_min_ves_for_one_usd,
)

load_dotenv()

biyuyo_c2p_bp = Blueprint('biyuyo_c2p_service', __name__, url_prefix='/biyuyo')


def get_biyuyo_headers():
    api_key = os.getenv("VEXPAY_BIYUYO_API_KEY", "").strip()
    if not api_key:
        return None, None
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }, api_key


def _is_valid_biyuyo_ref(external_ref) -> bool:
    ref = str(external_ref or "").strip().upper()
    return (ref.startswith("BYX-") or ref.startswith("RES-BYX-")) and len(ref) <= 80


# RUTA 1: Crear intencion C2P (Biyuyo Express)
@biyuyo_c2p_bp.route('/api/c2p/request', methods=['POST'])
def biyuyo_solicitar_intencion():
    headers, api_key = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"byx_c2p_req:{ip}", max_hits=15, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiadas solicitudes. Intenta en {retry_after}s."}), 429

    datos = request.get_json(silent=True) or {}
    external_ref = str(datos.get("externalRef") or "").strip()
    if not _is_valid_biyuyo_ref(external_ref):
        return jsonify({"status": "error", "message": "Referencia inválida: Biyuyo Express exige prefijo BYX-"}), 400

    ves_amount = parse_positive_float(datos.get("vesAmount"))
    if ves_amount is None:
        return jsonify({"status": "error", "message": "Monto en Bolívares inválido"}), 400
    min_ok, min_msg, min_code = validate_min_ves_for_one_usd(ves_amount, api_key)
    if not min_ok:
        return jsonify({"status": "error", "message": min_msg}), min_code

    phone_formatted = normalize_ve_phone(datos.get("debtorCellPhone"))
    if not phone_formatted:
        return jsonify({"status": "error", "message": "Teléfono móvil venezolano inválido (ej. 04141234567)"}), 400

    debtor_id = normalize_doc_id(datos.get("docType", "V"), datos.get("docNumber"))
    if not debtor_id:
        return jsonify({"status": "error", "message": "Cédula o RIF inválido"}), 400

    bank_code = parse_bank_code(datos.get("debtorBankCode"))
    if bank_code is None:
        return jsonify({"status": "error", "message": "Código de banco emisor inválido"}), 400

    payload = {
        "vesAmount": ves_amount,
        "debtorId": debtor_id,
        "debtorCellPhone": phone_formatted,
        "debtorBankCode": bank_code,
        "externalRef": external_ref
    }

    url = f"{VEXPAY_BASE_URL}/v1/payments/c2p/request"
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=45)
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado con el banco (45s). Tu reserva sigue protegida."}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error temporal de conexión con la pasarela Biyuyo"}), 502


# RUTA 2: Ejecutar cobro C2P con Token OTP (Biyuyo Express)
@biyuyo_c2p_bp.route('/api/c2p/execute', methods=['POST'])
def biyuyo_ejecutar_cobro():
    headers, api_key = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"byx_c2p_exec:{ip}", max_hits=15, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiadas solicitudes. Intenta en {retry_after}s."}), 429

    datos = request.get_json(silent=True) or {}
    external_ref = str(datos.get("externalRef") or "").strip()
    intent_id = str(datos.get("intentId") or "").strip()
    if not _is_valid_biyuyo_ref(external_ref) or not intent_id:
        return jsonify({"status": "error", "message": "Faltan intentId o externalRef válido de Biyuyo (BYX-)"}), 400

    ves_amount = parse_positive_float(datos.get("vesAmount"))
    if ves_amount is None:
        return jsonify({"status": "error", "message": "Monto en Bolívares inválido"}), 400
    min_ok, min_msg, min_code = validate_min_ves_for_one_usd(ves_amount, api_key)
    if not min_ok:
        return jsonify({"status": "error", "message": min_msg}), min_code

    phone_formatted = normalize_ve_phone(datos.get("debtorCellPhone"))
    if not phone_formatted:
        return jsonify({"status": "error", "message": "Teléfono móvil venezolano inválido"}), 400

    debtor_id = normalize_doc_id(datos.get("docType", "V"), datos.get("docNumber"))
    if not debtor_id:
        return jsonify({"status": "error", "message": "Cédula o RIF inválido"}), 400

    bank_code = parse_bank_code(datos.get("debtorBankCode"))
    if bank_code is None:
        return jsonify({"status": "error", "message": "Código de banco emisor inválido"}), 400

    token = validate_bank_otp(datos.get("token"))
    if not token:
        return jsonify({"status": "error", "message": "Clave dinámica C2P/OTP inválida (6 a 8 dígitos)"}), 400

    payload = {
        "intentId": intent_id,
        "vesAmount": ves_amount,
        "debtorId": debtor_id,
        "debtorCellPhone": phone_formatted,
        "debtorBankCode": bank_code,
        "token": token,
        "externalRef": external_ref
    }

    url = f"{VEXPAY_BASE_URL}/v1/payments/c2p"
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=45)
        try:
            body = response.json()
            if is_vexpay_approved(body, response.status_code):
                bank_ref = extract_bank_reference_from_response(body)
                if bank_ref:
                    raw_r, canon_r, suf6 = normalize_bank_reference(bank_ref)
                    if canon_r:
                        record_consumed_bank_reference(raw_r, canon_r, suf6, "biyuyo", external_ref, "C2P")
        except ValueError:
            pass
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "El banco tardó más de 45s en responder. Tu reserva permanece protegida."}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error temporal de conexión con la pasarela Biyuyo"}), 502


# RUTA 3: Cuenta receptora Pago Movil (Biyuyo Express)
@biyuyo_c2p_bp.route('/api/pago-movil/account', methods=['GET'])
def biyuyo_obtener_cuenta_pago_movil():
    headers, _ = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    url = f"{VEXPAY_BASE_URL}/v1/payments/pago-movil/receiving-account"
    try:
        response = requests.get(url, headers=headers, timeout=45)
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado al obtener cuenta receptora Biyuyo"}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error al obtener cuenta receptora Biyuyo"}), 502


# RUTA 4: Verificar Pago Movil (Biyuyo Express) + Anti-Replay Atomico
@biyuyo_c2p_bp.route('/api/pago-movil/verify', methods=['POST'])
def biyuyo_verificar_pago_movil():
    headers, _ = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"byx_pm_verify:{ip}", max_hits=20, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiados intentos. Espera {retry_after}s."}), 429

    datos = request.get_json(silent=True) or {}
    external_ref = str(datos.get("externalRef") or "").strip()
    if not _is_valid_biyuyo_ref(external_ref):
        return jsonify({"status": "error", "message": "Referencia inválida: Biyuyo Express exige prefijo BYX-"}), 400

    usd_amount = parse_positive_float(datos.get("usdAmount"), min_val=1.0, max_val=10000.0)
    if usd_amount is None:
        return jsonify({"status": "error", "message": "El monto mínimo para Pago Móvil es $1.00 USD"}), 400

    raw_ref, canonical_ref, suffix6 = normalize_bank_reference(datos.get("reference"))
    if not raw_ref:
        return jsonify({"status": "error", "message": "La referencia bancaria debe tener entre 6 y 12 dígitos numéricos"}), 400

    reserved, state, prev = reserve_bank_reference_inflight(
        raw_ref, canonical_ref, suffix6, "biyuyo", external_ref, ttl_seconds=60
    )
    if not reserved:
        if state == "inflight":
            return jsonify({
                "status": "error",
                "code": "REFERENCE_IN_FLIGHT",
                "message": "Esta referencia bancaria ya se está verificando en este instante. Espera unos segundos."
            }), 409
        return jsonify({
            "status": "error",
            "code": "REPLAY_ATTACK",
            "message": f"Esta referencia bancaria ya fue acreditada previamente en {prev['game'].upper()}."
        }), 409

    payload = {
        "usdAmount": usd_amount,
        "reference": raw_ref,
        "externalRef": external_ref
    }
    if datos.get("dateMovement"):
        payload["dateMovement"] = str(datos.get("dateMovement")).strip()[:20]

    url = f"{VEXPAY_BASE_URL}/v1/payments/pago-movil/verify"
    approved = False
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=45)
        try:
            body = response.json()
            if is_vexpay_approved(body, response.status_code):
                approved = True
                record_consumed_bank_reference(raw_ref, canonical_ref, suffix6, "biyuyo", external_ref, "PAGO_MOVIL", usd_amount)
        except ValueError:
            pass
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado verificando Pago Móvil Biyuyo (45s)"}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error temporal de conexión con la pasarela Biyuyo"}), 502
    finally:
        if not approved:
            release_bank_reference_inflight(canonical_ref, suffix6, external_ref)


# RUTA 5: Tasa oficial BCV/VexPay (Biyuyo Express)
@biyuyo_c2p_bp.route('/api/quote', methods=['GET'])
def biyuyo_obtener_tasa_vexpay():
    headers, _ = get_biyuyo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_BIYUYO_API_KEY no configurada en Render"}), 500

    url = f"{VEXPAY_BASE_URL}/v1/quote?usdAmount=1"
    try:
        response = requests.get(url, headers=headers, timeout=20)
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado consultando tasa VexPay Biyuyo"}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error al obtener tasa de VexPay Biyuyo"}), 502


# RUTA 6: Conciliacion post-F5 de pagos Biyuyo (con rate-limit y payload minimo)
@biyuyo_c2p_bp.route('/api/c2p/status/<external_ref>', methods=['GET'])
def consultar_estado_biyuyo(external_ref):
    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"byx_status:{ip}", max_hits=30, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiadas consultas. Espera {retry_after}s."}), 429

    clean_ref = str(external_ref or "").strip()
    if not _is_valid_biyuyo_ref(clean_ref):
        return jsonify({"status": "error", "message": "Referencia de Biyuyo inválida"}), 400
    record = get_webhook_status(clean_ref, expected_game="biyuyo")
    if not record:
        return jsonify({"status": "not_found", "externalRef": clean_ref}), 404
    return jsonify({"status": "ok", "data": record}), 200


# RUTA 7: Reloj oficial Venezuela (UTC-4)
@biyuyo_c2p_bp.route('/api/time', methods=['GET'])
def biyuyo_server_time():
    return jsonify(get_venezuela_time_payload()), 200
