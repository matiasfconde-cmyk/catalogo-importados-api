import os
import io
import json
import time
import hashlib
import tempfile
import threading
from pathlib import Path

import requests
from flask import Flask, request, jsonify, send_file, Response

from PIL import Image as PILImage

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIGURACION TIENDANUBE
# ============================================================

CLIENT_ID = os.environ.get("TIENDANUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("TIENDANUBE_CLIENT_SECRET")

API_VERSION = "2025-03"

USER_AGENT = (
    "Catalogo Importados Agronomia "
    "(catalogo-importados-api.onrender.com)"
)


# ============================================================
# DATOS DE MARCA
# ============================================================

MARCA = "IMPORTADOS AGRONOMÍA"

WHATSAPP = "11 6625-3738"

INSTAGRAM = "@importados.agronomia"

LOGO_URL = (
    "https://dcdn-us.mitiendanube.com/"
    "stores/006/777/697/themes/common/"
    "logo-4412898902984757469-1779314772-"
    "c30b03377e4c51f3c40cabda29a8000d1779314772-480-0.webp"
)


# ============================================================
# COLORES
# ============================================================

AZUL = colors.HexColor("#153D7A")
ROJO = colors.HexColor("#D91F2A")
NEGRO = colors.HexColor("#161616")

GRIS_FONDO = colors.HexColor("#F7F8FA")
GRIS_BORDE = colors.HexColor("#E1E4E8")
GRIS_TEXTO = colors.HexColor("#62666D")

BLANCO = colors.white


# ============================================================
# ARCHIVOS TEMPORALES
# ============================================================

TMP = Path(tempfile.gettempdir())

IMG_DIR = TMP / "importados_catalogo_imgs"
IMG_DIR.mkdir(exist_ok=True)

PDF_PATH = TMP / "Catalogo_Importados_Agronomia.pdf"
JSON_PATH = TMP / "catalogo_importados.json"
LOGO_PATH = TMP / "logo_importados.jpg"
AUTH_PATH = TMP / "tiendanube_auth.json"


# ============================================================
# ESTADO DEL GENERADOR
# ============================================================

STATE = {
    "status": "idle",
    "progress": 0,
    "message": "Esperando autorización de Tiendanube.",
    "products": 0,
    "images_ok": 0,
    "images_error": 0,
    "pdf_ready": False,
    "error": None,
}

STATE_LOCK = threading.Lock()
BUILD_LOCK = threading.Lock()


def set_state(**kwargs):
    with STATE_LOCK:
        STATE.update(kwargs)


# ============================================================
# AUTH
# ============================================================

def guardar_auth(access_token, store_id):
    datos = {
        "access_token": access_token,
        "store_id": str(store_id),
    }

    AUTH_PATH.write_text(
        json.dumps(datos),
        encoding="utf-8"
    )


def cargar_auth():
    if not AUTH_PATH.exists():
        return None

    try:
        return json.loads(
            AUTH_PATH.read_text(encoding="utf-8")
        )
    except Exception:
        return None


# ============================================================
# HELPERS
# ============================================================

def nombre_localizado(valor):
    if isinstance(valor, dict):
        return (
            valor.get("es")
            or valor.get("pt")
            or valor.get("en")
            or next(iter(valor.values()), "")
        )

    return str(valor or "")


def precio_argentino(valor):
    try:
        numero = float(valor)

        return "$ {:,.0f}".format(
            numero
        ).replace(",", ".")

    except Exception:
        return "CONSULTAR"


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

    precios_unicos = sorted(set(precios))

    menor = min(precios_unicos)

    if len(precios_unicos) > 1:
        return "DESDE " + precio_argentino(menor)

    return precio_argentino(menor)


def headers_api(token):
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def api_url(store_id, endpoint):
    return (
        f"https://api.tiendanube.com/"
        f"{API_VERSION}/{store_id}/"
        f"{endpoint.lstrip('/')}"
    )


# ============================================================
# PRODUCTOS
# ============================================================

def descargar_productos(token, store_id):
    productos = []

    page = 1

    while True:
        respuesta = requests.get(
            api_url(store_id, "products"),
            headers=headers_api(token),
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


# ============================================================
# CATEGORIAS
# ============================================================

def descargar_categorias(token, store_id):
    categorias = {}

    page = 1

    while True:
        respuesta = requests.get(
            api_url(store_id, "categories"),
            headers=headers_api(token),
            params={
                "page": page,
                "per_page": 200,
            },
            timeout=40,
        )

        if respuesta.status_code != 200:
            break

        lote = respuesta.json()

        if not lote:
            break

        for categoria in lote:
            cid = categoria.get("id")

            parent = categoria.get("parent")

            if isinstance(parent, dict):
                parent = parent.get("id")

            categorias[cid] = {
                "name": nombre_localizado(
                    categoria.get("name")
                ).strip(),

                "parent": parent,
            }

        if len(lote) < 200:
            break

        page += 1

    return categorias


def raiz_categoria(cat_id, mapa):
    visitados = set()

    actual = cat_id

    nombre = None

    while actual and actual not in visitados:
        visitados.add(actual)

        datos = mapa.get(actual)

        if not datos:
            break

        nombre = datos.get("name") or nombre

        parent = datos.get("parent")

        if not parent:
            return nombre

        actual = parent

    return nombre


def categorias_producto(producto, mapa):
    nombres = []

    for categoria in producto.get("categories", []):
        if isinstance(categoria, dict):
            cid = categoria.get("id")

            if cid and mapa:
                raiz = raiz_categoria(cid, mapa)

                if raiz:
                    nombres.append(
                        raiz.upper().strip()
                    )
                    continue

            nombre = nombre_localizado(
                categoria.get("name")
            )

            if nombre:
                nombres.append(
                    nombre.upper().strip()
                )

        else:
            raiz = raiz_categoria(
                categoria,
                mapa
            )

            if raiz:
                nombres.append(
                    raiz.upper().strip()
                )

    return list(dict.fromkeys(nombres))


def categoria_principal(producto, mapa):
    categorias = categorias_producto(
        producto,
        mapa
    )

    # Entrega inmediata tiene prioridad.
    # Así los productos disponibles ya quedan
    # juntos en esa sección especial.

    if "ENTREGA INMEDIATA" in categorias:
        return "ENTREGA INMEDIATA"

    for categoria in [
        "MUJER",
        "HOMBRE",
        "NIÑOS",
    ]:
        if categoria in categorias:
            return categoria

    # Variantes habituales por si la categoría
    # está escrita de otra manera.

    for nombre in categorias:
        if "MUJER" in nombre:
            return "MUJER"

        if "HOMBRE" in nombre:
            return "HOMBRE"

        if (
            "NIÑO" in nombre
            or "NIÑOS" in nombre
            or "KIDS" in nombre
        ):
            return "NIÑOS"

        if (
            "ENTREGA" in nombre
            and "INMEDIATA" in nombre
        ):
            return "ENTREGA INMEDIATA"

    return "OTROS"


# ============================================================
# FOTO PRINCIPAL
# ============================================================

def foto_principal(producto):
    imagenes = producto.get("images
