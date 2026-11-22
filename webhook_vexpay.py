import os
import json
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("VEXPAY_API_KEY")
BIYUYO_API_KEY = os.getenv("VEXPAY_BIYUYO_API_KEY")

webhook_bp = Blueprint('webhook_service', __name__)


def extract_vexpay_fields(payload):
    """Extrae campos tanto si vienen en la raíz como anidados en data/payment."""
    nested = payload.get("data") or payload.get("payment") or {}
    if not isinstance(nested, dict):
        nested = {}

    event_type = payload.get("event") or payload.get("type") or nested.get("event")
    status = nested.get("status") or payload.get("status")
    external_ref = (
        nested.get("externalRef")
        or nested.get("external_ref")
        or payload.get("externalRef")
        or payload.get("external_ref")
    )
    bank_reference = (
        nested.get("bankReference")
        or nested.get("bank_reference")
        or nested.get("reference")
        or payload.get("bankReference")
        or payload.get("reference")
    )
    reason = nested.get("failureReason") or nested.get("reason") or payload.get("reason")
    return event_type, status, external_ref, bank_reference, reason


# WEBHOOK 1: BINGO EL DORADO
@webhook_bp.route('/webhook/vexpay', methods=['POST'])
def webhook_vexpay():
    received_key = request.headers.get("x-api-key")
    if API_KEY and received_key and received_key != API_KEY:
        return jsonify({"status": "unauthorized", "message": "API Key inválida"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "bad_request", "message": "Payload vacío"}), 400

    event_type, status, external_ref, bank_reference, reason = extract_vexpay_fields(payload)

    # Ignora limpiamente eventos que pertenecen a Biyuyo Express
    if external_ref and str(external_ref).upper().startswith("BYX-"):
        return jsonify({"status": "ignored", "message": "Evento perteneciente a Biyuyo Express"}), 200

    print(
        f"📥 [WEBHOOK BINGO] Evento: {event_type} | Ref: {external_ref} | Estatus: {status} | RefBanco: {bank_reference} | Motivo: {reason}",
        flush=True
    )
    if not external_ref:
        print(f"🔎 [WEBHOOK BINGO RAW] {json.dumps(payload, ensure_ascii=False)}", flush=True)

    if str(status).upper() in ("COMPLETED", "DONE", "APPROVED") or event_type == "payment.completed":
        liberacion_exitosa = liberar_carton_en_sistema(external_ref, bank_reference)
        if liberacion_exitosa:
            return jsonify({
                "status": "success",
                "message": f"Cartón {external_ref} liberado correctamente.",
                "bankReference": bank_reference
            }), 200
        return jsonify({"status": "error", "message": "Referencia no encontrada en inventario"}), 404

    return jsonify({"status": "acknowledged", "event": event_type, "externalRef": external_ref}), 200


def liberar_carton_en_sistema(external_ref, bank_ref):
    print(f"🎉 ¡CARTÓN BINGO LIBERADO AUTOMÁTICO! Referencia interna: {external_ref} | Ref Bancaria: {bank_ref}", flush=True)
    return True


# WEBHOOK 2: BIYUYO EXPRESS
@webhook_bp.route('/webhook/vexpay-biyuyo', methods=['POST'])
def webhook_vexpay_biyuyo():
    received_key = request.headers.get("x-api-key")
    if BIYUYO_API_KEY and received_key and received_key != BIYUYO_API_KEY:
        return jsonify({"status": "unauthorized", "message": "API Key de Biyuyo inválida"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "bad_request", "message": "Payload vacío"}), 400

    event_type, status, external_ref, bank_reference, reason = extract_vexpay_fields(payload)

    # Ignora limpiamente eventos que pertenecen a Bingo El Dorado
    if external_ref and not str(external_ref).upper().startswith("BYX-"):
        return jsonify({"status": "ignored", "message": "Evento perteneciente a Bingo El Dorado"}), 200

    print(
        f"🦁 [WEBHOOK BIYUYO] Evento: {event_type} | Ref: {external_ref} | Estatus: {status} | RefBanco: {bank_reference} | Motivo: {reason}",
        flush=True
    )
    if not external_ref:
        print(f"🔎 [WEBHOOK BIYUYO RAW] {json.dumps(payload, ensure_ascii=False)}", flush=True)

    if str(status).upper() in ("COMPLETED", "DONE", "APPROVED") or event_type == "payment.completed":
        liberacion_exitosa = liberar_ticket_biyuyo_en_sistema(external_ref, bank_reference)
        if liberacion_exitosa:
            return jsonify({
                "status": "success",
                "message": f"Ticket Biyuyo {external_ref} liberado correctamente.",
                "bankReference": bank_reference
            }), 200
        return jsonify({"status": "error", "message": "Referencia de ticket no encontrada"}), 404

    return jsonify({"status": "acknowledged", "event": event_type, "externalRef": external_ref}), 200


def liberar_ticket_biyuyo_en_sistema(external_ref, bank_ref):
    print(f"🎉 ¡TICKET BIYUYO LIBERADO AUTOMÁTICO! Referencia interna: {external_ref} | Ref Bancaria: {bank_ref}", flush=True)
    return True
