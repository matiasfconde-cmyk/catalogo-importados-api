import os
import io
import tempfile
import hashlib
import shutil

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


# ============================================================
# CONFIGURACIÓN
# ============================================================

app = Flask(__name__)

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

USER_AGENT = (
    "Catalogo Importados Agronomia "
    "(catalogo-importados-api.onrender.com)"
)

STORE_NAME = "IMPORTADOS AGRONOMÍA"
WHATSAPP = "11 6625-3738"
INSTAGRAM = "@importados.agronomia"

LOGO_URL = (
    "https://dcdn-us.mitiendanube.com/"
    "stores/006/777/697/themes/common/"
    "logo-4412898902984757469-1779314772-"
    "c30b03377e4c51f3c40cabda29a8000d"
    "1779314772-480-0.webp"
)

# Colores de Importados
AZUL = colors.HexColor("#153D7A")
ROJO = colors.HexColor("#D91F2A")
NEGRO = colors.HexColor("#161616")
GRIS = colors.HexColor("#666666")
GRIS_CLARO = colors.HexColor("#F5F6F8")
BORDE = colors.HexColor("#E0E3E8")
BLANCO = colors.white

# Categorías principales
ORDEN_CATEGORIAS = [
    "MUJER",
    "HOMBRE",
    "NIÑOS",
    "ENTREGA INMEDIATA",
]

# Token actual mientras la instancia esté encendida
AUTH = {
    "access_token": None,
    "store_id": None,
}


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def texto_localizado(valor):
    if isinstance(valor, dict):

        for idioma in ["es", "pt", "en"]:
            if valor.get(idioma):
                return str(valor[idioma])

        if valor:
            return str(next(iter(valor.values())))

        return ""

    return str(valor or "")


def formato_precio(valor):
    try:
        numero = float(valor)

        return (
            "$ {:,.0f}"
            .format(numero)
            .replace(",", ".")
        )

    except Exception:
        return str(valor or "")


def obtener_precio(producto):

    precios = []

    for variante in producto.get("variants", []):

        valor = variante.get("price")

        if valor in (None, ""):
            continue

        try:
            precios.append(float(valor))

        except Exception:
            continue

    if not precios:
        return "CONSULTAR"

    precios = sorted(set(precios))

    if len(precios) > 1:
        return "DESDE " + formato_precio(precios[0])

    return formato_precio(precios[0])


def headers_api():

    return {
        "Authorization": f"Bearer {AUTH['access_token']}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def url_api(recurso):

    return (
        f"https://api.tiendanube.com/"
        f"2025-03/"
        f"{AUTH['store_id']}/"
        f"{recurso.lstrip('/')}"
    )


# ============================================================
# LEER CATEGORÍAS
# ============================================================

def obtener_categorias():

    mapa = {}
    pagina = 1

    while True:

        r = requests.get(
            url_api("categories"),
            headers=headers_api(),
            params={
                "page": pagina,
                "per_page": 200,
            },
            timeout=30,
        )

        r.raise_for_status()

        datos = r.json()

        if not datos:
            break

        for categoria in datos:

            cid = categoria.get("id")

            nombre = texto_localizado(
                categoria.get("name")
            ).strip()

            if cid is not None and nombre:
                mapa[cid] = nombre

        if len(datos) < 200:
            break

        pagina += 1

    return mapa


# ============================================================
# LEER PRODUCTOS
# ============================================================

def obtener_productos():

    productos = []
    pagina = 1

    while True:

        r = requests.get(
            url_api("products"),
            headers=headers_api(),
            params={
                "page": pagina,
                "per_page": 200,
            },
            timeout=45,
        )

        r.raise_for_status()

        datos = r.json()

        if not datos:
            break

        productos.extend(datos)

        if len(datos) < 200:
            break

        pagina += 1

    return productos


# ============================================================
# CATEGORÍAS DEL PRODUCTO
# ============================================================

def nombres_categorias(producto, mapa):

    resultado = []

    for categoria in producto.get(
        "categories",
        []
    ):

        if isinstance(categoria, dict):

            nombre = texto_localizado(
                categoria.get("name")
            ).strip()

            if nombre:
                resultado.append(nombre)

        else:

            nombre = mapa.get(categoria)

            if nombre:
                resultado.append(nombre)

    return resultado


def categoria_principal(producto, mapa):

    categorias = nombres_categorias(
        producto,
        mapa
    )

    # Primero buscamos las categorías principales.
    for buscada in ORDEN_CATEGORIAS:

        for categoria in categorias:

            if categoria.upper().strip() == buscada:
                return buscada

    # Si no pertenece a una de las principales,
    # usamos la primera categoría existente.
    if categorias:
        return categorias[0].upper().strip()

    return "OTROS"


# ============================================================
# FOTO PRINCIPAL
# ============================================================

def obtener_foto_principal(producto):

    imagenes = producto.get(
        "images",
        []
    )

    if not imagenes:
        return None

    try:
        imagenes = sorted(
            imagenes,
            key=lambda x: x.get(
                "position",
                999
            )
        )

    except Exception:
        pass

    for imagen in imagenes:

        url = imagen.get("src")

        if url:
            return url

    return None


# ============================================================
# PREPARAR PRODUCTOS
# ============================================================

def preparar_productos():

    productos_raw = obtener_productos()
    categorias = obtener_categorias()

    productos = []

    for producto in productos_raw:

        nombre = texto_localizado(
            producto.get("name")
        ).strip()

        if not nombre:
            nombre = "PRODUCTO"

        productos.append({
            "id": producto.get("id"),
            "nombre": nombre.upper(),
            "precio": obtener_precio(producto),
            "categoria": categoria_principal(
                producto,
                categorias
            ),
            "foto": obtener_foto_principal(
                producto
            ),
        })

    return productos


# ============================================================
# IMÁGENES
# ============================================================

def descargar_y_reducir_imagen(
    url,
    carpeta,
    indice
):

    if not url:
        return None

    destino = os.path.join(
        carpeta,
        f"producto_{indice}.jpg"
    )

    try:

        with requests.get(
            url,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            },
            stream=True,
            timeout=15,
        ) as r:

            r.raise_for_status()

            # Limita el tamaño descargado.
            datos = bytearray()

            for bloque in r.iter_content(
                chunk_size=32768
            ):

                if not bloque:
                    continue

                datos.extend(bloque)

                # Protección adicional:
                # no necesitamos imágenes enormes.
                if len(datos) > 8 * 1024 * 1024:
                    break

        imagen = PILImage.open(
            io.BytesIO(datos)
        )

        imagen = ImageOps.exif_transpose(
            imagen
        )

        imagen = imagen.convert("RGB")

        # La foto final usada en el PDF será
        # de solo 500 x 500.
        imagen.thumbnail(
            (500, 500),
            PILImage.Resampling.LANCZOS
        )

        fondo = PILImage.new(
            "RGB",
            (500, 500),
            "white"
        )

        x = (
            500 - imagen.width
        ) // 2

        y = (
            500 - imagen.height
        ) // 2

        fondo.paste(
            imagen,
            (x, y)
        )

        fondo.save(
            destino,
            "JPEG",
            quality=72,
            optimize=True,
        )

        # Liberamos la imagen inmediatamente.
        imagen.close()
        fondo.close()

        return destino

    except Exception as e:

        print(
            "ERROR FOTO:",
            url,
            repr(e)
        )

        return None


# ============================================================
# LOGO
# ============================================================

def descargar_logo(carpeta):

    destino = os.path.join(
        carpeta,
        "logo.jpg"
    )

    try:

        r = requests.get(
            LOGO_URL,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            },
            timeout=15,
        )

        r.raise_for_status()

        imagen = PILImage.open(
            io.BytesIO(r.content)
        )

        imagen = ImageOps.exif_transpose(
            imagen
        )

        imagen = imagen.convert("RGB")

        imagen.thumbnail(
            (700, 250),
            PILImage.Resampling.LANCZOS
        )

        imagen.save(
            destino,
            "JPEG",
            quality=85,
            optimize=True,
        )

        imagen.close()

        return destino

    except Exception as e:

        print(
            "ERROR LOGO:",
            repr(e)
        )

        return None


# ============================================================
# ESTILOS
# ============================================================

ESTILO_NOMBRE = ParagraphStyle(
    "nombre",
    fontName="Helvetica-Bold",
    fontSize=9,
    leading=10.5,
    alignment=TA_CENTER,
    textColor=NEGRO,
)

ESTILO_PRECIO = ParagraphStyle(
    "precio",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=14,
    alignment=TA_CENTER,
    textColor=ROJO,
)

ESTILO_CATEGORIA = ParagraphStyle(
    "categoria",
    fontName="Helvetica-Bold",
    fontSize=15,
    leading=17,
    alignment=TA_CENTER,
    textColor=BLANCO,
)

ESTILO_SIN_FOTO = ParagraphStyle(
    "sinfoto",
    fontName="Helvetica-Bold",
    fontSize=8,
    alignment=TA_CENTER,
    textColor=GRIS,
)


# ============================================================
# TARJETA DE PRODUCTO
# ============================================================

def tarjeta_producto(
    producto,
    foto_path
):

    ancho = 8.45 * cm

    if foto_path:

        visual = Image(
            foto_path,
            width=5.45 * cm,
            height=5.45 * cm,
        )

    else:

        visual = Table(
            [[
                Paragraph(
                    "FOTO NO DISPONIBLE",
                    ESTILO_SIN_FOTO
                )
            ]],
            colWidths=[5.45 * cm],
            rowHeights=[5.45 * cm],
        )

        visual.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    GRIS_CLARO
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    BORDE
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER"
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE"
                ),
            ])
        )

    nombre = Paragraph(
        producto["nombre"],
        ESTILO_NOMBRE
    )

    precio = Paragraph(
        producto["precio"],
        ESTILO_PRECIO
    )

    tarjeta = Table(
        [
            [visual],
            [nombre],
            [precio],
        ],
        colWidths=[ancho],
        rowHeights=[
            5.65 * cm,
            0.78 * cm,
            0.60 * cm,
        ],
    )

    tarjeta.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                BLANCO
            ),
            (
                "BOX",
                (0, 0),
                (-1, -1),
                0.65,
                BORDE
            ),
            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "CENTER"
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                3
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                3
            ),
        ])
    )

    return tarjeta


# ============================================================
# BARRA DE CATEGORÍA
# ============================================================

def barra_categoria(nombre):

    barra = Table(
        [[
            Paragraph(
                nombre,
                ESTILO_CATEGORIA
            )
        ]],
        colWidths=[17.55 * cm],
        rowHeights=[0.72 * cm],
    )

    barra.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, -1),
                AZUL
            ),
            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "CENTER"
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                0
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                0
            ),
        ])
    )

    return barra


# ============================================================
# HEADER + FOOTER
# ============================================================

def dibujar_header_footer(
    canvas,
    doc,
    logo_path
):

    canvas.saveState()

    ancho, alto = A4

    # -------------------------
    # HEADER
    # -------------------------

    if (
        logo_path
        and os.path.exists(logo_path)
    ):

        try:

            canvas.drawImage(
                logo_path,
                1.2 * cm,
                alto - 1.58 * cm,
                width=4.2 * cm,
                height=1.05 * cm,
                preserveAspectRatio=True,
                anchor="sw",
            )

        except Exception:
            pass

    else:

        canvas.setFont(
            "Helvetica-Bold",
            11
        )

        canvas.setFillColor(AZUL)

        canvas.drawString(
            1.2 * cm,
            alto - 1.18 * cm,
            STORE_NAME
        )

    canvas.setFont(
        "Helvetica-Bold",
        8.5
    )

    canvas.setFillColor(GRIS)

    canvas.drawRightString(
        ancho - 1.2 * cm,
        alto - 1.12 * cm,
        "CATÁLOGO DE PRODUCTOS"
    )

    canvas.setStrokeColor(AZUL)
    canvas.setLineWidth(1.3)

    canvas.line(
        1.2 * cm,
        alto - 1.72 * cm,
        ancho - 1.2 * cm,
        alto - 1.72 * cm
    )

    canvas.setStrokeColor(ROJO)
    canvas.setLineWidth(2.5)

    canvas.line(
        1.2 * cm,
        alto - 1.72 * cm,
        4.0 * cm,
        alto - 1.72 * cm
    )

    # -------------------------
    # FOOTER
    # -------------------------

    canvas.setStrokeColor(BORDE)
    canvas.setLineWidth(0.5)

    canvas.line(
        1.2 * cm,
        1.25 * cm,
        ancho - 1.2 * cm,
        1.25 * cm
    )

    canvas.setFont(
        "Helvetica-Bold",
        8
    )

    canvas.setFillColor(AZUL)

    canvas.drawString(
        1.2 * cm,
        0.78 * cm,
        f"WhatsApp  {WHATSAPP}"
    )

    canvas.setFillColor(ROJO)

    canvas.drawCentredString(
        ancho / 2,
        0.78 * cm,
        INSTAGRAM
    )

    canvas.setFillColor(GRIS)

    canvas.drawRightString(
        ancho - 1.2 * cm,
        0.78 * cm,
        f"Página {doc.page}"
    )

    canvas.restoreState()


# ============================================================
# GENERAR PDF
# ============================================================

def generar_catalogo(productos):

    carpeta = tempfile.mkdtemp(
        prefix="catalogo_importados_"
    )

    pdf_path = os.path.join(
        carpeta,
        "Catalogo_Importados_Agronomia.pdf"
    )

    try:

        logo_path = descargar_logo(
            carpeta
        )

        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            leftMargin=1.2 * cm,
            rightMargin=1.2 * cm,
            topMargin=2.05 * cm,
            bottomMargin=1.45 * cm,
        )

        story = []

        # Agrupamos.
        agrupados = {}

        for producto in productos:

            categoria = producto[
                "categoria"
            ]

            agrupados.setdefault(
                categoria,
                []
            ).append(producto)

        # Orden principal.
        categorias = []

        for categoria in ORDEN_CATEGORIAS:

            if categoria in agrupados:
                categorias.append(categoria)

        # No perder productos.
        for categoria in sorted(
            agrupados.keys()
        ):

            if categoria not in categorias:
                categorias.append(categoria)

        primera = True
        contador_imagen = 0

        # IMPORTANTE:
        # las imágenes se descargan únicamente
        # cuando llega el momento de usarlas.
        for categoria in categorias:

            productos_categoria = sorted(
                agrupados[categoria],
                key=lambda p: p["nombre"]
            )

            # 6 productos por página.
            for inicio in range(
                0,
                len(productos_categoria),
                6
            ):

                if not primera:
                    story.append(
                        PageBreak()
                    )

                primera = False

                lote = productos_categoria[
                    inicio:inicio + 6
                ]

                story.append(
                    barra_categoria(
                        categoria
                    )
                )

                story.append(
                    Spacer(
                        1,
                        0.18 * cm
                    )
                )

                filas = []

                for i in range(
                    0,
                    len(lote),
                    2
                ):

                    producto_1 = lote[i]

                    contador_imagen += 1

                    foto_1 = (
                        descargar_y_reducir_imagen(
                            producto_1.get(
                                "foto"
                            ),
                            carpeta,
                            contador_imagen
                        )
                    )

                    tarjeta_1 = (
                        tarjeta_producto(
                            producto_1,
                            foto_1
                        )
                    )

                    tarjeta_2 = ""

                    if i + 1 < len(lote):

                        producto_2 = lote[
                            i + 1
                        ]

                        contador_imagen += 1

                        foto_2 = (
                            descargar_y_reducir_imagen(
                                producto_2.get(
                                    "foto"
                                ),
                                carpeta,
                                contador_imagen
                            )
                        )

                        tarjeta_2 = (
                            tarjeta_producto(
                                producto_2,
                                foto_2
                            )
                        )

                    filas.append([
                        tarjeta_1,
                        tarjeta_2
                    ])

                grilla = Table(
                    filas,
                    colWidths=[
                        8.65 * cm,
                        8.65 * cm,
                    ],
                    rowHeights=[
                        7.30 * cm
                        for _ in filas
                    ],
                )

                grilla.setStyle(
                    TableStyle([
                        (
                            "VALIGN",
                            (0, 0),
                            (-1, -1),
                            "TOP"
                        ),
                        (
                            "LEFTPADDING",
                            (0, 0),
                            (-1, -1),
                            3
                        ),
                        (
                            "RIGHTPADDING",
                            (0, 0),
                            (-1, -1),
                            3
                        ),
                        (
                            "TOPPADDING",
                            (0, 0),
                            (-1, -1),
                            3
                        ),
                        (
                            "BOTTOMPADDING",
                            (0, 0),
                            (-1, -1),
                            3
                        ),
                    ])
                )

                story.append(grilla)

        doc.build(
            story,
            onFirstPage=lambda c, d:
                dibujar_header_footer(
                    c,
                    d,
                    logo_path
                ),
            onLaterPages=lambda c, d:
                dibujar_header_footer(
                    c,
                    d,
                    logo_path
                ),
        )

        # Leemos solamente el PDF terminado.
        with open(
            pdf_path,
            "rb"
        ) as archivo:

            resultado = io.BytesIO(
                archivo.read()
            )

        resultado.seek(0)

        return resultado

    finally:

        # IMPORTANTE:
        # borramos todas las imágenes temporales.
        try:
            shutil.rmtree(
                carpeta,
                ignore_errors=True
            )
        except Exception:
            pass


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def home():

    return """
    <!doctype html>

    <html lang="es">

    <head>

        <meta charset="utf-8">

        <title>
            Importados Agronomía
        </title>

        <style>

            body {
                font-family:
                    Arial,
                    Helvetica,
                    sans-serif;

                background:
                    #f4f5f7;

                margin: 0;

                padding:
                    50px 20px;
            }

            .card {
                max-width:
                    680px;

                margin:
                    auto;

                background:
                    white;

                padding:
                    40px;

                border-radius:
                    20px;

                text-align:
                    center;

                box-shadow:
                    0 12px 35px
                    rgba(0,0,0,.08);
            }

            h1 {
                color:
                    #153D7A;
            }

            span {
                color:
                    #D91F2A;
            }

            p {
                color:
                    #666;

                line-height:
                    1.5;
            }

        </style>

    </head>

    <body>

        <div class="card">

            <h1>
                IMPORTADOS
                <span>
                    AGRONOMÍA
                </span>
            </h1>

            <p>
                Generador de catálogo
                conectado con Tiendanube.
            </p>

            <p>
                Para generar el catálogo
                actualizado, ingresá desde
                el Link de Instalación de
                CATALOGO IMPORTADOS.
            </p>

        </div>

    </body>

    </html>
    """


# ============================================================
# CALLBACK TIENDANUBE
# ============================================================

@app.route(
    "/tiendanube/callback"
)
def callback():

    code = request.args.get(
        "code"
    )

    if not code:

        return (
            "Falta el código de autorización.",
            400
        )

    try:

        r = requests.post(
            "https://www.tiendanube.com/"
            "apps/authorize/token",
            json={
                "client_id":
                    CLIENT_ID,

                "client_secret":
                    CLIENT_SECRET,

                "grant_type":
                    "authorization_code",

                "code":
                    code,
            },
            timeout=30,
        )

    except Exception as e:

        return (
            f"Error conectando Tiendanube: {e}",
            500
        )

    if r.status_code != 200:

        return (
            "Error de autorización: "
            + r.text,
            400
        )

    datos = r.json()

    AUTH["access_token"] = datos.get(
        "access_token"
    )

    AUTH["store_id"] = datos.get(
        "user_id"
    )

    if not AUTH["access_token"]:

        return (
            "Tiendanube no devolvió access_token.",
            400
        )

    if not AUTH["store_id"]:

        return (
            "Tiendanube no devolvió store_id.",
            400
        )

    try:

        productos = preparar_productos()

        cantidad = len(productos)

    except Exception as e:

        return (
            "La aplicación se conectó, "
            "pero no pudo leer los productos: "
            + str(e),
            500
        )

    return f"""
    <!doctype html>

    <html lang="es">

    <head>

        <meta charset="utf-8">

        <title>
            Catálogo Importados
        </title>

        <style>

            body {{
                font-family:
                    Arial,
                    Helvetica,
                    sans-serif;

                background:
                    #f4f5f7;

                margin: 0;

                padding:
                    45px 20px;
            }}

            .card {{
                max-width:
                    700px;

                margin:
                    auto;

                background:
                    white;

                padding:
                    42px;

                border-radius:
                    20px;

                text-align:
                    center;

                box-shadow:
                    0 12px 35px
                    rgba(0,0,0,.09);
            }}

            h1 {{
                color:
                    #153D7A;

                margin-bottom:
                    5px;
            }}

            .ok {{
                color:
                    #16834b;

                font-weight:
                    bold;
            }}

            .cantidad {{
                font-size:
                    48px;

                font-weight:
                    bold;

                color:
                    #D91F2A;

                margin:
                    20px 0 0;
            }}

            .btn {{
                display:
                    inline-block;

                margin-top:
                    25px;

                padding:
                    17px 30px;

                background:
                    #153D7A;

                color:
                    white;

                text-decoration:
                    none;

                font-weight:
                    bold;

                border-radius:
                    10px;
            }}

            .nota {{
                margin-top:
                    25px;

                font-size:
                    13px;

                color:
                    #777;
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

            <div class="cantidad">
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

            <p class="nota">
                El catálogo se genera con
                foto, nombre y precio actual
                de cada producto.
            </p>

        </div>

    </body>

    </html>
    """


# ============================================================
# PDF
# ============================================================

@app.route(
    "/catalogo.pdf"
)
def descargar_catalogo():

    if (
        not AUTH.get("access_token")
        or not AUTH.get("store_id")
    ):

        return """
        <h2>
            Primero autorizá
            CATALOGO IMPORTADOS
            desde Tiendanube.
        </h2>
        """, 401

    try:

        print(
            "LEYENDO PRODUCTOS..."
        )

        productos = preparar_productos()

        print(
            f"PRODUCTOS: {len(productos)}"
        )

        print(
            "GENERANDO PDF..."
        )

        pdf = generar_catalogo(
            productos
        )

        print(
            "PDF TERMINADO."
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
            "ERROR PDF:",
            repr(e)
        )

        return (
            "No se pudo generar el catálogo. "
            f"Error: {str(e)}",
            500
        )


# ============================================================
# WEBHOOKS
# ============================================================

@app.route(
    "/webhooks/store-redact",
    methods=["POST"]
)
def store_redact():

    AUTH["access_token"] = None
    AUTH["store_id"] = None

    return jsonify(
        {"ok": True}
    ), 200


@app.route(
    "/webhooks/customers-redact",
    methods=["POST"]
)
def customers_redact():

    return jsonify(
        {"ok": True}
    ), 200


@app.route(
    "/webhooks/customers-data-request",
    methods=["POST"]
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
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
