import os
import io
import math
import hashlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, request, jsonify, send_file

from PIL import Image as PILImage, ImageOps

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
)
from reportlab.pdfbase.pdfmetrics import stringWidth


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

app = Flask(__name__)

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

API_VERSION = "2025-03"

USER_AGENT = (
    "Catalogo Importados Agronomia "
    "(https://catalogo-importados-api.onrender.com)"
)

STORE_NAME = "IMPORTADOS AGRONOMÍA"

WHATSAPP = "11 6625-3738"
INSTAGRAM = "@importados.agronomia"

# Logo que ya utiliza la tienda
LOGO_URL = (
    "https://dcdn-us.mitiendanube.com/stores/006/777/697/"
    "themes/common/"
    "logo-4412898902984757469-1779314772-"
    "c30b03377e4c51f3c40cabda29a8000d1779314772-480-0.webp"
)

# Colores de marca
AZUL = colors.HexColor("#153D7A")
ROJO = colors.HexColor("#D91F2A")
GRIS = colors.HexColor("#F3F4F6")
GRIS_BORDE = colors.HexColor("#E2E5E9")
GRIS_TEXTO = colors.HexColor("#55585E")
NEGRO = colors.HexColor("#151515")
BLANCO = colors.white

# Orden de las categorías principales
ORDEN_CATEGORIAS = [
    "MUJER",
    "HOMBRE",
    "NIÑOS",
    "ENTREGA INMEDIATA",
]

# Guarda temporalmente el acceso mientras Render esté encendido.
AUTH = {
    "access_token": None,
    "store_id": None,
}


# ============================================================
# HELPERS
# ============================================================

def nombre_localizado(valor):
    """
    Tiendanube puede devolver nombres como:
    {"es": "Producto"} o directamente como texto.
    """
    if isinstance(valor, dict):
        return (
            valor.get("es")
            or valor.get("pt")
            or valor.get("en")
            or next(iter(valor.values()), "")
        )

    return str(valor or "")


def formato_precio(numero):
    """
    $ 75.000
    """
    try:
        valor = float(numero)
        return "$ {:,.0f}".format(valor).replace(",", ".")
    except Exception:
        return str(numero or "")


def obtener_precio(producto):
    """
    Si todas las variantes tienen el mismo precio:
        $ 75.000

    Si existen distintos precios:
        DESDE $ 75.000
    """
    precios = []

    for variante in producto.get("variants", []):
        precio = variante.get("price")

        if precio in (None, ""):
            continue

        try:
            precios.append(float(precio))
        except Exception:
            pass

    if not precios:
        return "CONSULTAR"

    unicos = sorted(set(precios))
    menor = min(unicos)

    if len(unicos) > 1:
        return f"DESDE {formato_precio(menor)}"

    return formato_precio(menor)


def api_headers():
    return {
        "Authorization": f"Bearer {AUTH['access_token']}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def api_url(path):
    return (
        f"https://api.tiendanube.com/"
        f"{API_VERSION}/{AUTH['store_id']}/{path.lstrip('/')}"
    )


# ============================================================
# TIENDANUBE - CATEGORÍAS
# ============================================================

def obtener_categorias():
    """
    Descarga las categorías para poder traducir IDs a nombres.
    """
    categorias = {}
    page = 1

    while True:
        respuesta = requests.get(
            api_url("categories"),
            headers=api_headers(),
            params={
                "page": page,
                "per_page": 200,
            },
            timeout=30,
        )

        respuesta.raise_for_status()

        lote = respuesta.json()

        if not lote:
            break

        for cat in lote:
            categorias[cat.get("id")] = nombre_localizado(
                cat.get("name")
            ).strip()

        if len(lote) < 200:
            break

        page += 1

    return categorias


# ============================================================
# TIENDANUBE - PRODUCTOS
# ============================================================

def obtener_productos():
    """
    Trae TODOS los productos de la tienda usando paginación.
    """
    productos = []
    page = 1

    while True:
        respuesta = requests.get(
            api_url("products"),
            headers=api_headers(),
            params={
                "page": page,
                "per_page": 200,
            },
            timeout=60,
        )

        respuesta.raise_for_status()

        lote = respuesta.json()

        if not lote:
            break

        productos.extend(lote)

        if len(lote) < 200:
            break

        page += 1

    return productos


def categorias_producto(producto, mapa_categorias):
    """
    Soporta ambas formas de respuesta:
    - categorías como IDs
    - categorías como objetos
    """
    resultado = []

    for categoria in producto.get("categories", []):
        if isinstance(categoria, dict):
            nombre = nombre_localizado(
                categoria.get("name")
            ).strip()

            if nombre:
                resultado.append(nombre)

        else:
            nombre = mapa_categorias.get(categoria)

            if nombre:
                resultado.append(nombre)

    return resultado


def categoria_principal(producto, mapa_categorias):
    """
    Mantiene el criterio de tu Tiendanube:
    la primera categoría principal del producto.

    Si no puede resolverla, intenta encontrar una
    de las cuatro categorías principales.
    """
    categorias = categorias_producto(
        producto,
        mapa_categorias
    )

    if categorias:
        primera = categorias[0].upper().strip()

        if primera in ORDEN_CATEGORIAS:
            return primera

    for categoria in categorias:
        nombre = categoria.upper().strip()

        if nombre in ORDEN_CATEGORIAS:
            return nombre

    return "OTROS"


def preparar_productos():
    productos_raw = obtener_productos()
    mapa_categorias = obtener_categorias()

    resultado = []

    for producto in productos_raw:
        nombre = nombre_localizado(
            producto.get("name")
        ).strip()

        if not nombre:
            nombre = "PRODUCTO"

        imagenes = producto.get("images", [])

        # Ordenar según posición para tomar la foto principal.
        try:
            imagenes = sorted(
                imagenes,
                key=lambda x: x.get("position", 999)
            )
        except Exception:
            pass

        foto = None

        for imagen in imagenes:
            src = imagen.get("src")

            if src:
                foto = src
                break

        resultado.append({
            "id": producto.get("id"),
            "nombre": nombre.upper(),
            "precio": obtener_precio(producto),
            "foto": foto,
            "categoria": categoria_principal(
                producto,
                mapa_categorias
            ),
        })

    return resultado


# ============================================================
# DESCARGA Y PROCESAMIENTO DE IMÁGENES
# ============================================================

def ruta_cache_imagen(url):
    hash_url = hashlib.md5(
        url.encode("utf-8")
    ).hexdigest()

    return os.path.join(
        tempfile.gettempdir(),
        f"catalogo_{hash_url}.jpg"
    )


def preparar_imagen(url):
    """
    Descarga la foto y la convierte a JPG cuadrado,
    fondo blanco, sin deformarla.
    """

    if not url:
        return None

    destino = ruta_cache_imagen(url)

    if os.path.exists(destino):
        return destino

    try:
        respuesta = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
            timeout=20,
        )

        respuesta.raise_for_status()

        imagen = PILImage.open(
            io.BytesIO(respuesta.content)
        )

        imagen = imagen.convert("RGBA")

        # Caja cuadrada profesional para todas las fotos.
        canvas = PILImage.new(
            "RGBA",
            (800, 800),
            (255, 255, 255, 255),
        )

        imagen.thumbnail(
            (750, 750),
            PILImage.Resampling.LANCZOS,
        )

        x = (800 - imagen.width) // 2
        y = (800 - imagen.height) // 2

        canvas.alpha_composite(
            imagen,
            (x, y)
        )

        canvas = canvas.convert("RGB")

        canvas.save(
            destino,
            "JPEG",
            quality=84,
            optimize=True,
        )

        return destino

    except Exception as e:
        print(
            f"No se pudo descargar imagen {url}: {e}"
        )

        return None


def descargar_imagenes(productos):
    """
    Descarga varias fotos simultáneamente para no
    esperar una por una.
    """

    urls = list(
        {
            producto["foto"]
            for producto in productos
            if producto.get("foto")
        }
    )

    resultado = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futuros = {
            executor.submit(
                preparar_imagen,
                url
            ): url
            for url in urls
        }

        for futuro in as_completed(futuros):
            url = futuros[futuro]

            try:
                resultado[url] = futuro.result()
            except Exception:
                resultado[url] = None

    return resultado


# ============================================================
# LOGO
# ============================================================

def obtener_logo():
    try:
        respuesta = requests.get(
            LOGO_URL,
            headers={
                "User-Agent": "Mozilla/5.0",
            },
            timeout=20,
        )

        respuesta.raise_for_status()

        imagen = PILImage.open(
            io.BytesIO(respuesta.content)
        ).convert("RGBA")

        # Mantener transparencia sobre blanco.
        fondo = PILImage.new(
            "RGBA",
            imagen.size,
            (255, 255, 255, 255),
        )

        fondo.alpha_composite(imagen)

        ruta = os.path.join(
            tempfile.gettempdir(),
            "logo_importados_catalogo.png"
        )

        fondo.convert("RGB").save(
            ruta,
            "PNG"
        )

        return ruta

    except Exception as e:
        print(f"Error descargando logo: {e}")
        return None


# ============================================================
# DISEÑO DEL PDF
# ============================================================

ESTILO_NOMBRE = ParagraphStyle(
    "NombreProducto",
    fontName="Helvetica-Bold",
    fontSize=9.6,
    leading=11.3,
    textColor=NEGRO,
    alignment=TA_CENTER,
    spaceAfter=0,
)

ESTILO_PRECIO = ParagraphStyle(
    "PrecioProducto",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=15,
    textColor=ROJO,
    alignment=TA_CENTER,
    spaceBefore=0,
    spaceAfter=0,
)

ESTILO_CATEGORIA = ParagraphStyle(
    "Categoria",
    fontName="Helvetica-Bold",
    fontSize=15,
    leading=17,
    textColor=BLANCO,
    alignment=TA_CENTER,
)

ESTILO_PLACEHOLDER = ParagraphStyle(
    "SinFoto",
    fontName="Helvetica",
    fontSize=9,
    textColor=GRIS_TEXTO,
    alignment=TA_CENTER,
)


def tarjeta_producto(producto, mapa_imagenes):
    """
    Tarjeta fija con:
    FOTO
    NOMBRE
    PRECIO
    """

    ancho = 8.55 * cm

    ruta = None

    if producto.get("foto"):
        ruta = mapa_imagenes.get(
            producto["foto"]
        )

    if ruta and os.path.exists(ruta):
        foto = Image(
            ruta,
            width=5.65 * cm,
            height=5.65 * cm,
        )
    else:
        placeholder = Table(
            [[Paragraph(
                "IMAGEN NO DISPONIBLE",
                ESTILO_PLACEHOLDER
            )]],
            colWidths=[5.65 * cm],
            rowHeights=[5.65 * cm],
        )

        placeholder.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    colors.HexColor("#F7F7F7"),
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    GRIS_BORDE,
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER",
                ),
            ])
        )

        foto = placeholder

    nombre = Paragraph(
        producto["nombre"],
        ESTILO_NOMBRE,
    )

    precio = Paragraph(
        producto["precio"],
        ESTILO_PRECIO,
    )

    contenido = Table(
        [
            [foto],
            [nombre],
            [precio],
        ],
        colWidths=[ancho],
        rowHeights=[
            5.85 * cm,
            0.90 * cm,
            0.62 * cm,
        ],
    )

    contenido.setStyle(
        TableStyle([
            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "CENTER",
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                7,
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                7,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                4,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                4,
            ),
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                BLANCO,
            ),
            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.65,
                GRIS_BORDE,
            ),
        ])
    )

    return contenido


def encabezado_pie(canvas, doc, logo_path):
    """
    Encabezado y pie que aparece en TODAS las páginas.
    """

    canvas.saveState()

    ancho_pagina, alto_pagina = A4

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    if logo_path and os.path.exists(logo_path):
        try:
            imagen = PILImage.open(logo_path)

            iw, ih = imagen.size

            max_w = 4.7 * cm
            max_h = 1.25 * cm

            escala = min(
                max_w / iw,
                max_h / ih
            )

            w = iw * escala
            h = ih * escala

            canvas.drawImage(
                logo_path,
                1.2 * cm,
                alto_pagina - 1.65 * cm,
                width=w,
                height=h,
                preserveAspectRatio=True,
                mask="auto",
            )

        except Exception:
            canvas.setFont(
                "Helvetica-Bold",
                11
            )

            canvas.setFillColor(AZUL)

            canvas.drawString(
                1.25 * cm,
                alto_pagina - 1.2 * cm,
                STORE_NAME,
            )

    else:
        canvas.setFont(
            "Helvetica-Bold",
            11
        )

        canvas.setFillColor(AZUL)

        canvas.drawString(
            1.25 * cm,
            alto_pagina - 1.2 * cm,
            STORE_NAME,
        )

    # Texto derecho
    canvas.setFont(
        "Helvetica-Bold",
        8.5
    )

    canvas.setFillColor(GRIS_TEXTO)

    canvas.drawRightString(
        ancho_pagina - 1.25 * cm,
        alto_pagina - 1.05 * cm,
        "CATÁLOGO DE PRODUCTOS",
    )

    # Línea azul
    canvas.setStrokeColor(AZUL)
    canvas.setLineWidth(1.5)

    canvas.line(
        1.25 * cm,
        alto_pagina - 1.82 * cm,
        ancho_pagina - 1.25 * cm,
        alto_pagina - 1.82 * cm,
    )

    # Detalle rojo
    canvas.setStrokeColor(ROJO)
    canvas.setLineWidth(2.5)

    canvas.line(
        1.25 * cm,
        alto_pagina - 1.82 * cm,
        4.3 * cm,
        alto_pagina - 1.82 * cm,
    )

    # --------------------------------------------------------
    # FOOTER
    # --------------------------------------------------------

    canvas.setStrokeColor(
        GRIS_BORDE
    )

    canvas.setLineWidth(0.5)

    canvas.line(
        1.25 * cm,
        1.32 * cm,
        ancho_pagina - 1.25 * cm,
        1.32 * cm,
    )

    canvas.setFont(
        "Helvetica-Bold",
        8.2
    )

    canvas.setFillColor(AZUL)

    canvas.drawString(
        1.25 * cm,
        0.83 * cm,
        f"WhatsApp  {WHATSAPP}",
    )

    canvas.setFillColor(ROJO)

    canvas.drawCentredString(
        ancho_pagina / 2,
        0.83 * cm,
        INSTAGRAM,
    )

    canvas.setFillColor(
        GRIS_TEXTO
    )

    canvas.drawRightString(
        ancho_pagina - 1.25 * cm,
        0.83 * cm,
        f"Página {doc.page}",
    )

    canvas.restoreState()


def barra_categoria(nombre):
    barra = Table(
        [[Paragraph(
            nombre,
            ESTILO_CATEGORIA
        )]],
        colWidths=[17.7 * cm],
        rowHeights=[0.82 * cm],
    )

    barra.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                AZUL,
            ),
            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "CENTER",
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE",
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                0,
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                0,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                0,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                0,
            ),
        ])
    )

    return barra


def pagina_productos(
    productos,
    categoria,
    mapa_imagenes,
):
    """
    Arma una página de hasta 6 productos:
    2 columnas x 3 filas.
    """

    elementos = [
        barra_categoria(categoria),
        Spacer(1, 0.25 * cm),
    ]

    filas = []

    for i in range(0, len(productos), 2):
        izquierda = tarjeta_producto(
            productos[i],
            mapa_imagenes,
        )

        if i + 1 < len(productos):
            derecha = tarjeta_producto(
                productos[i + 1],
                mapa_imagenes,
            )
        else:
            derecha = ""

        filas.append([
            izquierda,
            derecha,
        ])

    grilla = Table(
        filas,
        colWidths=[
            8.72 * cm,
            8.72 * cm,
        ],
        rowHeights=[
            7.58 * cm
            for _ in filas
        ],
    )

    grilla.setStyle(
        TableStyle([
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP",
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                3,
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                3,
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                3,
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                3,
            ),
        ])
    )

    elementos.append(grilla)

    return elementos


def generar_pdf(productos):
    """
    Genera el catálogo profesional completo.
    """

    mapa_imagenes = descargar_imagenes(
        productos
    )

    logo_path = obtener_logo()

    salida = io.BytesIO()

    doc = SimpleDocTemplate(
        salida,
        pagesize=A4,

        # Sin portada. La primera página ya arranca
        # directamente con MUJER.
        leftMargin=1.15 * cm,
        rightMargin=1.15 * cm,

        # Espacio para logo arriba
        topMargin=2.10 * cm,

        # Espacio para WhatsApp/Instagram abajo
        bottomMargin=1.52 * cm,
    )

    story = []

    agrupados = {}

    for producto in productos:
        categoria = producto["categoria"]

        agrupados.setdefault(
            categoria,
            []
        ).append(producto)

    # Las cuatro secciones principales primero.
    categorias_finales = [
        c
        for c in ORDEN_CATEGORIAS
        if agrupados.get(c)
    ]

    # Por seguridad, cualquier producto raro o sin
    # categoría se agrega al final y no se pierde.
    extras = sorted(
        categoria
        for categoria in agrupados.keys()
        if categoria not in ORDEN_CATEGORIAS
    )

    categorias_finales.extend(extras)

    primera_pagina = True

    for categoria in categorias_finales:
        productos_categoria = agrupados[
            categoria
        ]

        # Orden alfabético dentro de cada categoría.
        productos_categoria = sorted(
            productos_categoria,
            key=lambda x: x["nombre"],
        )

        # 6 productos por hoja.
        for inicio in range(
            0,
            len(productos_categoria),
            6,
        ):
            if not primera_pagina:
                story.append(
                    PageBreak()
                )

            lote = productos_categoria[
                inicio:inicio + 6
            ]

            story.extend(
                pagina_productos(
                    lote,
                    categoria,
                    mapa_imagenes,
                )
            )

            primera_pagina = False

    doc.build(
        story,
        onFirstPage=lambda c, d: encabezado_pie(
            c,
            d,
            logo_path
        ),
        onLaterPages=lambda c, d: encabezado_pie(
            c,
            d,
            logo_path
        ),
    )

    salida.seek(0)

    return salida


# ============================================================
# WEB
# ============================================================

@app.route("/")
def home():
    return """
    <!doctype html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <title>Catálogo Importados Agronomía</title>

        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f4f5f7;
                margin: 0;
                padding: 50px 20px;
                color: #171717;
            }

            .card {
                max-width: 680px;
                margin: auto;
                background: white;
                border-radius: 18px;
                padding: 38px;
                box-shadow:
                    0 10px 30px rgba(0,0,0,.08);
                text-align: center;
            }

            h1 {
                color: #153D7A;
                margin-bottom: 8px;
            }

            .red {
                color: #D91F2A;
            }

            p {
                color: #555;
                line-height: 1.5;
            }

            .btn {
                display: inline-block;
                margin-top: 18px;
                padding: 15px 25px;
                border-radius: 10px;
                background: #153D7A;
                color: white;
                font-weight: bold;
                text-decoration: none;
            }
        </style>
    </head>

    <body>
        <div class="card">
            <h1>
                IMPORTADOS
                <span class="red">
                    AGRONOMÍA
                </span>
            </h1>

            <p>
                Generador de catálogo conectado
                a Tiendanube.
            </p>

            <p>
                Para actualizar el catálogo,
                autorizá nuevamente la aplicación
                desde Tiendanube Partners.
            </p>
        </div>
    </body>
    </html>
    """


@app.route("/tiendanube/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return (
            "No llegó el código de autorización.",
            400,
        )

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
            "Error conectando Tiendanube: "
            + respuesta.text,
            400,
        )

    datos = respuesta.json()

    AUTH["access_token"] = datos.get(
        "access_token"
    )

    AUTH["store_id"] = datos.get(
        "user_id"
    )

    if (
        not AUTH["access_token"]
        or not AUTH["store_id"]
    ):
        return (
            "Tiendanube no devolvió "
            "access_token/store_id.",
            400,
        )

    try:
        productos = preparar_productos()
        cantidad = len(productos)

    except Exception as e:
        return (
            "La aplicación se conectó, "
            "pero hubo un error leyendo "
            f"productos: {e}",
            500,
        )

    return f"""
    <!doctype html>

    <html lang="es">

    <head>
        <meta charset="utf-8">

        <title>
            Importados Agronomía
        </title>

        <style>
            body {{
                font-family:
                    Arial,
                    Helvetica,
                    sans-serif;

                background:
                    #f3f4f6;

                margin: 0;

                padding:
                    50px 20px;

                color:
                    #181818;
            }}

            .card {{
                max-width:
                    700px;

                margin:
                    auto;

                background:
                    #ffffff;

                border-radius:
                    20px;

                padding:
                    42px;

                text-align:
                    center;

                box-shadow:
                    0 12px 35px
                    rgba(0,0,0,.10);
            }}

            h1 {{
                color:
                    #153D7A;

                margin-bottom:
                    8px;
            }}

            .ok {{
                color:
                    #198754;

                font-weight:
                    bold;
            }}

            .number {{
                font-size:
                    45px;

                font-weight:
                    bold;

                color:
                    #D91F2A;

                margin:
                    15px 0 5px;
            }}

            p {{
                color:
                    #555;

                line-height:
                    1.55;
            }}

            .btn {{
                display:
                    inline-block;

                margin-top:
                    24px;

                padding:
                    17px 28px;

                border-radius:
                    11px;

                background:
                    #153D7A;

                color:
                    white;

                font-weight:
                    bold;

                text-decoration:
                    none;

                font-size:
                    16px;
            }}

            .small {{
                margin-top:
                    25px;

                font-size:
                    13px;

                color:
                    #888;
            }}
        </style>

    </head>

    <body>

        <div class="card">

            <h1>
                IMPORTADOS AGRONOMÍA
            </h1>

            <p class="ok">
                Tiendanube conectada correctamente ✓
            </p>

            <div class="number">
                {cantidad}
            </div>

            <p>
                productos encontrados
            </p>

            <a
                class="btn"
                href="/catalogo.pdf"
            >
                DESCARGAR CATÁLOGO PDF
            </a>

            <p class="small">
                El PDF se arma con la información
                actual de Tiendanube y puede tardar
                unos momentos porque descarga las
                fotos de los productos.
            </p>

        </div>

    </body>

    </html>
    """


@app.route("/catalogo.pdf")
def catalogo_pdf():

    if (
        not AUTH.get("access_token")
        or not AUTH.get("store_id")
    ):
        return """
        <h2>
            Primero tenés que autorizar
            CATALOGO IMPORTADOS desde
            Tiendanube.
        </h2>

        <p>
            Después volvé a presionar
            Descargar Catálogo PDF.
        </p>
        """, 401

    try:
        productos = preparar_productos()

        pdf = generar_pdf(
            productos
        )

        return send_file(
            pdf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=(
                "Catalogo_Importados_"
                "Agronomia.pdf"
            ),
        )

    except Exception as e:
        print(
            "ERROR GENERANDO PDF:",
            repr(e)
        )

        return (
            f"Error generando el PDF: {e}",
            500,
        )


# ============================================================
# WEBHOOKS DE PRIVACIDAD
# ============================================================

@app.route(
    "/webhooks/store-redact",
    methods=["POST"],
)
def store_redact():

    # Si la tienda elimina la app,
    # descartamos el acceso guardado.
    AUTH["access_token"] = None
    AUTH["store_id"] = None

    return jsonify({
        "ok": True
    }), 200


@app.route(
    "/webhooks/customers-redact",
    methods=["POST"],
)
def customers_redact():

    return jsonify({
        "ok": True
    }), 200


@app.route(
    "/webhooks/customers-data-request",
    methods=["POST"],
)
def customers_data_request():

    return jsonify({
        "ok": True
    }), 200


# ============================================================
# EJECUCIÓN LOCAL / RENDER
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
