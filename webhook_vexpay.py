import os
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("VEXPAY_API_KEY")
BIYUYO_API_KEY = os.getenv("VEXPAY_BIYUYO_API_KEY")

webhook_bp = Blueprint('webhook_service', __name__)


# WEBHOOK 1: BINGO EL DORADO (Intacto)
@webhook_bp.route('/webhook/vexpay', methods=['POST'])
def webhook_vexpay():
    received_key = request.headers.get("x-api-key")
    if API_KEY and received_key and received_key != API_KEY:
        return jsonify({"status": "unauthorized", "message": "API Key inválida"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "bad_request", "message": "Payload vacío"}), 400

    event_type = data.get("event")
    status = data.get("status")
    external_ref = data.get("externalRef")
    bank_reference = data.get("bankReference")

    print(f"📥 [WEBHOOK BINGO] Evento: {event_type} | Ref Cartón: {external_ref} | Estatus: {status}", flush=True)

    if status == "COMPLETED" or event_type == "payment.completed":
        liberacion_exitosa = liberar_carton_en_sistema(external_ref, bank_reference)
        if liberacion_exitosa:
            return jsonify({
                "status": "success",
                "message": f"Cartón {external_ref} liberado correctamente.",
                "bankReference": bank_reference
            }), 200
        else:
            return jsonify({"status": "error", "message": "Referencia no encontrada en inventario"}), 404

    return jsonify({"status": "ignored", "message": "El evento no requiere acción"}), 200


def liberar_carton_en_sistema(external_ref, bank_ref):
    print(f"🎉 ¡CARTÓN BINGO LIBERADO AUTOMÁTICO! Referencia interna: {external_ref} | Ref Bancaria: {bank_ref}", flush=True)
    return True


# WEBHOOK 2: BIYUYO EXPRESS (Independiente)
@webhook_bp.route('/webhook/vexpay-biyuyo', methods=['POST'])
def webhook_vexpay_biyuyo():
    received_key = request.headers.get("x-api-key")
    if BIYUYO_API_KEY and received_key and received_key != BIYUYO_API_KEY:
        return jsonify({"status": "unauthorized", "message": "API Key de Biyuyo inválida"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "bad_request", "message": "Payload vacío"}), 400

    event_type = data.get("event")
    status = data.get("status")
    external_ref = data.get("externalRef")
    bank_reference = data.get("bankReference")

    print(f"🦁 [WEBHOOK BIYUYO] Evento: {event_type} | Ref Ticket: {external_ref} | Estatus: {status}", flush=True)

    if status == "COMPLETED" or event_type == "payment.completed":
        liberacion_exitosa = liberar_ticket_biyuyo_en_sistema(external_ref, bank_reference)
        if liberacion_exitosa:
            return jsonify({
                "status": "success",
                "message": f"Ticket Biyuyo {external_ref} liberado correctamente.",
                "bankReference": bank_reference
            }), 200
        else:
            return jsonify({"status": "error", "message": "Referencia de ticket no encontrada"}), 404

    return jsonify({"status": "ignored", "message": "El evento no requiere acción"}), 200


def liberar_ticket_biyuyo_en_sistema(external_ref, bank_ref):
    print(f"🎉 ¡TICKET BIYUYO LIBERADO AUTOMÁTICO! Referencia interna: {external_ref} | Ref Bancaria: {bank_ref}", flush=True)
    return True
