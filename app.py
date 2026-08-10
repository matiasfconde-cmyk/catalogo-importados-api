import os
import re
import requests
from flask import Flask, request, jsonify, redirect, url_for, render_template_string

app = Flask(__name__)

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

API_VERSION = "2025-03"
USER_AGENT = "Catalogo Importados Agronomia (catalogo-importados-api.onrender.com)"

WHATSAPP = "11 6625-3738"
INSTAGRAM = "@importados.agronomia"

LOGO_URL = (
    "https://dcdn-us.mitiendanube.com/stores/006/777/697/themes/common/"
    "logo-4412898902984757469-1779314772-"
    "c30b03377e4c51f3c40cabda29a8000d1779314772-480-0.webp"
)

ORDEN_CATEGORIAS = [
    "MUJER",
    "HOMBRE",
    "NIÑOS",
    "ENTREGA INMEDIATA",
]

SUBTITULOS_CATEGORIAS = {
    "MUJER": "SPORT • URBAN • DENIM",
    "HOMBRE": "SPORT • URBAN • STREET",
    "NIÑOS": "SPORT • URBAN • KIDS",
    "ENTREGA INMEDIATA": "STOCK DISPONIBLE • ENTREGA RÁPIDA",
}

AUTH = {
    "access_token": None,
    "store_id": None,
}


def texto_localizado(valor):
    if isinstance(valor, dict):
        for idioma in ("es", "pt", "en"):
            if valor.get(idioma):
                return str(valor[idioma])
        if valor:
            return str(next(iter(valor.values())))
        return ""
    return str(valor or "")


def formato_precio(valor):
    try:
        numero = float(valor)
        return "$ {:,.0f}".format(numero).replace(",", ".")
    except Exception:
        return str(valor or "")


def obtener_precio(producto):
    precios = []

    for variante in producto.get("variants") or []:
        valor = variante.get("price")
        if valor in (None, ""):
            continue

        try:
            precios.append(float(valor))
        except Exception:
            pass

    if not precios:
        return "CONSULTAR"

    precios = sorted(set(precios))

    if len(precios) > 1:
        return "DESDE " + formato_precio(precios[0])

    return formato_precio(precios[0])


def headers_api():
    return {
        "Authorization": f"Bearer {AUTH['access_token']}",
        "Authentication": f"bearer {AUTH['access_token']}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def url_api(recurso):
    return (
        f"https://api.tiendanube.com/"
        f"{API_VERSION}/"
        f"{AUTH['store_id']}/"
        f"{recurso.lstrip('/')}"
    )


def obtener_categorias():
    mapa = {}
    pagina = 1

    while True:
        respuesta = requests.get(
            url_api("categories"),
            headers=headers_api(),
            params={
                "page": pagina,
                "per_page": 200,
            },
            timeout=30,
        )

        respuesta.raise_for_status()
        datos = respuesta.json()

        if not datos:
            break

        for categoria in datos:
            cid = categoria.get("id")
            nombre = texto_localizado(categoria.get("name")).strip()

            if cid is not None and nombre:
                mapa[cid] = nombre

        if len(datos) < 200:
            break

        pagina += 1

    return mapa


def obtener_productos():
    productos = []
    pagina = 1

    while True:
        respuesta = requests.get(
            url_api("products"),
            headers=headers_api(),
            params={
                "page": pagina,
                "per_page": 200,
            },
            timeout=45,
        )

        respuesta.raise_for_status()
        datos = respuesta.json()

        if not datos:
            break

        productos.extend(datos)

        if len(datos) < 200:
            break

        pagina += 1

    return productos


def nombres_categorias(producto, mapa_categorias):
    resultado = []

    for categoria in producto.get("categories") or []:
        if isinstance(categoria, dict):
            nombre = texto_localizado(categoria.get("name")).strip()

            if not nombre and categoria.get("id") in mapa_categorias:
                nombre = mapa_categorias[categoria.get("id")]

            if nombre:
                resultado.append(nombre)

        else:
            nombre = mapa_categorias.get(categoria)

            if nombre:
                resultado.append(nombre)

    return resultado


def categoria_principal(producto, mapa_categorias):
    categorias = nombres_categorias(producto, mapa_categorias)
    categorias_upper = [x.upper().strip() for x in categorias]

    # Entrega inmediata tiene prioridad comercial.
    if "ENTREGA INMEDIATA" in categorias_upper:
        return "ENTREGA INMEDIATA"

    for buscada in ("MUJER", "HOMBRE", "NIÑOS"):
        if buscada in categorias_upper:
            return buscada

    if categorias_upper:
        return categorias_upper[0]

    return "OTROS"


def foto_principal(producto):
    imagenes = producto.get("images") or []

    if not imagenes:
        return ""

    try:
        imagenes = sorted(
            imagenes,
            key=lambda img: img.get("position", 999),
        )
    except Exception:
        pass

    for imagen in imagenes:
        src = imagen.get("src")

        if src:
            return src

    return ""



def limpiar_html(valor):
    texto = texto_localizado(valor)
    texto = re.sub(r"<br\s*/?>", " ", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _valor_variante_texto(valor):
    if isinstance(valor, dict):
        return str(
            valor.get("es")
            or valor.get("pt")
            or valor.get("en")
            or valor.get("value")
            or valor.get("name")
            or ""
        ).strip()

    return str(valor or "").strip()


def obtener_talles(producto):
    """
    Intenta obtener talles desde las variantes de Tiendanube.
    Si no encuentra talles claros, busca una referencia simple
    dentro de la descripción del producto.
    """
    talles = []

    for variante in producto.get("variants") or []:
        for valor in variante.get("values") or []:
            candidato = _valor_variante_texto(valor).upper()

            if not candidato:
                continue

            # Talles habituales de indumentaria o numéricos.
            if re.fullmatch(r"(XS|S|M|L|XL|XXL|XXXL|[0-9]{1,3})", candidato):
                if candidato not in talles:
                    talles.append(candidato)

    if talles:
        return " • ".join(talles)

    descripcion = limpiar_html(producto.get("description"))

    if not descripcion:
        return ""

    patrones = [
        r"talles?\s*:?\s*([0-9]{1,3}\s*(?:al|a|-)\s*[0-9]{1,3})",
        r"talles?\s*:?\s*((?:xs|s|m|l|xl|xxl|xxxl)\s*(?:al|a|-)\s*(?:xs|s|m|l|xl|xxl|xxxl))",
        r"talles?\s*:?\s*((?:xs|s|m|l|xl|xxl|xxxl)(?:\s*,\s*(?:xs|s|m|l|xl|xxl|xxxl))+)",
    ]

    for patron in patrones:
        coincidencia = re.search(patron, descripcion, flags=re.I)

        if coincidencia:
            valor = re.sub(r"\s+", " ", coincidencia.group(1)).strip().upper()
            valor = valor.replace(" A ", " AL ").replace(" - ", " AL ")
            return valor

    return ""


def preparar_catalogo():
    mapa_categorias = obtener_categorias()
    productos_raw = obtener_productos()

    productos = []

    for producto in productos_raw:
        nombre = texto_localizado(producto.get("name")).strip()

        if not nombre:
            nombre = "PRODUCTO"

        categoria = categoria_principal(producto, mapa_categorias)

        productos.append(
            {
                "id": producto.get("id"),
                "nombre": nombre.upper(),
                "precio": obtener_precio(producto),
                "foto": foto_principal(producto),
                "categoria": categoria,
                "talles": obtener_talles(producto),
                "stock_inmediato": categoria == "ENTREGA INMEDIATA",
            }
        )

    return productos


def construir_paginas(productos):
    agrupados = {}

    for producto in productos:
        categoria = producto["categoria"]
        agrupados.setdefault(categoria, []).append(producto)

    categorias_finales = []

    for categoria in ORDEN_CATEGORIAS:
        if agrupados.get(categoria):
            categorias_finales.append(categoria)

    extras = sorted(
        categoria
        for categoria in agrupados.keys()
        if categoria not in categorias_finales
    )

    categorias_finales.extend(extras)

    paginas = []
    numero = 1

    for categoria in categorias_finales:
        productos_categoria = sorted(
            agrupados[categoria],
            key=lambda p: p["nombre"],
        )

        for inicio in range(0, len(productos_categoria), 6):
            paginas.append(
                {
                    "numero": numero,
                    "categoria": categoria,
                    "subtitulo": SUBTITULOS_CATEGORIAS.get(
                        categoria,
                        "IMPORTADOS AGRONOMÍA",
                    ),
                    "productos": productos_categoria[inicio : inicio + 6],
                }
            )
            numero += 1

    return paginas


TEMPLATE_CATALOGO = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Catálogo Importados Agronomía</title>

<style>
* {
    box-sizing: border-box;
}

html,
body {
    margin: 0;
    padding: 0;
}

body {
    font-family: Arial, Helvetica, sans-serif;
    background: #e9edf2;
    color: #151515;
}

.toolbar {
    position: sticky;
    top: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 18px;
    padding: 14px 16px;
    background: #ffffff;
    border-bottom: 1px solid #dde2e8;
    box-shadow: 0 4px 14px rgba(0,0,0,.07);
}

.toolbar-info {
    font-size: 14px;
    font-weight: 800;
    color: #153D7A;
}

.print-button {
    border: 0;
    border-radius: 10px;
    background: #153D7A;
    color: #ffffff;
    font-size: 14px;
    font-weight: 800;
    padding: 13px 22px;
    cursor: pointer;
    transition: .2s;
}

.print-button:hover {
    background: #102f60;
}

.print-button:disabled {
    opacity: .6;
    cursor: wait;
}

.status {
    min-width: 120px;
    color: #777;
    font-size: 12px;
}

.page {
    width: 210mm;
    height: 297mm;
    position: relative;
    margin: 12mm auto;
    background: #ffffff;
    box-shadow: 0 8px 32px rgba(0,0,0,.12);
    overflow: hidden;
    page-break-after: always;
    break-after: page;
}

/* ==========================================================
   ENCABEZADO
   ========================================================== */

.header {
    position: absolute;
    left: 12mm;
    right: 12mm;
    top: 7mm;
    height: 15mm;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.logo {
    display: block;
    max-width: 50mm;
    max-height: 12mm;
    object-fit: contain;
}

.header-right {
    text-align: right;
}

.header-title {
    color: #153D7A;
    font-size: 8.8pt;
    font-weight: 900;
    letter-spacing: .9px;
}

.header-mini {
    margin-top: 1mm;
    color: #8a8f97;
    font-size: 6.8pt;
    font-weight: 700;
    letter-spacing: .4px;
}

.brand-line {
    position: absolute;
    left: 12mm;
    right: 12mm;
    top: 22mm;
    height: 1.2mm;
    background: linear-gradient(
        90deg,
        #D91F2A 0 18%,
        #153D7A 18% 100%
    );
}

/* ==========================================================
   CATEGORÍA
   ========================================================== */

.category-block {
    position: absolute;
    top: 28mm;
    left: 12mm;
    right: 12mm;
    height: 12mm;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
}

.category {
    color: #153D7A;
    font-size: 18pt;
    line-height: 1;
    font-weight: 900;
    letter-spacing: .5px;
}

.category-subtitle {
    margin-top: 1.2mm;
    color: #D91F2A;
    font-size: 7.5pt;
    line-height: 1;
    font-weight: 800;
    letter-spacing: 1.4px;
}

/* ==========================================================
   PRODUCTOS
   ========================================================== */

.products {
    position: absolute;
    top: 43mm;
    left: 12mm;
    right: 12mm;
    bottom: 19mm;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    grid-template-rows: repeat(3, 1fr);
    column-gap: 6mm;
    row-gap: 4mm;
}

.product {
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    align-items: center;
    background: #ffffff;
    border: .25mm solid #e7e9ed;
    border-radius: 4mm;
    padding: 3mm 3mm 2.8mm;
}

.product.stock {
    border-color: #e6b7bb;
}

.stock-badge {
    position: absolute;
    top: 3mm;
    left: 3mm;
    z-index: 10;
    padding: 1.4mm 2.4mm;
    border-radius: 20mm;
    background: #D91F2A;
    color: #ffffff;
    font-size: 6.4pt;
    font-weight: 900;
    letter-spacing: .7px;
}

.photo-box {
    width: 100%;
    height: 55mm;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    background: #ffffff;
    border-radius: 2.5mm;
}

.photo {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
}

.no-photo {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #f5f6f8;
    color: #91969d;
    font-size: 8pt;
    font-weight: 700;
}

.product-name {
    width: 100%;
    min-height: 9mm;
    margin-top: 2mm;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    font-size: 10pt;
    line-height: 1.05;
    font-weight: 800;
    color: #171717;
    overflow: hidden;
}

.product-sizes {
    min-height: 4.7mm;
    margin-top: .8mm;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #747980;
    font-size: 7.4pt;
    line-height: 1;
    font-weight: 700;
    text-align: center;
}

.product-price {
    margin-top: auto;
    padding-top: 1.3mm;
    text-align: center;
    color: #D91F2A;
    font-size: 15.5pt;
    line-height: 1;
    font-weight: 900;
}

.price-line {
    width: 17mm;
    height: .7mm;
    margin: 1.4mm auto 0;
    border-radius: 2mm;
    background: #153D7A;
}

/* ==========================================================
   PIE
   ========================================================== */

.footer {
    position: absolute;
    left: 12mm;
    right: 12mm;
    bottom: 6mm;
    height: 9mm;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: .3mm solid #dfe3e7;
    padding-top: 3mm;
    font-size: 8pt;
    font-weight: 800;
}

.footer-left {
    color: #153D7A;
}

.footer-center {
    color: #D91F2A;
}

.footer-right {
    color: #666;
    text-align: right;
}

.footer-secondary {
    color: #858a91;
    font-weight: 700;
}

/* ==========================================================
   IMPRESIÓN
   ========================================================== */

@media print {
    @page {
        size: A4;
        margin: 0;
    }

    html,
    body {
        width: 210mm;
        margin: 0;
        padding: 0;
        background: #ffffff;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }

    .toolbar {
        display: none !important;
    }

    .page {
        margin: 0;
        box-shadow: none;
        page-break-after: always;
        break-after: page;
    }

    .page:last-child {
        page-break-after: auto;
    }
}
</style>
</head>

<body>

<div class="toolbar">
    <div class="toolbar-info">
        {{ cantidad }} PRODUCTOS · {{ total_paginas }} PÁGINAS
    </div>

    <button id="printButton" class="print-button" onclick="guardarPDF()">
        GUARDAR CATÁLOGO PDF
    </button>

    <div id="status" class="status">
        Cargando fotos...
    </div>
</div>

{% for pagina in paginas %}

<section class="page">

    <header class="header">
        <img class="logo" src="{{ logo_url }}" alt="Importados Agronomía">

        <div class="header-right">
            <div class="header-title">
                CATÁLOGO DE PRODUCTOS
            </div>

            <div class="header-mini">
                IMPORTADOS AGRONOMÍA
            </div>
        </div>
    </header>

    <div class="brand-line"></div>

    <div class="category-block">
        <div class="category">
            {{ pagina.categoria }}
        </div>

        <div class="category-subtitle">
            {{ pagina.subtitulo }}
        </div>
    </div>

    <div class="products">

        {% for producto in pagina.productos %}

        <article class="product {% if producto.stock_inmediato %}stock{% endif %}">

            {% if producto.stock_inmediato %}
            <div class="stock-badge">
                STOCK INMEDIATO
            </div>
            {% endif %}

            <div class="photo-box">

                {% if producto.foto %}

                <img
                    class="photo"
                    src="{{ producto.foto }}"
                    alt="{{ producto.nombre }}"
                    loading="eager"
                >

                {% else %}

                <div class="no-photo">
                    FOTO NO DISPONIBLE
                </div>

                {% endif %}

            </div>

            <div class="product-name">
                {{ producto.nombre }}
            </div>

            <div class="product-sizes">
                {% if producto.talles %}
                    Talles: {{ producto.talles }}
                {% endif %}
            </div>

            <div class="product-price">
                {{ producto.precio }}
            </div>

            <div class="price-line"></div>

        </article>

        {% endfor %}

    </div>

    <footer class="footer">

        <div class="footer-left">
            WhatsApp {{ whatsapp }}
            <span class="footer-secondary">
                · Envíos a todo el país
            </span>
        </div>

        <div class="footer-center">
            {{ instagram }}
        </div>

        <div class="footer-right">
            {{ pagina.numero }} / {{ total_paginas }}
        </div>

    </footer>

</section>

{% endfor %}

<script>
async function esperarImagenes() {
    const imagenes = Array.from(document.querySelectorAll("img"));

    await Promise.all(
        imagenes.map((imagen) => {
            if (imagen.complete) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                imagen.addEventListener("load", resolve, { once: true });
                imagen.addEventListener("error", resolve, { once: true });
            });
        })
    );
}

async function guardarPDF() {
    const boton = document.getElementById("printButton");
    const status = document.getElementById("status");

    boton.disabled = true;
    boton.textContent = "PREPARANDO...";
    status.textContent = "Esperando fotos";

    await esperarImagenes();

    status.textContent = "Catálogo listo";
    boton.textContent = "GUARDAR CATÁLOGO PDF";
    boton.disabled = false;

    setTimeout(() => {
        window.print();
    }, 250);
}

window.addEventListener("load", async () => {
    await esperarImagenes();

    document.getElementById("status").textContent = "Listo para PDF";
});
</script>

</body>
</html>
"""



@app.route("/")
def home():
    return """
    <!doctype html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <title>Importados Agronomía</title>
    </head>

    <body style="
        margin:0;
        padding:50px 20px;
        font-family:Arial,sans-serif;
        background:#f2f4f7;
        text-align:center;
    ">

        <div style="
            max-width:650px;
            margin:auto;
            padding:40px;
            background:white;
            border-radius:18px;
            box-shadow:0 10px 30px rgba(0,0,0,.08);
        ">

            <h1 style="color:#153D7A;">
                IMPORTADOS
                <span style="color:#D91F2A;">AGRONOMÍA</span>
            </h1>

            <p>
                Generador de catálogo conectado a Tiendanube.
            </p>

            <p>
                Ingresá desde el Link de Instalación de
                CATALOGO IMPORTADOS para actualizar el catálogo.
            </p>

        </div>

    </body>
    </html>
    """


@app.route("/tiendanube/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return "Falta el código de autorización.", 400

    respuesta = requests.post(
        "https://www.tiendanube.com/apps/authorize/token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
        },
        timeout=30,
    )

    if respuesta.status_code != 200:
        return "Error autorizando Tiendanube: " + respuesta.text, 400

    datos = respuesta.json()

    AUTH["access_token"] = datos.get("access_token")
    AUTH["store_id"] = datos.get("user_id")

    if not AUTH["access_token"] or not AUTH["store_id"]:
        return "No se pudo obtener el acceso a Tiendanube.", 400

    return redirect(url_for("catalogo"))


@app.route("/catalogo")
def catalogo():
    if not AUTH.get("access_token") or not AUTH.get("store_id"):
        return """
        <h2>
            Primero autorizá CATALOGO IMPORTADOS desde Tiendanube.
        </h2>
        """, 401

    try:
        productos = preparar_catalogo()
        paginas = construir_paginas(productos)

        return render_template_string(
            TEMPLATE_CATALOGO,
            paginas=paginas,
            cantidad=len(productos),
            total_paginas=len(paginas),
            logo_url=LOGO_URL,
            whatsapp=WHATSAPP,
            instagram=INSTAGRAM,
        )

    except Exception as e:
        print("ERROR CATALOGO:", repr(e))
        return "Error armando el catálogo: " + str(e), 500


@app.route("/catalogo.pdf")
def catalogo_pdf():
    return redirect(url_for("catalogo"))


@app.route("/webhooks/store-redact", methods=["POST"])
def store_redact():
    AUTH["access_token"] = None
    AUTH["store_id"] = None
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
