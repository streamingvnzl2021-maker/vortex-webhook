import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# Cargar variables de entorno locales
load_dotenv()
API_KEY = os.getenv("VEXPAY_API_KEY")

app = Flask(__name__)

@app.route('/webhook/vexpay', methods=['POST'])
def webhook_vexpay():
    """
    Puerta de escucha pasiva: Vex Pay toca aquí automáticamente cuando un pago se completa.
    """
    # Seguridad opcional: Verificar la API Key si Vex Pay la envía en los encabezados
    received_key = request.headers.get("x-api-key")
    if API_KEY and received_key and received_key != API_KEY:
        return jsonify({"status": "unauthorized", "message": "API Key inválida"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"status": "bad_request", "message": "Payload vacío"}), 400

    event_type = data.get("event")
    status = data.get("status")
    external_ref = data.get("externalRef")
    bank_reference = data.get("bankReference")

    print(f"📥 [WEBHOOK RECIBIDO] Evento: {event_type} | Ref Cartón: {external_ref} | Estatus: {status}")

    # Evaluamos si el pago fue exitoso (COMPLETED)
    if status == "COMPLETED" or event_type == "payment.completed":
        
        # AQUÍ OCURRE LA MAGIA AUTOMÁTICA DE LIBERACIÓN
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
    """
    Lógica interna que actualiza tu base de datos o archivo de control 
    para cambiar el estatus del cartón a 'PAGADO'.
    """
    print(f"🎉 ¡CARTÓN LIBERADO AUTOMÁTICO! Referencia interna: {external_ref} | Ref Bancaria: {bank_ref}")
    # Aquí en el futuro puedes guardar un registro en un archivo de texto, base de datos, etc.
    return True

if __name__ == '__main__':
    # Corre el servidor localmente en el puerto 5000 (en AWS se configurará para producción)
    app.run(host='0.0.0.0', port=5000, debug=False)