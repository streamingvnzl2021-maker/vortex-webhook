import os
import boto3
import requests
from botocore.exceptions import ClientError
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

from backend_core import (
    VEXPAY_BASE_URL,
    check_rate_limit,
    generate_secure_otp,
    get_client_ip,
    get_webhook_status,
    is_bank_reference_consumed,
    is_valid_email,
    is_vexpay_approved,
    logger,
    mask_email,
    normalize_bank_reference,
    normalize_doc_id,
    normalize_ve_phone,
    parse_bank_code,
    parse_positive_float,
    parse_vexpay_response,
    record_consumed_bank_reference,
    store_email_otp,
    validate_bank_otp,
    validate_min_ves_for_one_usd,
    verify_email_otp,
)

load_dotenv()

c2p_bp = Blueprint('c2p_service', __name__)


def get_bingo_headers():
    api_key = os.getenv("VEXPAY_API_KEY", "").strip()
    if not api_key:
        return None, None
    return {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }, api_key


def _is_biyuyo_ref(external_ref) -> bool:
    ref = str(external_ref or "").strip().upper()
    return ref.startswith("BYX-") or ref.startswith("RES-BYX-")


# RUTA 1: Crear intención de pago C2P (Bingo El Dorado)
@c2p_bp.route('/api/c2p/request', methods=['POST'])
def solicitar_intencion():
    headers, api_key = get_bingo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_API_KEY no configurada en Render"}), 500

    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"bingo_c2p_req:{ip}", max_hits=15, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiadas solicitudes. Intenta en {retry_after}s."}), 429

    datos = request.get_json(silent=True) or {}
    external_ref = str(datos.get("externalRef") or "").strip()
    if not external_ref or len(external_ref) > 80:
        return jsonify({"status": "error", "message": "Referencia externa (externalRef) inválida"}), 400
    if _is_biyuyo_ref(external_ref):
        return jsonify({"status": "error", "message": "Referencia de Biyuyo Express rechazada en ruta de Bingo El Dorado"}), 400

    ves_amount = parse_positive_float(datos.get("vesAmount"))
    if ves_amount is None:
        return jsonify({"status": "error", "message": "Monto en Bolívares inválido"}), 400
    min_ok, min_msg = validate_min_ves_for_one_usd(ves_amount, api_key)
    if not min_ok:
        return jsonify({"status": "error", "message": min_msg}), 400

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
        return jsonify({"status": "error", "message": "Error temporal de comunicación con la pasarela bancaria"}), 502


# RUTA 2: Ejecutar cobro C2P con Token OTP (Bingo El Dorado)
@c2p_bp.route('/api/c2p/execute', methods=['POST'])
def ejecutar_cobro():
    headers, api_key = get_bingo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_API_KEY no configurada en Render"}), 500

    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"bingo_c2p_exec:{ip}", max_hits=15, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiadas solicitudes. Intenta en {retry_after}s."}), 429

    datos = request.get_json(silent=True) or {}
    external_ref = str(datos.get("externalRef") or "").strip()
    intent_id = str(datos.get("intentId") or "").strip()
    if not external_ref or not intent_id:
        return jsonify({"status": "error", "message": "Faltan intentId o externalRef"}), 400
    if _is_biyuyo_ref(external_ref):
        return jsonify({"status": "error", "message": "Referencia de Biyuyo Express rechazada en ruta de Bingo El Dorado"}), 400

    ves_amount = parse_positive_float(datos.get("vesAmount"))
    if ves_amount is None:
        return jsonify({"status": "error", "message": "Monto en Bolívares inválido"}), 400
    min_ok, min_msg = validate_min_ves_for_one_usd(ves_amount, api_key)
    if not min_ok:
        return jsonify({"status": "error", "message": min_msg}), 400

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
                nested = body.get("data") if isinstance(body.get("data"), dict) else body
                bank_ref = nested.get("bankReference") or nested.get("reference")
                if bank_ref:
                    raw_r, canon_r = normalize_bank_reference(bank_ref)
                    if canon_r:
                        record_consumed_bank_reference(raw_r, canon_r, "bingo", external_ref, "C2P")
        except ValueError:
            pass
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "El banco tardó más de 45s en responder. No repitas el cobro sin verificar."}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error temporal de red con la pasarela bancaria"}), 502


# RUTA 3: Cuenta receptora Pago Móvil (Bingo El Dorado)
@c2p_bp.route('/api/pago-movil/account', methods=['GET'])
def obtener_cuenta_pago_movil():
    headers, _ = get_bingo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_API_KEY no configurada en Render"}), 500

    url = f"{VEXPAY_BASE_URL}/v1/payments/pago-movil/receiving-account"
    try:
        response = requests.get(url, headers=headers, timeout=45)
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado al consultar cuenta receptora"}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error al obtener cuenta receptora"}), 502


# RUTA 4: Verificar transferencia Pago Móvil (Bingo El Dorado) + Anti-Replay Servidor
@c2p_bp.route('/api/pago-movil/verify', methods=['POST'])
def verificar_pago_movil():
    headers, _ = get_bingo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_API_KEY no configurada en Render"}), 500

    ip = get_client_ip(request)
    ok_rl, retry_after = check_rate_limit(f"bingo_pm_verify:{ip}", max_hits=20, window_seconds=60)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiados intentos. Espera {retry_after}s."}), 429

    datos = request.get_json(silent=True) or {}
    external_ref = str(datos.get("externalRef") or "").strip()
    if not external_ref or len(external_ref) > 80:
        return jsonify({"status": "error", "message": "Referencia externa inválida"}), 400
    if _is_biyuyo_ref(external_ref):
        return jsonify({"status": "error", "message": "Referencia de Biyuyo Express rechazada en ruta de Bingo El Dorado"}), 400

    usd_amount = parse_positive_float(datos.get("usdAmount"), min_val=1.0, max_val=10000.0)
    if usd_amount is None:
        return jsonify({"status": "error", "message": "El monto mínimo para Pago Móvil es $1.00 USD"}), 400

    raw_ref, canonical_ref = normalize_bank_reference(datos.get("reference"))
    if not raw_ref:
        return jsonify({"status": "error", "message": "La referencia bancaria debe tener entre 6 y 12 dígitos numéricos"}), 400

    already_used, prev = is_bank_reference_consumed(canonical_ref, external_ref)
    if already_used:
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
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=45)
        try:
            body = response.json()
            if is_vexpay_approved(body, response.status_code):
                record_consumed_bank_reference(raw_ref, canonical_ref, "bingo", external_ref, "PAGO_MOVIL", usd_amount)
        except ValueError:
            pass
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado verificando Pago Móvil (45s)"}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error temporal de red con la pasarela bancaria"}), 502


# RUTA 5: Tasa oficial BCV/VexPay (Bingo El Dorado)
@c2p_bp.route('/api/quote', methods=['GET'])
def obtener_tasa_vexpay():
    headers, _ = get_bingo_headers()
    if not headers:
        return jsonify({"status": "error", "message": "VEXPAY_API_KEY no configurada en Render"}), 500

    url = f"{VEXPAY_BASE_URL}/v1/quote?usdAmount=1"
    try:
        response = requests.get(url, headers=headers, timeout=20)
        return parse_vexpay_response(response)
    except requests.Timeout:
        return jsonify({"status": "error", "message": "Tiempo de espera agotado consultando tasa VexPay"}), 504
    except requests.RequestException:
        return jsonify({"status": "error", "message": "Error al obtener tasa de VexPay"}), 502


# RUTA 6: Conciliación post-F5 de pagos Bingo registrados por webhook
@c2p_bp.route('/api/c2p/status/<external_ref>', methods=['GET'])
def consultar_estado_bingo(external_ref):
    clean_ref = str(external_ref or "").strip()
    if not clean_ref or len(clean_ref) > 80 or _is_biyuyo_ref(clean_ref):
        return jsonify({"status": "error", "message": "Referencia inválida"}), 400
    record = get_webhook_status(clean_ref, expected_game="bingo")
    if not record:
        return jsonify({"status": "not_found", "externalRef": clean_ref}), 404
    return jsonify({"status": "ok", "data": record}), 200


# RUTA 7: Envío de código OTP por correo (AWS SES + CSPRNG secrets + Rate-Limit + Cero Logs PII)
@c2p_bp.route('/api/auth/send-code', methods=['POST'])
def enviar_codigo_correo():
    datos = request.get_json(silent=True) or {}
    email = str(datos.get("email", "")).strip().lower()

    if not is_valid_email(email):
        return jsonify({"status": "error", "message": "Correo electrónico inválido"}), 400

    ip = get_client_ip(request)
    ok_cooldown, wait_s = check_rate_limit(f"otp_email_1m:{email}", max_hits=1, window_seconds=60)
    if not ok_cooldown:
        return jsonify({"status": "error", "message": f"Espera {wait_s} segundos antes de solicitar otro código."}), 429

    ok_email_hr, _ = check_rate_limit(f"otp_email_1h:{email}", max_hits=5, window_seconds=3600)
    ok_ip_hr, _ = check_rate_limit(f"otp_ip_1h:{ip}", max_hits=12, window_seconds=3600)
    if not ok_email_hr or not ok_ip_hr:
        return jsonify({"status": "error", "message": "Límite de envíos alcanzado por seguridad. Intenta más tarde."}), 429

    aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    sender_email = os.environ.get("MAIL_SENDER")

    if not aws_access_key or not aws_secret_key or not sender_email:
        return jsonify({"status": "error", "message": "Servicio de correo temporalmente no disponible"}), 500

    code = generate_secure_otp()

    html_content = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 30px; background-color: #0b0f19; color: #ffffff;">
        <div style="max-width: 500px; margin: 0 auto; background: #151b2b; padding: 40px; border-radius: 12px; border: 1px solid #c5a059; box-shadow: 0 8px 24px rgba(0,0,0,0.5);">
            <div style="text-align: center; margin-bottom: 25px;">
                <h1 style="color: #f3c653; font-size: 24px; margin: 0; text-transform: uppercase; letter-spacing: 2px;">Vortex Play</h1>
                <p style="color: #a0aec0; font-size: 14px; margin: 5px 0 0 0; font-weight: bold;">Plataforma Oficial de Sorteos en Vivo</p>
            </div>
            <hr style="border: none; border-top: 1px solid #2d3748; margin: 20px 0;">
            <p style="font-size: 16px; color: #e2e8f0; line-height: 1.5;">¡Hola!</p>
            <p style="font-size: 15px; color: #cbd5e0; line-height: 1.5;">Utiliza el siguiente código de seguridad de 6 dígitos para continuar (válido por 10 minutos):</p>
            <div style="text-align: center; margin: 35px 0;">
                <div style="display: inline-block; font-size: 36px; font-weight: 800; background: #1a2238; color: #f3c653; padding: 15px 30px; border-radius: 8px; letter-spacing: 8px; border: 1px dashed #c5a059;">
                    {code}
                </div>
            </div>
            <p style="font-size: 13px; color: #a0aec0; text-align: center; line-height: 1.4;">Este código es personal e intransferible y caduca en 10 minutos. Si no realizaste esta solicitud, ignora este mensaje.</p>
            <hr style="border: none; border-top: 1px solid #2d3748; margin: 25px 0;">
            <p style="font-size: 11px; color: #718096; text-align: center; margin: 0;">
                © 2026 Vortex Play. Todos los derechos reservados.
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
                'Subject': {'Data': 'Código de Verificación - Vortex Play', 'Charset': 'UTF-8'},
                'Body': {
                    'Html': {'Data': html_content, 'Charset': 'UTF-8'},
                    'Text': {'Data': f'Tu código de verificación de Vortex Play (válido por 10 minutos) es: {code}', 'Charset': 'UTF-8'}
                }
            }
        )
        # Guardar en BD ÚNICAMENTE tras envío exitoso
        store_email_otp(email, code)
        logger.info("[AWS SES] OTP despachado correctamente a %s", mask_email(email))
        return jsonify({"status": "success", "message": "Código enviado exitosamente al correo"}), 200

    except ClientError as exc:
        err_code = exc.response.get("Error", {}).get("Code", "SESError")
        logger.error("[AWS SES ERROR] Fallo al enviar OTP a %s (%s)", mask_email(email), err_code)
        return jsonify({"status": "error", "message": "No se pudo enviar el correo de verificación. Intenta nuevamente."}), 502
    except Exception:
        logger.exception("[AUTH SEND ERROR] Error interno enviando OTP")
        return jsonify({"status": "error", "message": "Error interno al enviar el correo"}), 500


# RUTA 8: Verificar código OTP de 6 dígitos (BD compartida + Límite de 5 intentos)
@c2p_bp.route('/api/auth/verify-code', methods=['POST'])
def verificar_codigo_correo():
    ip = get_client_ip(request)
    ok_rl, wait_s = check_rate_limit(f"otp_verify_ip:{ip}", max_hits=15, window_seconds=600)
    if not ok_rl:
        return jsonify({"status": "error", "message": f"Demasiados intentos. Espera {wait_s} segundos."}), 429

    datos = request.get_json(silent=True) or {}
    email = str(datos.get("email", "")).strip().lower()
    user_code = "".join(filter(str.isdigit, str(datos.get("code", "")).strip()))

    if not is_valid_email(email) or len(user_code) != 6:
        return jsonify({"status": "error", "message": "Correo o código inválido"}), 400

    valid, msg = verify_email_otp(email, user_code)
    if not valid:
        return jsonify({"status": "error", "message": msg}), 400

    return jsonify({"status": "success", "message": msg}), 200
