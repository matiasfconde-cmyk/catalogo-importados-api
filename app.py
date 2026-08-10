import os
import json
import requests
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

TIENDANUBE_API_VERSION = "2025-03"
USER_AGENT = "Catalogo Importados (catalogo-importados-api.onrender.com)"

catalogo_cache = []


@app.route("/", methods=["GET"])
def home():
    return """
    <h2>Catalogo Importados API funcionando</h2>
    <p>Si la tienda ya fue autorizada, podes consultar /catalogo</p>
    """, 200


@app.route("/tiendanube/callback", methods=["GET"])
def tiendanube_callback():
    global catalogo_cache

    code = request.args.get("code")

    if not code:
        return "Falta el codigo de autorizacion", 400

    # 1. Cambiar el code por access_token
    token_response = requests.post(
        "https://www.tiendanube.com/apps/authorize/token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code
        },
        timeout=30
    )

    if token_response.status_code != 200:
        return f"Error obteniendo access token: {token_response.text}", 400

    token_data = token_response.json()

    access_token = token_data.get("access_token")
    user_id = token_data.get("user_id")

    if not access_token or not user_id:
        return "Tiendanube no devolvio access_token o user_id", 400

    headers = {
        "Authentication": f"bearer {access_token}",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": USER_AGENT
    }

    # 2. Descargar productos
    productos = []
    page = 1

    while True:
        url = (
            f"https://api.tiendanube.com/"
            f"{TIENDANUBE_API_VERSION}/{user_id}/products"
        )

        response = requests.get(
            url,
            headers=headers,
            params={
                "page": page,
                "per_page": 200
            },
            timeout=60
        )

        if response.status_code != 200:
            return (
                f"Error leyendo productos "
                f"(pagina {page}): {response.text}",
                400
            )

        lote = response.json()

        if not lote:
            break

        productos.extend(lote)

        if len(lote) < 200:
            break

        page += 1

    # 3. Dejar solamente los datos que necesitamos para el PDF
    catalogo = []

    for producto in productos:

        nombre = producto.get("name", {})
        descripcion = producto.get("description", {})

        if isinstance(nombre, dict):
            nombre = (
                nombre.get("es")
                or nombre.get("pt")
                or nombre.get("en")
                or next(iter(nombre.values()), "")
            )

        if isinstance(descripcion, dict):
            descripcion = (
                descripcion.get("es")
                or descripcion.get("pt")
                or descripcion.get("en")
                or next(iter(descripcion.values()), "")
            )

        imagenes = []

        for imagen in producto.get("images", []):
            src = imagen.get("src")
            if src:
                imagenes.append(src)

        categorias = []

        for categoria in producto.get("categories", []):
            cat_nombre = categoria.get("name", "")

            if isinstance(cat_nombre, dict):
                cat_nombre = (
                    cat_nombre.get("es")
                    or cat_nombre.get("pt")
                    or cat_nombre.get("en")
                    or next(iter(cat_nombre.values()), "")
                )

            if cat_nombre:
                categorias.append(cat_nombre)

        variantes = producto.get("variants", [])

        precios = []

        for variante in variantes:
            precio = variante.get("price")
            if precio not in [None, ""]:
                precios.append(precio)

        precio = precios[0] if precios else ""

        catalogo.append({
            "id": producto.get("id"),
            "nombre": nombre,
            "descripcion": descripcion,
            "precio": precio,
            "categorias": categorias,
            "imagenes": imagenes,
            "url": producto.get("canonical_url", ""),
            "handle": producto.get("handle", {})
        })

    catalogo_cache = catalogo

    # También guardamos una copia temporal en Render
    try:
        with open("/tmp/catalogo.json", "w", encoding="utf-8") as archivo:
            json.dump(
                catalogo,
                archivo,
                ensure_ascii=False,
                indent=2
            )
    except Exception:
        pass

    return f"""
    <h2>Tiendanube conectada correctamente ✅</h2>
    <p><b>Tienda:</b> {user_id}</p>
    <p><b>Productos encontrados:</b> {len(catalogo)}</p>
    <p><a href="/catalogo">Abrir catálogo JSON</a></p>
    <p><a href="/catalogo/descargar">Descargar catálogo JSON</a></p>
    """, 200


@app.route("/catalogo", methods=["GET"])
def ver_catalogo():
    global catalogo_cache

    if not catalogo_cache:
        try:
            with open("/tmp/catalogo.json", "r", encoding="utf-8") as archivo:
                catalogo_cache = json.load(archivo)
        except Exception:
            return jsonify({
                "error": "Todavia no hay catalogo cargado. Volve a autorizar la app."
            }), 404

    return jsonify({
        "cantidad": len(catalogo_cache),
        "productos": catalogo_cache
    })


@app.route("/catalogo/descargar", methods=["GET"])
def descargar_catalogo():
    global catalogo_cache

    if not catalogo_cache:
        try:
            with open("/tmp/catalogo.json", "r", encoding="utf-8") as archivo:
                catalogo_cache = json.load(archivo)
        except Exception:
            return "Todavia no hay catalogo cargado.", 404

    contenido = json.dumps(
        catalogo_cache,
        ensure_ascii=False,
        indent=2
    )

    return Response(
        contenido,
        mimetype="application/json",
        headers={
            "Content-Disposition":
            "attachment; filename=catalogo_importados.json"
        }
    )


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
