import os
import re
import unicodedata
import requests
from flask import Flask, request, jsonify, redirect, url_for, render_template_string

app = Flask(__name__)

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

API_VERSION = "2025-03"
USER_AGENT = "Catalogo Importados Agronomia (catalogo-importados-api.onrender.com)"

WHATSAPP = "11 6625-3738"
INSTAGRAM = "@importados.agronomia"
FOOTER_TEXT = "Envíos a todo el país"
STORE_URL = "https://importadosagronomia.mitiendanube.com"

LOGO_URL = (
    "https://dcdn-us.mitiendanube.com/stores/006/777/697/themes/common/"
    "logo-4412898902984757469-1779314772-"
    "c30b03377e4c51f3c40cabda29a8000d1779314772-480-0.webp"
)

MAYORISTA_FACTOR = 0.80

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


# ============================================================
# HELPERS
# ============================================================

def texto_localizado(valor):
    if isinstance(valor, dict):
        for idioma in ("es", "pt", "en"):
            if valor.get(idioma):
                return str(valor[idioma])

        if valor:
            return str(next(iter(valor.values())))

        return ""

    return str(valor or "")


def limpiar_html(valor):
    texto = texto_localizado(valor)
    texto = re.sub(r"<br\s*/?>", " ", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def normalizar_categoria(texto):
    valor = str(texto or "").strip().upper()
    valor_ascii = unicodedata.normalize("NFD", valor)
    valor_ascii = "".join(
        caracter
        for caracter in valor_ascii
        if unicodedata.category(caracter) != "Mn"
    )

    equivalencias = {
        "MUJER": "MUJER",
        "MUJERES": "MUJER",
        "HOMBRE": "HOMBRE",
        "HOMBRES": "HOMBRE",
        "NINO": "NIÑOS",
        "NINOS": "NIÑOS",
        "NINA": "NIÑOS",
        "NINAS": "NIÑOS",
        "NINOS Y NINAS": "NIÑOS",
        "ENTREGA INMEDIATA": "ENTREGA INMEDIATA",
    }

    return equivalencias.get(valor_ascii, valor)


def formato_precio(valor):
    try:
        numero = float(valor)
        return "$ {:,.0f}".format(numero).replace(",", ".")
    except Exception:
        return str(valor or "")


def obtener_precio(producto, factor=1.0):
    precios = []

    for variante in producto.get("variants") or []:
        valor = variante.get("price")

        if valor in (None, ""):
            continue

        try:
            precios.append(float(valor) * factor)
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


def slug_anchor(texto):
    valor = str(texto or "").lower().strip()
    valor = unicodedata.normalize("NFD", valor)
    valor = "".join(
        caracter
        for caracter in valor
        if unicodedata.category(caracter) != "Mn"
    )
    valor = re.sub(r"[^a-z0-9]+", "-", valor)
    return valor.strip("-")


# ============================================================
# API TIENDANUBE
# ============================================================

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
        else:
            nombre = mapa_categorias.get(categoria, "")

        if nombre:
            nombre_normalizado = normalizar_categoria(nombre)

            if nombre_normalizado not in resultado:
                resultado.append(nombre_normalizado)

    return resultado


def categoria_principal(categorias):
    if "ENTREGA INMEDIATA" in categorias:
        return "ENTREGA INMEDIATA"

    for buscada in ("MUJER", "HOMBRE", "NIÑOS"):
        if buscada in categorias:
            return buscada

    if categorias:
        return categorias[0]

    return "OTROS"


def foto_principal(producto):
    imagenes = producto.get("images") or []

    if not imagenes:
        return ""

    try:
        imagenes = sorted(
            imagenes,
            key=lambda imagen: imagen.get("position", 999),
        )
    except Exception:
        pass

    for imagen in imagenes:
        src = imagen.get("src")

        if src:
            return src

    return ""


def url_producto(producto):
    canonical = producto.get("canonical_url")

    if isinstance(canonical, dict):
        canonical = (
            canonical.get("es")
            or canonical.get("pt")
            or canonical.get("en")
            or next(iter(canonical.values()), "")
        )

    canonical = str(canonical or "").strip()

    if canonical:
        return canonical

    handle = producto.get("handle")

    if isinstance(handle, dict):
        handle = (
            handle.get("es")
            or handle.get("pt")
            or handle.get("en")
            or next(iter(handle.values()), "")
        )

    handle = str(handle or "").strip().strip("/")

    if handle:
        return f"{STORE_URL}/productos/{handle}/"

    return ""


# ============================================================
# TALLES
# ============================================================

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
    talles = []

    for variante in producto.get("variants") or []:
        for valor in variante.get("values") or []:
            candidato = _valor_variante_texto(valor).upper()

            if not candidato:
                continue

            if re.fullmatch(
                r"(XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|XXXXL|[0-9]{1,3})",
                candidato,
            ):
                if candidato not in talles:
                    talles.append(candidato)

    if talles:
        return " • ".join(talles)

    descripcion = limpiar_html(producto.get("description"))

    if not descripcion:
        return ""

    patrones = [
        r"talles?\s*:?\s*([0-9]{1,3}\s*(?:al|a|-)\s*[0-9]{1,3})",
        r"talles?\s*:?\s*((?:xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|xxxxl)\s*(?:al|a|-)\s*(?:xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|xxxxl))",
        r"talles?\s*:?\s*((?:xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|xxxxl)(?:\s*,\s*(?:xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|xxxxl))+)",
    ]

    for patron in patrones:
        coincidencia = re.search(patron, descripcion, flags=re.I)

        if coincidencia:
            valor = re.sub(
                r"\s+",
                " ",
                coincidencia.group(1),
            ).strip().upper()

            valor = valor.replace(" A ", " AL ")
            valor = valor.replace(" - ", " AL ")

            return valor

    return ""


# ============================================================
# PREPARAR CATÁLOGO
# ============================================================

def preparar_catalogo(factor_precio=1.0):
    mapa_categorias = obtener_categorias()
    productos_raw = obtener_productos()

    productos = []

    for producto in productos_raw:
        nombre = texto_localizado(
            producto.get("name")
        ).strip()

        if not nombre:
            nombre = "PRODUCTO"

        categorias = nombres_categorias(
            producto,
            mapa_categorias,
        )

        productos.append(
            {
                "id": producto.get("id"),
                "nombre": nombre.upper(),
                "precio": obtener_precio(
                    producto,
                    factor=factor_precio,
                ),
                "foto": foto_principal(producto),
                "categoria": categoria_principal(categorias),
                "categorias": categorias,
                "talles": obtener_talles(producto),
                "url": url_producto(producto),
                "stock_inmediato": "ENTREGA INMEDIATA" in categorias,
            }
        )

    return productos


def producto_pertenece(producto, categoria):
    return categoria in (producto.get("categorias") or [])


def construir_paginas(productos, filtro=None):
    agrupados = {}

    if filtro:
        seleccionados = [
            producto
            for producto in productos
            if producto_pertenece(producto, filtro)
        ]

        if seleccionados:
            agrupados[filtro] = seleccionados

    else:
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
        and agrupados.get(categoria)
    )

    categorias_finales.extend(extras)

    paginas = []
    numero = 1

    for categoria in categorias_finales:
        productos_categoria = sorted(
            agrupados[categoria],
            key=lambda producto: producto["nombre"],
        )

        for inicio in range(
            0,
            len(productos_categoria),
            6,
        ):
            paginas.append(
                {
                    "numero": numero,
                    "categoria": categoria,
                    "subtitulo": SUBTITULOS_CATEGORIAS.get(
                        categoria,
                        "IMPORTADOS AGRONOMÍA",
                    ),
                    "anchor": slug_anchor(categoria),
                    "es_primera": inicio == 0,
                    "productos": productos_categoria[
                        inicio:inicio + 6
                    ],
                }
            )

            numero += 1

    return paginas


# ============================================================
# MENÚ DE 5 DESCARGAS
# ============================================================

TEMPLATE_MENU = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Catálogos · Importados Agronomía</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    padding: 40px 18px;
    font-family: Arial, Helvetica, sans-serif;
    background: #f1f4f8;
    color: #151515;
}

.wrap {
    max-width: 930px;
    margin: 0 auto;
}

.hero {
    position: relative;
    overflow: hidden;
    padding: 34px 38px;
    background: #153D7A;
    color: #ffffff;
    border-radius: 22px;
}

.hero::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    width: 10px;
    height: 100%;
    background: #D91F2A;
}

.logo-box {
    display: inline-block;
    padding: 10px 15px;
    background: #ffffff;
    border-radius: 10px;
}

.menu-logo {
    display: block;
    width: 190px;
    max-height: 54px;
    object-fit: contain;
}

.hero h1 {
    margin: 23px 0 8px;
    font-size: 31px;
}

.hero p {
    margin: 0;
    color: rgba(255,255,255,.82);
    font-weight: 700;
}

.section {
    margin-top: 28px;
}

.section-title {
    margin-bottom: 13px;
    color: #153D7A;
    font-size: 18px;
    font-weight: 900;
}

.grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
}

.card {
    display: flex;
    min-height: 108px;
    align-items: center;
    justify-content: space-between;
    padding: 22px;
    background: #ffffff;
    border: 1px solid #e1e5ea;
    border-radius: 16px;
    text-decoration: none;
    color: #151515;
    box-shadow: 0 5px 18px rgba(0,0,0,.045);
}

.card:hover {
    border-color: #153D7A;
}

.card.mayorista {
    border-color: #ecc1c5;
}

.card-title {
    color: #153D7A;
    font-size: 17px;
    font-weight: 900;
}

.card.mayorista .card-title {
    color: #D91F2A;
}

.card-sub {
    margin-top: 5px;
    color: #777d85;
    font-size: 12px;
    line-height: 1.35;
    font-weight: 700;
}

.arrow {
    color: #D91F2A;
    font-size: 28px;
    font-weight: 900;
}

.note {
    margin-top: 20px;
    color: #777d85;
    font-size: 12px;
    line-height: 1.5;
}

@media (max-width: 680px) {
    .grid {
        grid-template-columns: 1fr;
    }
}
</style>
</head>

<body>

<div class="wrap">

    <section class="hero">

        <div class="logo-box">
            <img
                class="menu-logo"
                src="{{ logo_url }}"
                alt="Importados Agronomía"
            >
        </div>

        <h1>
            Catálogos actualizados
        </h1>

        <p>
            Elegí la versión que querés abrir y después
            tocá “Guardar catálogo PDF”.
        </p>

    </section>

    <section class="section">

        <div class="section-title">
            MINORISTA
        </div>

        <div class="grid">

            <a
                class="card"
                href="{{ url_for('catalogo', tipo='completo') }}"
            >
                <div>
                    <div class="card-title">
                        Catálogo completo
                    </div>

                    <div class="card-sub">
                        Mujer · Hombre · Niños · Entrega inmediata
                    </div>
                </div>

                <div class="arrow">
                    →
                </div>
            </a>

            <a
                class="card"
                href="{{ url_for('catalogo', tipo='hombre') }}"
            >
                <div>
                    <div class="card-title">
                        Catálogo Hombres
                    </div>

                    <div class="card-sub">
                        Solo productos para Hombre
                    </div>
                </div>

                <div class="arrow">
                    →
                </div>
            </a>

            <a
                class="card"
                href="{{ url_for('catalogo', tipo='mujer') }}"
            >
                <div>
                    <div class="card-title">
                        Catálogo Mujeres
                    </div>

                    <div class="card-sub">
                        Solo productos para Mujer
                    </div>
                </div>

                <div class="arrow">
                    →
                </div>
            </a>

            <a
                class="card"
                href="{{ url_for('catalogo', tipo='ninos') }}"
            >
                <div>
                    <div class="card-title">
                        Catálogo Niños
                    </div>

                    <div class="card-sub">
                        Solo productos para Niños
                    </div>
                </div>

                <div class="arrow">
                    →
                </div>
            </a>

        </div>

    </section>

    <section class="section">

        <div class="section-title">
            MAYORISTA
        </div>

        <div class="grid">

            <a
                class="card mayorista"
                href="{{ url_for('catalogo', tipo='mayorista') }}"
            >
                <div>
                    <div class="card-title">
                        Catálogo Mayorista completo
                    </div>

                    <div class="card-sub">
                        Versión completa para venta mayorista
                    </div>
                </div>

                <div class="arrow">
                    →
                </div>
            </a>

        </div>

    </section>

    <div class="note">
        Los 5 catálogos toman productos, fotos y precios
        desde Tiendanube.
    </div>

</div>

</body>
</html>
"""


# ============================================================
# TEMPLATE DE CATÁLOGO
# ============================================================

TEMPLATE_CATALOGO = r"""
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>{{ titulo_documento }}</title>

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
    color: #153D7A;
    font-size: 14px;
    font-weight: 900;
}

.print-button {
    border: 0;
    border-radius: 10px;
    padding: 13px 22px;
    background: #153D7A;
    color: #ffffff;
    font-size: 14px;
    font-weight: 900;
    cursor: pointer;
}

.print-button:disabled {
    opacity: .6;
    cursor: wait;
}

.status {
    min-width: 115px;
    color: #777;
    font-size: 12px;
}

.page {
    width: 210mm;
    height: 297mm;
    position: relative;
    margin: 12mm auto;
    overflow: hidden;
    background: #ffffff;
    box-shadow: 0 8px 32px rgba(0,0,0,.12);
    page-break-after: always;
    break-after: page;
}


/* ==========================================================
   PORTADA
   ========================================================== */

.cover {
    background: #153D7A;
    color: #ffffff;
}

.cover-red {
    position: absolute;
    left: 0;
    top: 0;
    width: 16mm;
    height: 100%;
    background: #D91F2A;
}

.cover-inner {
    position: absolute;
    left: 29mm;
    right: 22mm;
    top: 34mm;
    bottom: 27mm;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}

.cover-logo-box {
    display: inline-block;
    width: fit-content;
    padding: 5mm 7mm;
    background: #ffffff;
    border-radius: 4mm;
}

.cover-logo {
    display: block;
    max-width: 72mm;
    max-height: 19mm;
    object-fit: contain;
}

.cover-kicker {
    margin-top: 27mm;
    color: rgba(255,255,255,.72);
    font-size: 9pt;
    font-weight: 900;
    letter-spacing: 2.2px;
}

.cover-title {
    max-width: 150mm;
    margin-top: 4mm;
    font-size: 34pt;
    line-height: .96;
    font-weight: 900;
}

.cover-subtitle {
    margin-top: 6mm;
    color: rgba(255,255,255,.85);
    font-size: 13pt;
    font-weight: 700;
}

.cover-bottom {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    padding-top: 6mm;
    border-top: .35mm solid rgba(255,255,255,.35);
    font-size: 9pt;
    line-height: 1.55;
    font-weight: 800;
}


/* ==========================================================
   ÍNDICE
   ========================================================== */

.index-header {
    position: absolute;
    left: 18mm;
    right: 18mm;
    top: 26mm;
}

.index-title {
    color: #153D7A;
    font-size: 27pt;
    font-weight: 900;
}

.index-subtitle {
    margin-top: 3mm;
    color: #747a82;
    font-size: 10pt;
    font-weight: 700;
}

.index-grid {
    position: absolute;
    left: 18mm;
    right: 18mm;
    top: 62mm;
    display: grid;
    grid-template-columns: 1fr;
    gap: 6mm;
}

.index-link {
    min-height: 34mm;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 10mm;
    border: .35mm solid #dfe3e8;
    border-radius: 5mm;
    background: #ffffff;
    color: #153D7A;
    text-decoration: none;
}

.index-name {
    font-size: 20pt;
    font-weight: 900;
}

.index-meta {
    margin-top: 2mm;
    color: #D91F2A;
    font-size: 8pt;
    font-weight: 900;
    letter-spacing: 1px;
}

.index-arrow {
    color: #D91F2A;
    font-size: 25pt;
    font-weight: 900;
}


/* ==========================================================
   ENCABEZADO INTERNO
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
    font-size: 8.6pt;
    font-weight: 900;
    letter-spacing: .8px;
}

.header-mini {
    margin-top: 1mm;
    color: #8a8f97;
    font-size: 6.7pt;
    font-weight: 700;
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
    left: 12mm;
    right: 12mm;
    top: 28mm;
    height: 12mm;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

.category {
    color: #153D7A;
    font-size: 18pt;
    line-height: 1;
    font-weight: 900;
}

.category-subtitle {
    margin-top: 1.2mm;
    color: #D91F2A;
    font-size: 7.4pt;
    font-weight: 900;
    letter-spacing: 1.3px;
}


/* ==========================================================
   PRODUCTOS
   ========================================================== */

.products {
    position: absolute;
    left: 12mm;
    right: 12mm;
    top: 43mm;
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
    padding: 3mm 3mm 2.4mm;
    background: #ffffff;
    border: .25mm solid #e7e9ed;
    border-radius: 4mm;
}

.product.stock {
    border-color: #e7b7bc;
}

.stock-badge {
    position: absolute;
    left: 3mm;
    top: 3mm;
    z-index: 10;
    padding: 1.3mm 2.3mm;
    background: #D91F2A;
    color: #ffffff;
    border-radius: 20mm;
    font-size: 6.2pt;
    font-weight: 900;
    letter-spacing: .6px;
}

.product-link {
    color: inherit;
    text-decoration: none;
}

.photo-box {
    width: 100%;
    height: 51mm;
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

/* Nombre más chico, máximo 3 líneas, sin superposición */
.product-name {
    width: 100%;
    height: 10mm;
    margin-top: 1.4mm;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    overflow: hidden;
    color: #171717;
    text-align: center;
    font-size: 8.4pt;
    line-height: 1.08;
    font-weight: 800;
}

.product-sizes {
    width: 100%;
    height: 4.3mm;
    margin-top: .3mm;
    overflow: hidden;
    color: #747980;
    text-align: center;
    font-size: 6.8pt;
    line-height: 1.15;
    font-weight: 700;
}

.product-more {
    width: 100%;
    height: 4mm;
    margin-top: .2mm;
    overflow: hidden;
    color: #153D7A;
    text-align: center;
    font-size: 6.4pt;
    line-height: 1.1;
    font-weight: 900;
    letter-spacing: .35px;
}

.product-price {
    margin-top: auto;
    color: #D91F2A;
    text-align: center;
    font-size: 14.7pt;
    line-height: 1;
    font-weight: 900;
}

.price-line {
    width: 17mm;
    height: .65mm;
    margin: 1.1mm auto 0;
    background: #153D7A;
    border-radius: 2mm;
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
    padding-top: 3mm;
    border-top: .3mm solid #dfe3e7;
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
    color: #666666;
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
        {{ cantidad }} PRODUCTOS · {{ total_paginas_con_inicio }} PÁGINAS
    </div>

    <button
        id="printButton"
        class="print-button"
        onclick="guardarPDF()"
    >
        GUARDAR CATÁLOGO PDF
    </button>

    <div id="status" class="status">
        Cargando fotos...
    </div>

</div>


<!-- ========================================================
     PORTADA
     ======================================================== -->

<section class="page cover">

    <div class="cover-red"></div>

    <div class="cover-inner">

        <div>

            <div class="cover-logo-box">
                <img
                    class="cover-logo"
                    src="{{ logo_url }}"
                    alt="Importados Agronomía"
                >
            </div>

            <div class="cover-kicker">
                IMPORTADOS AGRONOMÍA
            </div>

            <div class="cover-title">
                {{ portada_titulo }}
            </div>

            <div class="cover-subtitle">
                Indumentaria deportiva y urbana
            </div>

        </div>

        <div class="cover-bottom">

            <div>
                WhatsApp {{ whatsapp }}<br>
                {{ instagram }}
            </div>

            <div>
                CATÁLOGO 2026
            </div>

        </div>

    </div>

</section>


<!-- ========================================================
     ÍNDICE
     ======================================================== -->

{% if mostrar_indice %}

<section class="page">

    <div class="index-header">

        <div class="index-title">
            ELEGÍ TU SECCIÓN
        </div>

        <div class="index-subtitle">
            Tocá una categoría para ir directamente a esa parte del catálogo.
        </div>

    </div>

    <div class="index-grid">

        {% for item in indice %}

        <a
            class="index-link"
            href="#{{ item.anchor }}"
        >

            <div>

                <div class="index-name">
                    {{ item.nombre }}
                </div>

                <div class="index-meta">
                    {{ item.subtitulo }}
                </div>

            </div>

            <div class="index-arrow">
                →
            </div>

        </a>

        {% endfor %}

    </div>

</section>

{% endif %}


<!-- ========================================================
     PRODUCTOS
     ======================================================== -->

{% for pagina in paginas %}

<section
    class="page"
    {% if pagina.es_primera %}
        id="{{ pagina.anchor }}"
    {% endif %}
>

    <header class="header">

        <img
            class="logo"
            src="{{ logo_url }}"
            alt="Importados Agronomía"
        >

        <div class="header-right">

            <div class="header-title">
                {{ encabezado_titulo }}
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

        <article
            class="product {% if producto.stock_inmediato %}stock{% endif %}"
        >

            {% if producto.stock_inmediato %}

            <div class="stock-badge">
                STOCK INMEDIATO
            </div>

            {% endif %}


            <!-- FOTO -->

            {% if links_productos and producto.url %}

            <a
                class="product-link photo-box"
                href="{{ producto.url }}"
                target="_blank"
            >

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

            </a>

            {% else %}

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

            {% endif %}


            <!-- NOMBRE -->

            {% if links_productos and producto.url %}

            <a
                class="product-link product-name"
                href="{{ producto.url }}"
                target="_blank"
            >
                {{ producto.nombre }}
            </a>

            {% else %}

            <div class="product-name">
                {{ producto.nombre }}
            </div>

            {% endif %}


            <!-- TALLES -->

            <div class="product-sizes">

                {% if producto.talles %}
                    Talles: {{ producto.talles }}
                {% endif %}

            </div>


            <!-- VER MÁS MODELOS: SOLO MINORISTA -->

            {% if links_productos and producto.url %}

            <a
                class="product-link product-more"
                href="{{ producto.url }}"
                target="_blank"
            >
                VER MÁS MODELOS →
            </a>

            {% else %}

            <div class="product-more"></div>

            {% endif %}


            <!-- PRECIO -->

            <div class="product-price">
                {{ producto.precio }}
            </div>

            <div class="price-line"></div>

        </article>

        {% endfor %}

    </div>


    <footer class="footer">

        <div class="footer-left">
            WhatsApp {{ whatsapp }} · {{ footer_text }}
        </div>

        <div class="footer-center">
            {{ instagram }}
        </div>

        <div class="footer-right">
            {{ pagina.numero + paginas_offset }}
            /
            {{ total_paginas_con_inicio }}
        </div>

    </footer>

</section>

{% endfor %}


<script>
async function esperarImagenes() {
    const imagenes = Array.from(
        document.querySelectorAll("img")
    );

    await Promise.all(
        imagenes.map((imagen) => {
            if (imagen.complete) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                imagen.addEventListener(
                    "load",
                    resolve,
                    { once: true }
                );

                imagen.addEventListener(
                    "error",
                    resolve,
                    { once: true }
                );
            });
        })
    );
}


async function guardarPDF() {
    const boton = document.getElementById(
        "printButton"
    );

    const status = document.getElementById(
        "status"
    );

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


window.addEventListener(
    "load",
    async () => {
        await esperarImagenes();

        document.getElementById(
            "status"
        ).textContent = "Listo para PDF";
    }
);
</script>

</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    if AUTH.get("access_token") and AUTH.get("store_id"):
        return redirect(url_for("descargas"))

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
                <span style="color:#D91F2A;">
                    AGRONOMÍA
                </span>
            </h1>

            <p>
                Generador de catálogos conectado a Tiendanube.
            </p>

            <p>
                Ingresá desde el Link de Instalación de
                CATALOGO IMPORTADOS para actualizar los catálogos.
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
        return (
            "Error autorizando Tiendanube: "
            + respuesta.text,
            400,
        )

    datos = respuesta.json()

    AUTH["access_token"] = datos.get("access_token")
    AUTH["store_id"] = datos.get("user_id")

    if (
        not AUTH["access_token"]
        or not AUTH["store_id"]
    ):
        return (
            "No se pudo obtener el acceso a Tiendanube.",
            400,
        )

    return redirect(
        url_for("descargas")
    )


@app.route("/descargas")
def descargas():
    if (
        not AUTH.get("access_token")
        or not AUTH.get("store_id")
    ):
        return """
        <h2>
            Primero autorizá CATALOGO IMPORTADOS
            desde Tiendanube.
        </h2>
        """, 401

    return render_template_string(
        TEMPLATE_MENU,
        logo_url=LOGO_URL,
    )


@app.route("/catalogo")
def catalogo():
    if (
        not AUTH.get("access_token")
        or not AUTH.get("store_id")
    ):
        return """
        <h2>
            Primero autorizá CATALOGO IMPORTADOS
            desde Tiendanube.
        </h2>
        """, 401

    tipo = (
        request.args.get("tipo")
        or "completo"
    ).lower().strip()

    configuraciones = {
        "completo": {
            "factor": 1.0,
            "filtro": None,
            "portada": "CATÁLOGO DE PRODUCTOS",
            "encabezado": "CATÁLOGO DE PRODUCTOS",
            "titulo": "Catálogo Importados Agronomía",
            "mostrar_indice": True,
            "links": True,
        },
        "hombre": {
            "factor": 1.0,
            "filtro": "HOMBRE",
            "portada": "CATÁLOGO HOMBRES",
            "encabezado": "CATÁLOGO HOMBRES",
            "titulo": "Catálogo Hombres · Importados Agronomía",
            "mostrar_indice": False,
            "links": True,
        },
        "mujer": {
            "factor": 1.0,
            "filtro": "MUJER",
            "portada": "CATÁLOGO MUJERES",
            "encabezado": "CATÁLOGO MUJERES",
            "titulo": "Catálogo Mujeres · Importados Agronomía",
            "mostrar_indice": False,
            "links": True,
        },
        "ninos": {
            "factor": 1.0,
            "filtro": "NIÑOS",
            "portada": "CATÁLOGO NIÑOS",
            "encabezado": "CATÁLOGO NIÑOS",
            "titulo": "Catálogo Niños · Importados Agronomía",
            "mostrar_indice": False,
            "links": True,
        },
        "mayorista": {
            "factor": MAYORISTA_FACTOR,
            "filtro": None,
            "portada": "CATÁLOGO MAYORISTA",
            "encabezado": "CATÁLOGO MAYORISTA",
            "titulo": "Catálogo Mayorista · Importados Agronomía",
            "mostrar_indice": True,
            "links": False,
        },
    }

    config = configuraciones.get(
        tipo,
        configuraciones["completo"],
    )

    try:
        productos = preparar_catalogo(
            factor_precio=config["factor"]
        )

        paginas = construir_paginas(
            productos,
            filtro=config["filtro"],
        )

        categorias_presentes = []

        for pagina in paginas:
            categoria = pagina["categoria"]

            if categoria not in categorias_presentes:
                categorias_presentes.append(categoria)

        indice = [
            {
                "nombre": categoria,
                "anchor": slug_anchor(categoria),
                "subtitulo": SUBTITULOS_CATEGORIAS.get(
                    categoria,
                    "IMPORTADOS AGRONOMÍA",
                ),
            }
            for categoria in categorias_presentes
            if categoria in ORDEN_CATEGORIAS
        ]

        paginas_offset = (
            2
            if config["mostrar_indice"]
            else 1
        )

        total_paginas_con_inicio = (
            len(paginas)
            + paginas_offset
        )

        cantidad = sum(
            len(pagina["productos"])
            for pagina in paginas
        )

        return render_template_string(
            TEMPLATE_CATALOGO,
            paginas=paginas,
            cantidad=cantidad,
            total_paginas=len(paginas),
            total_paginas_con_inicio=total_paginas_con_inicio,
            paginas_offset=paginas_offset,
            logo_url=LOGO_URL,
            whatsapp=WHATSAPP,
            instagram=INSTAGRAM,
            footer_text=FOOTER_TEXT,
            portada_titulo=config["portada"],
            encabezado_titulo=config["encabezado"],
            titulo_documento=config["titulo"],
            mostrar_indice=config["mostrar_indice"],
            indice=indice,
            links_productos=config["links"],
        )

    except Exception as e:
        print(
            "ERROR CATALOGO:",
            repr(e)
        )

        return (
            "Error armando el catálogo: "
            + str(e),
            500,
        )


@app.route("/catalogo.pdf")
def catalogo_pdf():
    tipo = (
        request.args.get("tipo")
        or "completo"
    )

    return redirect(
        url_for(
            "catalogo",
            tipo=tipo,
        )
    )


# ============================================================
# WEBHOOKS
# ============================================================

@app.route(
    "/webhooks/store-redact",
    methods=["POST"],
)
def store_redact():
    AUTH["access_token"] = None
    AUTH["store_id"] = None

    return jsonify(
        {"ok": True}
    ), 200


@app.route(
    "/webhooks/customers-redact",
    methods=["POST"],
)
def customers_redact():
    return jsonify(
        {"ok": True}
    ), 200


@app.route(
    "/webhooks/customers-data-request",
    methods=["POST"],
)
def customers_data_request():
    return jsonify(
        {"ok": True}
    ), 200


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            10000,
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
