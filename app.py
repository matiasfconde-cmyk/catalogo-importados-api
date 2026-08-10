import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

@app.route("/", methods=["GET"])
def home():
    return "Catalogo Importados API funcionando", 200

@app.route("/tiendanube/callback", methods=["GET"])
def tiendanube_callback():
    code = request.args.get("code")

    if not code:
        return "Falta el codigo de autorizacion", 400

    response = requests.post(
        "https://www.tiendanube.com/apps/authorize/token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code
        },
        timeout=30
    )

    if response.status_code != 200:
        return f"Error al obtener access token: {response.text}", 400

    data = response.json()

    return jsonify({
        "ok": True,
        "user_id": data.get("user_id"),
        "scope": data.get("scope"),
        "message": "Tiendanube conectada correctamente"
    })

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
