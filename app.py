import os
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Catalogo Importados API funcionando", 200

@app.route("/tiendanube/callback", methods=["GET"])
def callback():
    code = request.args.get("code")
    if not code:
        return "Falta el codigo de autorizacion", 400

    # Por seguridad no mostramos ni guardamos credenciales acá.
    return "Aplicacion autorizada correctamente. Ya podes cerrar esta ventana.", 200

@app.route("/webhooks/store-redact", methods=["POST"])
def store_redact():
    return jsonify({"ok": True}), 200

@app.route("/webhooks/customers-redact", methods=["POST"])
def customers_redact():
    return jsonify({"ok": True}), 200

@app.route("/webhooks/customers-data-request", methods=["POST"])
def customers_data_request():
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
