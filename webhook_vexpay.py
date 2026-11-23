import hashlib
import hmac
import os
import requests
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

from backend_core import (
    APPROVED_STATUSES,
    VEXPAY_BASE_URL,
    VEXPAY_PLATFORM_ID,
    logger,
    normalize_bank_reference,
    record_consumed_bank_reference,
    save_webhook_event,
)

load_dotenv()

webhook_bp = Blueprint('webhook_service', __name__)

SIGNATURE_HEADERS = (
    "x-vexpay-signature",
    "x-vex-signature",
    "x-webhook-signature",
    "x-signature",
    "vexpay-signature",
)


def extract_vexpay_fields(raw_json):
    """Extrae campos desde payload/data/payment o raíz, alineado al JSON real de VexPay."""
    nested = raw_json.get("payload") or raw_json.get("data") or raw_json.get("payment") or {}
    if not isinstance(nested, dict):
        nested = {}

    event_type = raw_json.get("event") or raw_json.get("type") or nested.get("event") or ""
    status = nested.get("status") or raw_json.get("status") or ""
    external_ref = (
        nested.get("externalRef")
        or nested.get("external_ref")
        or raw_json.get("externalRef")
        or raw_json.get("external_ref")
    )
    bank_reference = (
        nested.get("bankReference")
        or nested.get("bank_reference")
        or nested.get("reference")
        or raw_json.get("bankReference")
        or raw_json.get("reference")
    )
    payment_id = nested.get("paymentId") or raw_json.get("paymentId")
    platform_id = nested.get("platformId") or raw_json.get("platformId") or ""
    method = nested.get("method") or raw_json.get("method") or ""
    usd_amount = nested.get("usdAmount") or raw_json.get("usdAmount")
    ves_amount = nested.get("vesAmount") or raw_json.get("vesAmount")
    reason = (
        nested.get("failureMessage")
        or nested.get("failureCode")
        or nested.get("cancelReason")
        or nested.get("failureReason")
        or nested.get("reason")
        or raw_json.get("reason")
        or ""
    )
    return {
        "event_type": str(event_type).strip(),
        "status": str(status).strip().upper(),
        "external_ref": str(external_ref).strip() if external_ref else "",
        "bank_reference": str(bank_reference).strip() if bank_reference else "",
        "payment_id": str(payment_id).strip() if payment_id else "",
        "platform_id": str(platform_id).strip(),
        "method": str(method).strip().upper(),
        "usd_amount": usd_amount,
        "ves_amount": ves_amount,
        "reason": str(reason).strip(),
    }


def _verify_hmac_signature(raw_body: bytes, secret: str) -> bool:
    sig_header = ""
    for h_name in SIGNATURE_HEADERS:
        val = request.headers.get(h_name)
        if val:
            sig_header = val.strip()
            break
    if not sig_header:
        return False

    # Formato Stripe-style: t=12345,v1=abcdef...
    if "v1=" in sig_header:
        parts = {}
        for item in sig_header.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                parts[k.strip()] = v.strip()
        v1 = parts.get("v1", "")
        ts = parts.get("t", "")
        candidates = [
            hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        ]
        if ts:
            signed_payload = f"{ts}.".encode("utf-8") + raw_body
            candidates.append(hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest())
        return any(hmac.compare_digest(v1.lower(), c.lower()) for c in candidates)

    clean_sig = sig_header.replace("sha256=", "").strip().lower()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest().lower()
    return hmac.compare_digest(clean_sig, expected)


def _verify_payment_against_vexpay(api_key: str, payment_id: str, expected_ref: str):
    """Confirma activamente contra la API de VexPay que el pago realmente existe y coincide."""
    if not payment_id:
        return "missing_id"
    try:
        resp = requests.get(
            f"{VEXPAY_BASE_URL}/v1/payments/{payment_id}",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=15
        )
        if resp.status_code in (404, 405):
            return "endpoint_unavailable"
        if resp.status_code != 200:
            return "rejected"
        body = resp.json()
        nested = body.get("data") or body.get("payload") or body
        if not isinstance(nested, dict):
            return "rejected"
        remote_ref = str(nested.get("externalRef") or "").strip()
        remote_status = str(nested.get("status") or "").strip().upper()
        if remote_ref and expected_ref and remote_ref != expected_ref:
            return "rejected"
        if remote_status and remote_status not in APPROVED_STATUSES:
            return "rejected"
        return "verified"
    except Exception:
        return "endpoint_unavailable"


def _authenticate_webhook(raw_body: bytes, fields: dict, api_key: str, webhook_secret: str):
    if not api_key:
        return False, 500, "API Key no configurada en Render"

    # 1. Si se configuró VEXPAY_WEBHOOK_SECRET en Render: HMAC obligatorio (fail-closed absoluto)
    if webhook_secret:
        if _verify_hmac_signature(raw_body, webhook_secret):
            return True, 200, "ok"
        return False, 401, "Firma criptográfica HMAC inválida o ausente"

    # 2. Si el llamador envía x-api-key: validación fail-closed con compare_digest
    received_key = request.headers.get("x-api-key", "").strip()
    if received_key:
        if hmac.compare_digest(received_key, api_key):
            return True, 200, "ok"
        return False, 401, "API Key inválida"

    # 3. Modo transición seguro mientras no exista VEXPAY_WEBHOOK_SECRET:
    # Exige platformId oficial de Vortex Play y verificación activa contra VexPay para payment.completed
    if not fields["platform_id"] or not hmac.compare_digest(fields["platform_id"], VEXPAY_PLATFORM_ID):
        return False, 401, "Solicitud de webhook no autenticada"

    # Registra únicamente los nombres de cabeceras (sin valores) para diagnóstico de firma VexPay
    sig_keys = [k for k in request.headers.keys() if "vex" in k.lower() or "sig" in k.lower()]
    if sig_keys:
        logger.info("[WEBHOOK] Cabeceras de firma detectadas (solo nombres): %s", sig_keys)

    return True, 200, "ok"


def _handle_webhook(game: str, api_key: str, webhook_secret: str):
    raw_body = request.get_data()
    raw_json = request.get_json(silent=True)
    if not isinstance(raw_json, dict) or not raw_json:
        return jsonify({"status": "bad_request", "message": "Payload JSON vacío o inválido"}), 400

    fields = extract_vexpay_fields(raw_json)

    # Ignora eventos de notificación de prueba o retiros propios del comercio (payout.*)
    if fields["event_type"].startswith("payout.") or fields["event_type"] == "notification.test":
        return jsonify({"status": "ignored", "message": "Evento administrativo no aplicable a jugadas"}), 200

    authed, code, err_msg = _authenticate_webhook(raw_body, fields, api_key, webhook_secret)
    if not authed:
        logger.warning("[WEBHOOK %s] Rechazado (%s): %s", game.upper(), code, err_msg)
        return jsonify({"status": "unauthorized" if code == 401 else "error", "message": err_msg}), code

    external_ref = fields["external_ref"]
    if not external_ref:
        return jsonify({"status": "bad_request", "message": "externalRef requerido en evento de pago"}), 400

    is_biyuyo = external_ref.upper().startswith("BYX-") or external_ref.upper().startswith("RES-BYX-")
    if game == "bingo" and is_biyuyo:
        return jsonify({"status": "ignored", "message": "Evento perteneciente a Biyuyo Express"}), 200
    if game == "biyuyo" and not is_biyuyo:
        return jsonify({"status": "ignored", "message": "Evento perteneciente a Bingo El Dorado"}), 200

    is_completed = fields["status"] in APPROVED_STATUSES or fields["event_type"] == "payment.completed"

    # Doble candado para payment.completed si todavía no hay VEXPAY_WEBHOOK_SECRET configurado
    if is_completed and not webhook_secret:
        check = _verify_payment_against_vexpay(api_key, fields["payment_id"], external_ref)
        if check == "rejected":
            logger.warning("[WEBHOOK %s] Pago %s rechazado en verificación activa", game.upper(), external_ref)
            return jsonify({"status": "unauthorized", "message": "No se pudo verificar el pago contra VexPay"}), 401

    save_webhook_event(
        external_ref=external_ref,
        game=game,
        event_type=fields["event_type"],
        status=fields["status"] or ("COMPLETED" if is_completed else "RECEIVED"),
        payment_id=fields["payment_id"],
        bank_reference=fields["bank_reference"],
        method=fields["method"],
        usd_amount=fields["usd_amount"],
        ves_amount=fields["ves_amount"],
        reason=fields["reason"],
    )

    if is_completed and fields["bank_reference"]:
        raw_r, canon_r = normalize_bank_reference(fields["bank_reference"])
        if canon_r:
            record_consumed_bank_reference(
                raw_r, canon_r, game, external_ref,
                fields["method"] or "WEBHOOK", fields["usd_amount"]
            )

    logger.info(
        "[WEBHOOK %s] Persistido evento=%s ref=%s status=%s",
        game.upper(), fields["event_type"], external_ref, fields["status"]
    )

    if is_completed:
        return jsonify({
            "status": "success",
            "message": f"Pago {external_ref} persistido y confirmado en {game}.",
            "externalRef": external_ref,
            "bankReference": fields["bank_reference"]
        }), 200

    return jsonify({"status": "acknowledged", "event": fields["event_type"], "externalRef": external_ref}), 200


@webhook_bp.route('/webhook/vexpay', methods=['POST'])
def webhook_vexpay():
    api_key = os.getenv("VEXPAY_API_KEY", "").strip()
    secret = os.getenv("VEXPAY_WEBHOOK_SECRET", "").strip()
    return _handle_webhook("bingo", api_key, secret)


@webhook_bp.route('/webhook/vexpay-biyuyo', methods=['POST'])
def webhook_vexpay_biyuyo():
    api_key = os.getenv("VEXPAY_BIYUYO_API_KEY", "").strip()
    secret = os.getenv("VEXPAY_BIYUYO_WEBHOOK_SECRET", "").strip() or os.getenv("VEXPAY_WEBHOOK_SECRET", "").strip()
    return _handle_webhook("biyuyo", api_key, secret)
