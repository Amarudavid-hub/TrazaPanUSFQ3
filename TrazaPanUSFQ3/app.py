# -*- coding: utf-8 -*-
"""
TrazaPanUSFQ - trazabilidad interna de lotes para la panaderia.

Un solo programa: servidor web + base de datos SQLite (un archivo).
Se ejecuta en una PC de la panaderia y se usa desde el navegador de
cualquier celular, tablet o PC conectado a la misma red wifi.

    python app.py            -> arranca en el puerto 8000
    python app.py 8080       -> arranca en otro puerto

Estructura del archivo:
  1. Configuracion y base de datos
  2. Utilidades (texto, fechas, bitacora)
  3. Importacion del Excel de bodega principal
  4. Lotes: bodega secundaria y cocina
  5. Producciones y hoja de produccion
  3b. Registro de recepcion en bodega principal (reemplaza al Excel)
  6. Etiquetas (exportacion para Zebra)
  7. Consulta de trazabilidad
  8. Administracion
"""
import calendar
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import threading
import sys
import unicodedata
import uuid

import secrets
import time

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

# ----------------------------------------------------------------------------
# 1. Configuracion y base de datos
# ----------------------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
DATOS = os.environ.get("TRAZAPAN_DATOS", os.path.join(BASE, "datos"))
DB = os.path.join(DATOS, "trazabilidad.db")
FOTOS = os.path.join(DATOS, "fotos")
RESPALDOS = os.path.join(DATOS, "respaldos")
PUERTO = 8000

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
# Sesiones (contraseñas por área): la sesión dura 30 días en cada dispositivo.
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30))


def clave_secreta():
    """Clave para firmar las sesiones: variable TRAZAPAN_SECRETO o un archivo creado una sola vez en datos/."""
    if os.environ.get("TRAZAPAN_SECRETO"):
        return os.environ["TRAZAPAN_SECRETO"]
    os.makedirs(DATOS, exist_ok=True)
    ruta = os.path.join(DATOS, "secreto.txt")
    if not os.path.exists(ruta):
        with open(ruta, "w") as f:
            f.write(secrets.token_hex(32))
    with open(ruta) as f:
        return f.read().strip()


app.secret_key = clave_secreta()
AREAS_LOGIN = {"bodega": "Bodega principal", "produccion": "Producción", "cocina": "Cocina"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS personas(
  id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, rol TEXT DEFAULT '', activo INTEGER DEFAULT 1);

-- Catalogo de ingredientes tal como aparecen en la hoja de produccion.
-- tipo: 'lote' (viene de un proveedor), 'interno' (subreceta hecha en la panaderia), 'no_aplica' (agua)
CREATE TABLE IF NOT EXISTS ingredientes(
  id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'lote', refrigerado INTEGER NOT NULL DEFAULT 0);

-- Palabras con las que se reconoce un ingrediente en los nombres que usa bodega principal.
CREATE TABLE IF NOT EXISTS claves(
  id INTEGER PRIMARY KEY, ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id), clave TEXT UNIQUE NOT NULL);

-- Cada nombre de producto distinto que aparece en el Excel de bodega y a que ingrediente corresponde.
CREATE TABLE IF NOT EXISTS alias(
  id INTEGER PRIMARY KEY, clave TEXT UNIQUE NOT NULL, producto TEXT NOT NULL,
  ingrediente_id INTEGER REFERENCES ingredientes(id), ignorar INTEGER DEFAULT 0, auto INTEGER DEFAULT 0);

-- Un lote = un codigo de lote de un ingrediente. El codigo se guarda tal cual esta escrito.
CREATE TABLE IF NOT EXISTS lotes(
  id INTEGER PRIMARY KEY, ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id),
  lote TEXT NOT NULL, origen TEXT NOT NULL DEFAULT 'excel',   -- excel | manual | interno
  en_secundaria INTEGER DEFAULT 0, agotado INTEGER DEFAULT 0,
  revisar INTEGER DEFAULT 0, nota TEXT DEFAULT '', foto TEXT DEFAULT '',
  creado_en TEXT, creado_por TEXT DEFAULT '',
  UNIQUE(ingrediente_id, lote));

-- Cada fila del Excel de bodega principal (una recepcion). Es la copia fiel de lo importado.
CREATE TABLE IF NOT EXISTS recepciones(
  id INTEGER PRIMARY KEY, fila_hash TEXT NOT NULL, parte INTEGER NOT NULL DEFAULT 0,
  alias_id INTEGER NOT NULL REFERENCES alias(id), lote_texto TEXT NOT NULL, texto_original TEXT,
  fecha_ingreso TEXT, proveedor TEXT, cantidad TEXT, caducidad TEXT,
  revisar INTEGER DEFAULT 0, nota TEXT DEFAULT '',
  lote_id INTEGER REFERENCES lotes(id), importado_en TEXT,
  UNIQUE(fila_hash, parte));

-- Periodos en que un lote estuvo abierto (en uso) en cocina.
CREATE TABLE IF NOT EXISTS aperturas(
  id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL REFERENCES lotes(id),
  abierto_en TEXT NOT NULL, abierto_por TEXT, cerrado_en TEXT, cerrado_por TEXT);

CREATE TABLE IF NOT EXISTS productos(
  id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, codigo TEXT NOT NULL,
  vida_util_meses INTEGER DEFAULT 1,
  produce_ingrediente_id INTEGER REFERENCES ingredientes(id),   -- si es subreceta
  activo INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS receta(
  id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL REFERENCES productos(id),
  orden INTEGER NOT NULL, etiqueta TEXT NOT NULL,
  ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id));

CREATE TABLE IF NOT EXISTS producciones(
  id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL REFERENCES productos(id), producto TEXT,
  fecha TEXT NOT NULL, lote_final TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'en_curso',          -- en_curso | por_completar | completa | anulada
  iniciado_en TEXT, iniciado_por TEXT, terminado_en TEXT, terminado_por TEXT,
  completado_en TEXT, completado_por TEXT,
  unidades TEXT DEFAULT '', horneado TEXT DEFAULT '', observaciones TEXT DEFAULT '',
  n_etiquetas INTEGER DEFAULT 1);

-- Renglones de la hoja de produccion (copia de la receta al momento de iniciar).
CREATE TABLE IF NOT EXISTS produccion_lineas(
  id INTEGER PRIMARY KEY, produccion_id INTEGER NOT NULL REFERENCES producciones(id),
  orden INTEGER, etiqueta TEXT, ingrediente_id INTEGER REFERENCES ingredientes(id), tipo TEXT,
  cantidad TEXT DEFAULT '', no_utilizado INTEGER DEFAULT 0);

-- Lotes anotados en cada renglon. Nunca se borran: si se corrigen, quedan marcados como quitados.
CREATE TABLE IF NOT EXISTS produccion_lotes(
  id INTEGER PRIMARY KEY, linea_id INTEGER NOT NULL REFERENCES produccion_lineas(id),
  lote_id INTEGER NOT NULL REFERENCES lotes(id),
  origen TEXT, agregado_en TEXT, agregado_por TEXT,
  quitado_en TEXT, quitado_por TEXT, motivo TEXT DEFAULT '');

-- Bitacora: todo lo que se registra queda aqui con fecha, hora y persona.
CREATE TABLE IF NOT EXISTS movimientos(
  id INTEGER PRIMARY KEY, fecha_hora TEXT NOT NULL, persona TEXT, tipo TEXT NOT NULL,
  lote_id INTEGER, produccion_id INTEGER, detalle TEXT DEFAULT '');

CREATE INDEX IF NOT EXISTS ix_rec_lote ON recepciones(lote_id);
CREATE INDEX IF NOT EXISTS ix_ap_lote ON aperturas(lote_id);
CREATE INDEX IF NOT EXISTS ix_pl_linea ON produccion_lotes(linea_id);
CREATE INDEX IF NOT EXISTS ix_pl_lote ON produccion_lotes(lote_id);
CREATE INDEX IF NOT EXISTS ix_lin_prod ON produccion_lineas(produccion_id);
CREATE INDEX IF NOT EXISTS ix_prod_lote ON producciones(lote_final);
"""


class ErrorApi(Exception):
    def __init__(self, mensaje, codigo=400):
        super().__init__(mensaje)
        self.mensaje, self.codigo = mensaje, codigo


@app.errorhandler(ErrorApi)
def _error_api(e):
    return jsonify(error=e.mensaje), e.codigo


# ----------------------------------------------------------------------------
# Contraseñas por área. Si no hay ninguna configurada (uso en la PC de la panadería), no se pide contraseña.
# ----------------------------------------------------------------------------
LIBRES = {"/api/sesion", "/api/entrar", "/api/salir"}


def claves_guardadas(con):
    return con.execute("SELECT COUNT(*) FROM claves_area").fetchone()[0] > 0


def claves_pausadas(con):
    return con.execute("SELECT 1 FROM meta WHERE clave='claves_pausadas'").fetchone() is not None


def claves_activas(con):
    """Se pide contraseña si hay contraseñas guardadas y no están en pausa."""
    return claves_guardadas(con) and not claves_pausadas(con)


_ULTIMO_RESPALDO = [None]


@app.before_request
def _proteger():
    ruta = request.path
    # Copia de seguridad diaria tambien cuando el servidor pasa dias sin reiniciarse
    if _ULTIMO_RESPALDO[0] != dt.date.today():
        _ULTIMO_RESPALDO[0] = dt.date.today()
        try:
            respaldo_diario()
        except Exception:
            pass
    if not (ruta.startswith("/api/") or ruta.startswith("/fotos/")) or ruta in LIBRES:
        return None
    if claves_activas(db()) and session.get("area") not in AREAS_LOGIN:
        return jsonify(error="Escribe la contraseña del área para continuar.", login=True), 401
    return None


@app.get("/api/sesion")
def api_sesion():
    return jsonify(requiere=claves_activas(db()), area=session.get("area"))


@app.post("/api/entrar")
def api_entrar():
    d = request.get_json(silent=True) or {}
    area_ = d.get("area")
    con = db()
    fila = con.execute("SELECT hash FROM claves_area WHERE area=?", (area_,)).fetchone()
    if area_ not in AREAS_LOGIN or not fila or not check_password_hash(fila["hash"], str(d.get("clave") or "")):
        time.sleep(1)       # frena los intentos de adivinar
        raise ErrorApi("Contraseña incorrecta.", 403)
    session.clear()
    session.permanent = True
    session["area"] = area_
    return jsonify(ok=True, area=area_)


@app.post("/api/salir")
def api_salir():
    session.clear()
    return jsonify(ok=True)


@app.post("/api/claves")
def api_claves():
    """Producción cambia las contraseñas. La primera vez se ponen las tres juntas (activa el inicio de sesión)."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    activas = claves_guardadas(con)
    if claves_activas(con) and session.get("area") != "produccion":
        raise ErrorApi("Solo Producción puede cambiar las contraseñas.", 403)
    nuevas = {a: str(c or "") for a, c in (d.get("claves") or {}).items() if a in AREAS_LOGIN and str(c or "").strip()}
    if not activas and set(nuevas) != set(AREAS_LOGIN):
        raise ErrorApi("Escribe la contraseña de las tres áreas.")
    if not nuevas:
        raise ErrorApi("Escribe la nueva contraseña.")
    for a, c in nuevas.items():
        if len(c) < 4:
            raise ErrorApi("La contraseña de %s debe tener al menos 4 caracteres." % AREAS_LOGIN[a])
    for a, c in nuevas.items():
        con.execute("INSERT INTO claves_area(area,hash,cambiado_en) VALUES(?,?,?) ON CONFLICT(area) DO UPDATE SET hash=excluded.hash, cambiado_en=excluded.cambiado_en",
                    (a, generate_password_hash(c), ahora()))
        anotar(con, persona, "CLAVE_CAMBIADA", "Contraseña de %s" % AREAS_LOGIN[a])
    con.commit()
    if not activas:
        session.permanent = True
        session["area"] = "produccion"     # quien las activa sigue dentro
    return jsonify(ok=True)


@app.get("/api/claves")
def api_claves_estado():
    con = db()
    filas_ = {f["area"]: f["cambiado_en"] for f in con.execute("SELECT area, cambiado_en FROM claves_area")}
    return jsonify(activas=bool(filas_), pausadas=claves_pausadas(con),
                   areas=[{"area": a, "nombre": n, "cambiado_en": filas_.get(a)} for a, n in AREAS_LOGIN.items()])


@app.post("/api/claves/pausa")
def api_claves_pausa():
    """Pausar o volver a pedir las contraseñas (las contraseñas guardadas no se borran)."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    if not claves_guardadas(con):
        raise ErrorApi("Todavía no hay contraseñas.")
    if claves_activas(con) and session.get("area") != "produccion":
        raise ErrorApi("Solo Producción puede pausar las contraseñas.", 403)
    if d.get("pausar"):
        con.execute("INSERT OR REPLACE INTO meta(clave,valor) VALUES('claves_pausadas',?)", (ahora(),))
        anotar(con, persona, "CLAVES_PAUSADAS", "Se dejó de pedir contraseña")
    else:
        con.execute("DELETE FROM meta WHERE clave='claves_pausadas'")
        anotar(con, persona, "CLAVES_REANUDADAS", "Se vuelve a pedir contraseña")
        session.permanent = True
        session["area"] = "produccion"     # quien las reanuda sigue dentro
    con.commit()
    return jsonify(ok=True, pausadas=bool(d.get("pausar")))


@app.errorhandler(413)
def _muy_grande(e):
    return jsonify(error="El archivo es demasiado grande (máximo 25 MB)."), 413


def conectar():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def db():
    if "db" not in g:
        g.db = conectar()
    return g.db


@app.teardown_appcontext
def _cerrar(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def preparar_base():
    for carpeta in (DATOS, FOTOS, RESPALDOS):
        os.makedirs(carpeta, exist_ok=True)
    con = conectar()
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(ESQUEMA)
    migrar(con)
    if con.execute("SELECT COUNT(*) FROM ingredientes").fetchone()[0] == 0:
        cargar_semilla(con)
        # Si la base se creo sin ingredientes, se repiten los ajustes que los necesitan.
        con.execute("DELETE FROM meta WHERE clave IN ('v7_mezclas','v8_recipientes','v11_catalogo_ingredientes','v14_subrecetas')")
    sembrar_mezclas(con)
    aplicar_empaques(con)
    cierres_anteriores(con)
    reparar_inventario(con)
    # Subrecetas y Mix Bread no tienen etapa de masa.
    con.execute("UPDATE productos SET lleva_reposo=0 WHERE COALESCE(armado,0)=1 OR produce_ingrediente_id IS NOT NULL")
    # En un servidor en internet: la contraseña inicial de las tres áreas viene de TRAZAPAN_CLAVE_INICIAL.
    inicial = os.environ.get("TRAZAPAN_CLAVE_INICIAL", "").strip()
    if inicial and not con.execute("SELECT 1 FROM claves_area").fetchone():
        for a in AREAS_LOGIN:
            con.execute("INSERT INTO claves_area(area,hash,cambiado_en) VALUES(?,?,?)", (a, generate_password_hash(inicial), ahora()))
    con.commit()
    con.close()
    respaldo_diario()


# Columnas agregadas en la version 2 (registro de bodega en la app). Se agregan solas a una base existente.
COLUMNAS_RECEPCION = [
    ("unidad", "TEXT DEFAULT ''"), ("apariencia", "TEXT DEFAULT ''"), ("transporte", "TEXT DEFAULT ''"),
    ("aprobado_por", "TEXT DEFAULT ''"), ("observaciones", "TEXT DEFAULT ''"),
    ("rechazado", "INTEGER DEFAULT 0"), ("anulado", "INTEGER DEFAULT 0"), ("motivo_anulacion", "TEXT DEFAULT ''"),
    ("origen", "TEXT DEFAULT 'excel'"),            # excel = importado | app = registrado en la app
    ("registrado_por", "TEXT DEFAULT ''"), ("registrado_en", "TEXT DEFAULT ''"),
]


def migrar(con):
    # Version 25: fotos de la recepcion (etiqueta del lote y requisicion del proveedor)
    columnas_r = {f["name"] for f in con.execute("PRAGMA table_info(recepciones)")}
    for col in ("foto_lote", "foto_requisicion"):
        if col not in columnas_r:
            con.execute("ALTER TABLE recepciones ADD COLUMN %s TEXT" % col)
    # Version 24: empaque de cada producto de bodega (1 paquete = 50 kg). Todos registran en paquetes y la app convierte.
    columnas = {f["name"] for f in con.execute("PRAGMA table_info(alias)")}
    for col, tipo in (("empaque", "TEXT"), ("contenido", "REAL"), ("unidad_contenido", "TEXT")):
        if col not in columnas:
            con.execute("ALTER TABLE alias ADD COLUMN %s %s" % (col, tipo))
    # Version 23: despachos. Cada lote de pan cerrado queda en el congelador con sus paquetes (1 etiqueta = 1 paquete);
    # el encargado de produccion registra a que destino sale cada lote y cuantos paquetes.
    con.execute("""CREATE TABLE IF NOT EXISTS destinos(
        id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE, ruc TEXT DEFAULT '', contacto TEXT DEFAULT '',
        telefono TEXT DEFAULT '', correo TEXT DEFAULT '', direccion TEXT DEFAULT '', notas TEXT DEFAULT '',
        activo INTEGER DEFAULT 1, creado_en TEXT, creado_por TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS despachos(
        id INTEGER PRIMARY KEY, destino_id INTEGER NOT NULL REFERENCES destinos(id), fecha TEXT NOT NULL,
        fecha_hora TEXT NOT NULL, persona TEXT, observaciones TEXT DEFAULT '',
        anulado INTEGER DEFAULT 0, anulado_en TEXT, anulado_por TEXT, motivo_anulacion TEXT DEFAULT '')""")
    con.execute("""CREATE TABLE IF NOT EXISTS despacho_lineas(
        id INTEGER PRIMARY KEY, despacho_id INTEGER NOT NULL REFERENCES despachos(id),
        produccion_id INTEGER NOT NULL REFERENCES producciones(id), paquetes INTEGER NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS bajas_congelador(
        id INTEGER PRIMARY KEY, produccion_id INTEGER NOT NULL REFERENCES producciones(id), paquetes INTEGER NOT NULL,
        motivo TEXT NOT NULL, comentario TEXT DEFAULT '', fecha_hora TEXT NOT NULL, persona TEXT,
        anulado INTEGER DEFAULT 0, anulado_en TEXT, anulado_por TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_dl_prod ON despacho_lineas(produccion_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_dl_desp ON despacho_lineas(despacho_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_bc_prod ON bajas_congelador(produccion_id)")
    con.execute("INSERT OR IGNORE INTO destinos(nombre,notas,creado_en,creado_por) VALUES('AMBROSÍA','San Francisco FoodService',?,'App')", (ahora(),)) \
        if not con.execute("SELECT 1 FROM destinos").fetchone() else None
    # Version 20: inventarios mensuales. Bodega principal no registra lo que va a otras areas: los lotes que ya no
    # estan se cierran al hacer inventario (en bodega principal y en bodega secundaria).
    con.execute("""CREATE TABLE IF NOT EXISTS inventarios(
        id INTEGER PRIMARY KEY, lugar TEXT NOT NULL, fecha_hora TEXT NOT NULL, persona TEXT,
        revisados INTEGER DEFAULT 0, cerrados INTEGER DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS inventario_lotes(
        id INTEGER PRIMARY KEY, inventario_id INTEGER NOT NULL REFERENCES inventarios(id), lote_id INTEGER NOT NULL REFERENCES lotes(id),
        esta INTEGER NOT NULL, reabierto_en TEXT, reabierto_por TEXT)""")
    # tipo 'suelto': un solo lote dado de baja como «ya no está» fuera del inventario del mes (no cuenta como inventario hecho).
    if "tipo" not in {f["name"] for f in con.execute("PRAGMA table_info(inventarios)")}:
        con.execute("ALTER TABLE inventarios ADD COLUMN tipo TEXT DEFAULT ''")
        con.execute("ALTER TABLE inventarios ADD COLUMN nota TEXT DEFAULT ''")
    # Version 19: panes con reposo (la masa se hace un dia y se termina al siguiente).
    #   productos.lleva_reposo: muestra el boton "Masa lista"; receta.despues_reposo: renglon que se agrega al dia
    #   siguiente (por ejemplo el relleno); producciones.masa_lista_en: desde ahi la masa ya no recibe lotes nuevos.
    if "lleva_reposo" not in {f["name"] for f in con.execute("PRAGMA table_info(productos)")}:
        con.execute("ALTER TABLE productos ADD COLUMN lleva_reposo INTEGER DEFAULT 0")
    if "despues_reposo" not in {f["name"] for f in con.execute("PRAGMA table_info(receta)")}:
        con.execute("ALTER TABLE receta ADD COLUMN despues_reposo INTEGER DEFAULT 0")
    if "despues_reposo" not in {f["name"] for f in con.execute("PRAGMA table_info(produccion_lineas)")}:
        con.execute("ALTER TABLE produccion_lineas ADD COLUMN despues_reposo INTEGER DEFAULT 0")
    columnas_r = {f["name"] for f in con.execute("PRAGMA table_info(producciones)")}
    if "masa_lista_en" not in columnas_r:
        con.execute("ALTER TABLE producciones ADD COLUMN masa_lista_en TEXT")
        con.execute("ALTER TABLE producciones ADD COLUMN masa_lista_por TEXT")
    existentes = {f["name"] for f in con.execute("PRAGMA table_info(recepciones)")}
    for nombre, tipo in COLUMNAS_RECEPCION:
        if nombre not in existentes:
            con.execute("ALTER TABLE recepciones ADD COLUMN %s %s" % (nombre, tipo))
    con.execute("CREATE INDEX IF NOT EXISTS ix_rec_fecha ON recepciones(fecha_ingreso)")
    # Version 3: cantidades que pasan de bodega principal a bodega secundaria.
    con.execute("""CREATE TABLE IF NOT EXISTS traslados(
        id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL REFERENCES lotes(id), cantidad REAL NOT NULL,
        unidad TEXT DEFAULT '', fecha_hora TEXT NOT NULL, persona TEXT, anulado INTEGER DEFAULT 0)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta(clave TEXT PRIMARY KEY, valor TEXT)")
    # Version 10: lista fija de unidades de medida de bodega principal.
    con.execute("CREATE TABLE IF NOT EXISTS unidades(id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL)")
    # Version 18: foto de cada producto de bodega (la subida desde Administrar reemplaza a la incluida en la app).
    if "foto" not in {f["name"] for f in con.execute("PRAGMA table_info(alias)")}:
        con.execute("ALTER TABLE alias ADD COLUMN foto TEXT")
    # Version 17: contraseñas por área (se guardan cifradas).
    con.execute("CREATE TABLE IF NOT EXISTS claves_area(area TEXT PRIMARY KEY, hash TEXT NOT NULL, cambiado_en TEXT)")
    # Version 15: bajas por caducidad o contaminacion en bodega secundaria y cocina
    # (las de bodega principal se guardan como salida "BAJA" para que descuenten su saldo).
    con.execute("""CREATE TABLE IF NOT EXISTS bajas(
        id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL REFERENCES lotes(id), lugar TEXT NOT NULL, cantidad REAL,
        unidad TEXT DEFAULT '', motivo TEXT NOT NULL, comentario TEXT DEFAULT '', fecha_hora TEXT NOT NULL, persona TEXT,
        anulado INTEGER DEFAULT 0, motivo_anulacion TEXT DEFAULT '')""")
    # Version 14: subrecetas (relleno, glace, masa madre): vida util en dias, cantidad usada el dia y caducidad del lote.
    if "vida_util_dias" not in {f["name"] for f in con.execute("PRAGMA table_info(productos)")}:
        con.execute("ALTER TABLE productos ADD COLUMN vida_util_dias INTEGER")
    # Masa madre: no se elabora, se alimenta. Cada alimentacion anota la harina abierta en ese momento y
    # la masa madre guarda las alimentaciones de los ultimos vida_util_dias dias (30 = un mes).
    if "se_alimenta" not in {f["name"] for f in con.execute("PRAGMA table_info(productos)")}:
        con.execute("ALTER TABLE productos ADD COLUMN se_alimenta INTEGER DEFAULT 0")
        con.execute("UPDATE productos SET se_alimenta=1, vida_util_dias=? WHERE produce_ingrediente_id IS NOT NULL AND nombre LIKE 'MASA MADRE%%'",
                    (DIAS_MEMORIA_MASA_MADRE,))
    columnas_p = {f["name"] for f in con.execute("PRAGMA table_info(producciones)")}
    if "cantidad_usada" not in columnas_p:
        con.execute("ALTER TABLE producciones ADD COLUMN cantidad_usada TEXT DEFAULT ''")
    if "unidad_sub" not in columnas_p:
        con.execute("ALTER TABLE producciones ADD COLUMN unidad_sub TEXT DEFAULT ''")
    if "caducidad" not in {f["name"] for f in con.execute("PRAGMA table_info(lotes)")}:
        con.execute("ALTER TABLE lotes ADD COLUMN caducidad TEXT")
    # Version 13: lo que bodega principal despacha a otras áreas (y el cierre de un lote con motivo).
    con.execute("""CREATE TABLE IF NOT EXISTS salidas_areas(
        id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL REFERENCES lotes(id), cantidad REAL NOT NULL,
        unidad TEXT DEFAULT '', area TEXT NOT NULL, motivo TEXT DEFAULT '', fecha_hora TEXT NOT NULL, persona TEXT,
        anulado INTEGER DEFAULT 0, motivo_anulacion TEXT DEFAULT '')""")
    # Version 12: avisos para bodega principal y producción (por ejemplo, un lote anotado a mano).
    con.execute("""CREATE TABLE IF NOT EXISTS avisos(
        id INTEGER PRIMARY KEY, area TEXT NOT NULL, lote_id INTEGER REFERENCES lotes(id), texto TEXT NOT NULL,
        creado_en TEXT NOT NULL, creado_por TEXT, resuelto_en TEXT, resuelto_por TEXT, comentario TEXT DEFAULT '')""")
    if "tipo" not in {f["name"] for f in con.execute("PRAGMA table_info(avisos)")}:
        # manual_cocina: cocina anoto un lote que no estaba en bodega secundaria (bodega secundaria registra lo que entro)
        con.execute("ALTER TABLE avisos ADD COLUMN tipo TEXT DEFAULT ''")
        con.execute("UPDATE avisos SET tipo='manual_cocina' WHERE area='produccion' AND texto LIKE '%porque no aparecía en bodega secundaria%'")
    if "principal_vacio" not in {f["name"] for f in con.execute("PRAGMA table_info(lotes)")}:
        con.execute("ALTER TABLE lotes ADD COLUMN principal_vacio INTEGER DEFAULT 0")
        # Antes, todo lo que pasaba a secundaria salia de la lista de bodega principal.
        con.execute("UPDATE lotes SET principal_vacio=1 WHERE en_secundaria=1 OR agotado=1")
    # Version 4: cantidades que cocina toma de cada lote.
    con.execute("""CREATE TABLE IF NOT EXISTS salidas_cocina(
        id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL REFERENCES lotes(id), cantidad REAL NOT NULL,
        unidad TEXT DEFAULT '', fecha_hora TEXT NOT NULL, persona TEXT, anulado INTEGER DEFAULT 0)""")
    # Version 5: los refrigerados tambien pasan por bodega secundaria. Lo que cocina habia tomado directo
    # de bodega principal se registra como pasado por bodega secundaria (una sola vez).
    if not con.execute("SELECT 1 FROM meta WHERE clave='v5_refrigerados'").fetchone():
        con.execute("""INSERT INTO traslados(lote_id,cantidad,unidad,fecha_hora,persona)
            SELECT s.lote_id, SUM(s.cantidad), MAX(s.unidad), MIN(s.fecha_hora), 'Ajuste automático (versión 5)'
            FROM salidas_cocina s JOIN lotes l ON l.id=s.lote_id JOIN ingredientes i ON i.id=l.ingrediente_id
            WHERE i.refrigerado=1 AND s.anulado=0
              AND NOT EXISTS(SELECT 1 FROM traslados t WHERE t.lote_id=s.lote_id AND t.anulado=0)
            GROUP BY s.lote_id""")
        con.execute("INSERT INTO meta(clave,valor) VALUES('v5_refrigerados',?)", (ahora(),))
    # Version 7: Mix Bread (armados con lotes de pan ya producidos).
    if "armado" not in {f["name"] for f in con.execute("PRAGMA table_info(productos)")}:
        con.execute("ALTER TABLE productos ADD COLUMN armado INTEGER DEFAULT 0")
    columnas = {f["name"] for f in con.execute("PRAGMA table_info(producciones)")}
    if "unidades_lote" not in columnas:
        con.execute("ALTER TABLE producciones ADD COLUMN unidades_lote TEXT DEFAULT ''")
    if "vence" not in columnas:          # fecha de vencimiento fija (el Mix Bread toma la mas temprana de sus panes)
        con.execute("ALTER TABLE producciones ADD COLUMN vence TEXT DEFAULT ''")
    con.execute("""CREATE TABLE IF NOT EXISTS composicion(
        id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL REFERENCES productos(id), orden INTEGER,
        componente_id INTEGER NOT NULL REFERENCES productos(id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS armado_lotes(
        id INTEGER PRIMARY KEY, produccion_id INTEGER NOT NULL REFERENCES producciones(id),
        origen_id INTEGER NOT NULL REFERENCES producciones(id), unidades REAL NOT NULL)""")
    # Version 9: lista fija de productos de bodega principal.
    if "en_catalogo" not in {f["name"] for f in con.execute("PRAGMA table_info(alias)")}:
        con.execute("ALTER TABLE alias ADD COLUMN en_catalogo INTEGER DEFAULT 0")
    # Version 8: recipientes de cocina. recipientes = cuantos recipientes tiene el ingrediente en cocina
    # (0 = se usa directo de su propio empaque). Cada apertura queda en un recipiente.
    if "recipientes" not in {f["name"] for f in con.execute("PRAGMA table_info(ingredientes)")}:
        con.execute("ALTER TABLE ingredientes ADD COLUMN recipientes INTEGER DEFAULT 1")
    if "recipiente" not in {f["name"] for f in con.execute("PRAGMA table_info(aperturas)")}:
        con.execute("ALTER TABLE aperturas ADD COLUMN recipiente INTEGER DEFAULT 1")


# Mezclas iniciales de Mix Bread (provisionales: se confirman y se editan en Administrar -> Recetas).
MEZCLAS_INICIALES = [
    ("MIX BREAD", "BM", ["CIABATTA", "BAGUETTE / ROSA DE AGUA", "PAN DE VINO Y QUESO"]),
    ("MIX BREAD INTEGRAL", "BMI", ["CROISSANT INTEGRAL", "CIABATTA", "BAGUETTE / ROSA DE AGUA"]),
]


# Listado de materias primas con lote obligatorio (lista de productos de bodega principal), escritos tal cual.
CATALOGO_BODEGA = [
    "ACEITE VEGETAL BIDON (20LT)", "AJONJOLI BLANCO", "AZUCAR (KG)", "AZUCAR MICRO IMPALPABLE (KG)", "AZUCAR MORENA (KG)",
    "CANELA POLVO", "SEMILLA CHIA", "ESENCIA VAINILLA BLANCA BOTELLA (500ML)", "HARINA DE TRIGO QUINTAL (50KG)",
    "HARINA INTEGRAL QUINTAL (50KG)", "HUEVOS", "JAMON AMERICANO REBANADO PAQUETE (1KG)", "LECHE ENTERA FUNDA (1LT)",
    "LEVADURA SECA PAQUETE (500GR)", "LINAZA KG", "MANTECA VEGETAL MANTEPAN", "MANTEQUILLA S/SAL",
    "QUESO PARMESANO NACIONAL RALLADO KG", "SAL CRISAL", "VINO BLANCO COCINA CARTON (1LT)", "FUNDA VACIO GRANDE",
    "FUNDA VACIO MEDIANA",
]
# Ingrediente de panaderia de cada producto de la lista (por si en la base ya existia el producto sin asignar).
CATALOGO_INGREDIENTE = {
    "ACEITE VEGETAL BIDON (20LT)": "ACEITE VEGETAL", "AJONJOLI BLANCO": "AJONJOLI BLANCO", "AZUCAR (KG)": "AZUCAR BLANCO",
    "AZUCAR MICRO IMPALPABLE (KG)": "AZUCAR MICROPULVERIZADA", "AZUCAR MORENA (KG)": "AZUCAR MORENA", "CANELA POLVO": "CANELA EN POLVO",
    "SEMILLA CHIA": "CHIA", "ESENCIA VAINILLA BLANCA BOTELLA (500ML)": "VAINILLA", "HARINA DE TRIGO QUINTAL (50KG)": "HARINA DE TRIGO",
    "HARINA INTEGRAL QUINTAL (50KG)": "HARINA INTEGRAL", "HUEVOS": "HUEVOS", "LECHE ENTERA FUNDA (1LT)": "LECHE",
    "LEVADURA SECA PAQUETE (500GR)": "LEVADURA SECA", "LINAZA KG": "LINAZA", "MANTECA VEGETAL MANTEPAN": "MANTECA VEGETAL",
    "MANTEQUILLA S/SAL": "MANTEQUILLA", "QUESO PARMESANO NACIONAL RALLADO KG": "QUESO PARMESANO", "SAL CRISAL": "SAL",
    "VINO BLANCO COCINA CARTON (1LT)": "VINO BLANCO",
}
NO_PANADERIA = ["JAMON AMERICANO REBANADO PAQUETE (1KG)", "FUNDA VACIO GRANDE", "FUNDA VACIO MEDIANA"]


def ingrediente_propio(con, alias_id):
    """Un producto de bodega se llama igual en toda la app: si no tiene ingrediente, se crea uno con su mismo nombre."""
    alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not alias or alias["ingrediente_id"] or alias["ignorar"]:
        return
    con.execute("INSERT OR IGNORE INTO ingredientes(nombre,tipo,refrigerado) VALUES(?,?,0)", (alias["producto"], "lote"))
    ing = con.execute("SELECT id FROM ingredientes WHERE nombre=?", (alias["producto"],)).fetchone()
    con.execute("UPDATE alias SET ingrediente_id=?, auto=0 WHERE id=?", (ing["id"], alias_id))


def renombrar_ingrediente_unico(con, alias_id):
    """Si el ingrediente es solo de este producto, toma el nombre del producto (el mismo en bodega, cocina e inventario).
    La hoja de producción no cambia: sus renglones tienen su propio nombre (el de la receta)."""
    a = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not a or not a["ingrediente_id"] or a["ignorar"]:
        return
    otros = con.execute("SELECT COUNT(*) FROM alias WHERE ingrediente_id=? AND id<>? AND en_catalogo=1 AND ignorar=0",
                        (a["ingrediente_id"], alias_id)).fetchone()[0]
    ocupado = con.execute("SELECT 1 FROM ingredientes WHERE nombre=? AND id<>?", (a["producto"], a["ingrediente_id"])).fetchone()
    if not otros and not ocupado:
        con.execute("UPDATE ingredientes SET nombre=? WHERE id=? AND tipo='lote'", (a["producto"], a["ingrediente_id"]))


def agregar_al_catalogo(con, nombre):
    """Pone un producto en la lista de bodega principal, con el nombre escrito tal cual. Devuelve el id del alias."""
    alias_id, _ = alias_para(con, nombre)
    con.execute("UPDATE alias SET en_catalogo=1, producto=? WHERE id=?", (nombre, alias_id))
    return alias_id


def sembrar_mezclas(con):
    """Ajustes iniciales que necesitan los productos e ingredientes ya cargados (se hacen una sola vez)."""
    if not con.execute("SELECT 1 FROM meta WHERE clave='v19_reposo'").fetchone():
        # La ciabatta reposa toda la noche: se marca con reposo (los demas se marcan en Administrar → Recetas).
        con.execute("UPDATE productos SET lleva_reposo=1 WHERE nombre LIKE 'CIABATTA%'")
        con.execute("INSERT INTO meta(clave,valor) VALUES('v19_reposo',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v9_catalogo'").fetchone():
        for nombre in CATALOGO_BODEGA:
            alias_id = agregar_al_catalogo(con, nombre)
            if nombre in NO_PANADERIA:
                con.execute("UPDATE alias SET ignorar=1, ingrediente_id=NULL, auto=0 WHERE id=? AND ingrediente_id IS NULL", (alias_id,))
        con.execute("INSERT INTO meta(clave,valor) VALUES('v9_catalogo',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v11_catalogo_ingredientes'").fetchone():
        for producto, ingrediente in CATALOGO_INGREDIENTE.items():
            ing = con.execute("SELECT id FROM ingredientes WHERE nombre=?", (ingrediente,)).fetchone()
            if ing:
                con.execute("UPDATE alias SET ingrediente_id=?, ignorar=0 WHERE en_catalogo=1 AND producto=? AND ingrediente_id IS NULL",
                            (ing["id"], producto))
        materializar(con)
        con.execute("INSERT INTO meta(clave,valor) VALUES('v11_catalogo_ingredientes',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v14_subrecetas'").fetchone():
        con.execute("UPDATE productos SET vida_util_dias=? WHERE produce_ingrediente_id IS NOT NULL AND vida_util_dias IS NULL",
                    (DIAS_SUBRECETA,))
        # Lotes de subreceta ya creados: caducan a los dias de vida util desde que se hicieron.
        for l in con.execute("""SELECT l.id, substr(l.creado_en,1,10) AS dia, pr.vida_util_dias FROM lotes l
                                JOIN productos pr ON pr.produce_ingrediente_id=l.ingrediente_id
                                WHERE l.origen='interno' AND l.caducidad IS NULL""").fetchall():
            try:
                cad = (dt.date.fromisoformat(l["dia"]) + dt.timedelta(days=l["vida_util_dias"] or DIAS_SUBRECETA)).isoformat()
            except (TypeError, ValueError):
                continue
            con.execute("UPDATE lotes SET caducidad=? WHERE id=?", (cad, l["id"]))
        con.execute("INSERT INTO meta(clave,valor) VALUES('v14_subrecetas',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v16_subrecetas_en_uso'").fetchone():
        # Ya no existe "Guardé lo que sobró": una subreceta guardada vuelve a quedar en uso hasta que se termine.
        for l in con.execute("""SELECT l.id FROM lotes l WHERE l.origen='interno' AND l.agotado=0
                                AND NOT EXISTS(SELECT 1 FROM aperturas ap WHERE ap.lote_id=l.id AND ap.cerrado_en IS NULL)""").fetchall():
            con.execute("INSERT INTO aperturas(lote_id,abierto_en,abierto_por,recipiente) VALUES(?,?,?,1)", (l["id"], ahora(), "Sistema"))
        con.execute("INSERT INTO meta(clave,valor) VALUES('v16_subrecetas_en_uso',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v10_unidades'").fetchone():
        con.executemany("INSERT OR IGNORE INTO unidades(nombre) VALUES(?)", [(u,) for u in UNIDADES])
        con.execute("INSERT INTO meta(clave,valor) VALUES('v10_unidades',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v8_recipientes'").fetchone():
        con.execute("UPDATE ingredientes SET recipientes=2 WHERE nombre='HARINA DE TRIGO'")    # la harina de trigo tiene 2
        con.execute("INSERT INTO meta(clave,valor) VALUES('v8_recipientes',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v22_todos_reposo'").fetchone():
        # Version 22: todos los panes se hacen con la masa un dia y la coccion al siguiente.
        # Lo que se pone al terminar: relleno y glace (las subrecetas, menos la masa madre, que va en la masa).
        con.execute("UPDATE productos SET lleva_reposo=1 WHERE COALESCE(armado,0)=0 AND produce_ingrediente_id IS NULL")
        con.execute("""UPDATE receta SET despues_reposo=1 WHERE ingrediente_id IN (
                         SELECT id FROM ingredientes WHERE tipo='interno' AND nombre NOT LIKE '%MASA MADRE%')
                       AND producto_id IN (SELECT id FROM productos WHERE COALESCE(armado,0)=0 AND produce_ingrediente_id IS NULL)
                       AND producto_id NOT IN (SELECT DISTINCT producto_id FROM receta WHERE despues_reposo=1)""")
        con.execute("INSERT INTO meta(clave,valor) VALUES('v22_todos_reposo',?)", (ahora(),))
    if not con.execute("SELECT 1 FROM meta WHERE clave='v21_mismo_nombre'").fetchone():
        # Version 21: cada producto de bodega se llama igual en bodega principal, bodega secundaria, cocina e inventario.
        for a in con.execute("SELECT id FROM alias WHERE en_catalogo=1 AND ignorar=0").fetchall():
            ingrediente_propio(con, a["id"])
            renombrar_ingrediente_unico(con, a["id"])
        materializar(con)
        con.execute("INSERT INTO meta(clave,valor) VALUES('v21_mismo_nombre',?)", (ahora(),))
    if con.execute("SELECT 1 FROM meta WHERE clave='v7_mezclas'").fetchone():
        return
    for nombre, codigo, panes in MEZCLAS_INICIALES:
        fila = con.execute("SELECT id FROM productos WHERE nombre=?", (nombre,)).fetchone()
        if fila:
            pid = fila["id"]
            con.execute("UPDATE productos SET armado=1 WHERE id=?", (pid,))
        else:
            pid = con.execute("INSERT INTO productos(nombre,codigo,vida_util_meses,armado) VALUES(?,?,1,1)",
                              (nombre, codigo)).lastrowid
        con.execute("DELETE FROM receta WHERE producto_id=?", (pid,))
        if not con.execute("SELECT 1 FROM composicion WHERE producto_id=?", (pid,)).fetchone():
            for orden, pan in enumerate(panes, 1):
                comp = con.execute("SELECT id FROM productos WHERE nombre=?", (pan,)).fetchone()
                if comp:
                    con.execute("INSERT INTO composicion(producto_id,orden,componente_id) VALUES(?,?,?)", (pid, orden, comp["id"]))
    con.execute("INSERT INTO meta(clave,valor) VALUES('v7_mezclas',?)", (ahora(),))



# Copia de semilla.json dentro del programa (por si el archivo no se copio junto con app.py).
SEMILLA_INTERNA = r'''
{
 "_nota": "Datos iniciales tomados de las hojas de produccion feb-oct 2026. Se cargan una sola vez, cuando la base esta vacia. Despues todo se edita desde la pantalla Administrar.",
 "ingredientes": [
  {
   "nombre": "HARINA DE TRIGO",
   "claves": [
    "HARINA DE TRIGO"
   ]
  },
  {
   "nombre": "HARINA INTEGRAL",
   "claves": [
    "HARINA INTEGRAL"
   ]
  },
  {
   "nombre": "SAL",
   "claves": [
    "SAL"
   ]
  },
  {
   "nombre": "AZUCAR BLANCO",
   "claves": [
    "AZUCAR",
    "AZUCAR BLANCO",
    "AZUCAR BLANCA"
   ]
  },
  {
   "nombre": "AZUCAR MORENA",
   "claves": [
    "AZUCAR MORENA",
    "AZUCAR MORENO"
   ]
  },
  {
   "nombre": "AZUCAR MICROPULVERIZADA",
   "claves": [
    "AZUCAR MICRO",
    "AZUCAR MICROPULVERIZADA",
    "AZUCAR IMPALPABLE"
   ]
  },
  {
   "nombre": "LEVADURA SECA",
   "claves": [
    "LEVADURA"
   ]
  },
  {
   "nombre": "MANTECA VEGETAL",
   "claves": [
    "MANTECA VEGETAL",
    "MANTECA"
   ]
  },
  {
   "nombre": "ACEITE VEGETAL",
   "claves": [
    "ACEITE VEGETAL"
   ]
  },
  {
   "nombre": "MANTEQUILLA",
   "refrigerado": true,
   "claves": [
    "MANTEQUILLA"
   ]
  },
  {
   "nombre": "HUEVOS",
   "refrigerado": true,
   "claves": [
    "HUEVOS",
    "HUEVO"
   ]
  },
  {
   "nombre": "LECHE",
   "refrigerado": true,
   "claves": [
    "LECHE"
   ]
  },
  {
   "nombre": "QUESO PARMESANO",
   "refrigerado": true,
   "claves": [
    "QUESO PARMESANO"
   ]
  },
  {
   "nombre": "VINO BLANCO",
   "claves": [
    "VINO BLANCO"
   ]
  },
  {
   "nombre": "AJONJOLI BLANCO",
   "claves": [
    "AJONJOLI BLANCO"
   ]
  },
  {
   "nombre": "CHIA",
   "claves": [
    "CHIA",
    "SEMILLA CHIA",
    "SEMILLA DE CHIA"
   ]
  },
  {
   "nombre": "LINAZA",
   "claves": [
    "LINAZA"
   ]
  },
  {
   "nombre": "CANELA EN POLVO",
   "claves": [
    "CANELA"
   ]
  },
  {
   "nombre": "VAINILLA",
   "claves": [
    "VAINILLA",
    "ESENCIA VAINILLA",
    "ESENCIA DE VAINILLA"
   ]
  },
  {
   "nombre": "AGUA",
   "tipo": "no_aplica"
  },
  {
   "nombre": "MASA MADRE",
   "tipo": "interno"
  },
  {
   "nombre": "RELLENO CANELA",
   "tipo": "interno"
  },
  {
   "nombre": "GLACE CANELA",
   "tipo": "interno"
  }
 ],
 "productos": [
  {
   "nombre": "BAGUETTE / ROSA DE AGUA",
   "codigo": "BA",
   "lineas": [
    "HARINA DE TRIGO",
    "SAL",
    "AZUCAR BLANCO",
    "LEVADURA SECA",
    "MANTECA VEGETAL",
    "AGUA"
   ]
  },
  {
   "nombre": "CIABATTA",
   "codigo": "CI",
   "lineas": [
    "HARINA DE TRIGO",
    "SAL",
    "LEVADURA SECA",
    "ACEITE VEGETAL",
    "AGUA"
   ]
  },
  {
   "nombre": "CROISSANT",
   "codigo": "CR",
   "lineas": [
    "HARINA DE TRIGO",
    "SAL",
    "AZUCAR BLANCO",
    "LEVADURA SECA",
    "MANTEQUILLA",
    "AGUA",
    {
     "etiqueta": "MANTEQUILLA PARA EMPASTE",
     "ingrediente": "MANTEQUILLA"
    }
   ]
  },
  {
   "nombre": "CROISSANT INTEGRAL",
   "codigo": "CRI",
   "lineas": [
    "HARINA DE TRIGO",
    "HARINA INTEGRAL",
    "SAL",
    "AZUCAR BLANCO",
    "LEVADURA SECA",
    "MANTEQUILLA",
    "AGUA",
    {
     "etiqueta": "MANTEQUILLA PARA EMPASTE",
     "ingrediente": "MANTEQUILLA"
    },
    "AJONJOLI BLANCO",
    "CHIA",
    "LINAZA"
   ]
  },
  {
   "nombre": "PAN DE VINO Y QUESO",
   "codigo": "VQ",
   "lineas": [
    "HARINA DE TRIGO",
    "AGUA",
    "VINO BLANCO",
    "MASA MADRE",
    "QUESO PARMESANO",
    "SAL"
   ]
  },
  {
   "nombre": "ROLLO DE CANELA",
   "codigo": "RC",
   "lineas": [
    "HARINA DE TRIGO",
    "SAL",
    "AZUCAR BLANCO",
    "LEVADURA SECA",
    "MANTEQUILLA",
    "HUEVOS",
    "AGUA",
    "RELLENO CANELA",
    "GLACE CANELA"
   ]
  },
  {
   "nombre": "MIX BREAD",
   "codigo": "BM",
   "lineas": []
  },
  {
   "nombre": "MASA MADRE",
   "codigo": "MM",
   "produce": "MASA MADRE",
   "lineas": [
    "HARINA DE TRIGO",
    "AGUA"
   ]
  },
  {
   "nombre": "RELLENO CANELA",
   "codigo": "RE",
   "produce": "RELLENO CANELA",
   "lineas": [
    "AZUCAR MORENA",
    "CANELA EN POLVO",
    "MANTEQUILLA"
   ]
  },
  {
   "nombre": "GLACE CANELA",
   "codigo": "GL",
   "produce": "GLACE CANELA",
   "lineas": [
    "AZUCAR MICROPULVERIZADA",
    "VAINILLA",
    "LECHE"
   ]
  }
 ]
}
'''


def cargar_semilla(con):
    """Ingredientes, productos y recetas iniciales: de semilla.json si existe, si no, la copia interna."""
    ruta = os.path.join(BASE, "semilla.json")
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            semilla = json.load(f)
    else:
        semilla = json.loads(SEMILLA_INTERNA)
    ids = {}
    for ing in semilla.get("ingredientes", []):
        cur = con.execute("INSERT INTO ingredientes(nombre,tipo,refrigerado) VALUES(?,?,?)",
                          (ing["nombre"], ing.get("tipo", "lote"), 1 if ing.get("refrigerado") else 0))
        ids[ing["nombre"]] = cur.lastrowid
        for clave in ing.get("claves", []):
            con.execute("INSERT OR IGNORE INTO claves(ingrediente_id,clave) VALUES(?,?)",
                        (cur.lastrowid, normalizar(clave)))
    for p in semilla.get("productos", []):
        if con.execute("SELECT 1 FROM productos WHERE nombre=?", (p["nombre"],)).fetchone():
            continue
        cur = con.execute("INSERT INTO productos(nombre,codigo,vida_util_meses,produce_ingrediente_id) VALUES(?,?,?,?)",
                          (p["nombre"], p["codigo"], p.get("vida_util_meses", 1), ids.get(p.get("produce"))))
        for orden, linea in enumerate(p.get("lineas", []), 1):
            if isinstance(linea, str):
                linea = {"etiqueta": linea, "ingrediente": linea}
            con.execute("INSERT INTO receta(producto_id,orden,etiqueta,ingrediente_id) VALUES(?,?,?,?)",
                        (cur.lastrowid, orden, linea["etiqueta"], ids[linea["ingrediente"]]))


def respaldo_diario():
    """Copia de seguridad: una por dia, se conservan las ultimas 30."""
    if not os.path.exists(DB):
        return
    destino = os.path.join(RESPALDOS, "trazabilidad_%s.db" % dt.date.today().strftime("%Y%m%d"))
    if not os.path.exists(destino):
        origen = sqlite3.connect(DB)
        copia = sqlite3.connect(destino)
        origen.backup(copia)
        copia.close()
        origen.close()
    viejos = sorted(f for f in os.listdir(RESPALDOS) if f.startswith("trazabilidad_"))[:-30]
    for f in viejos:
        os.remove(os.path.join(RESPALDOS, f))


# ----------------------------------------------------------------------------
# 2. Utilidades
# ----------------------------------------------------------------------------
# Hora de Ecuador aunque la app corra en un equipo o servidor con otra zona horaria (por ejemplo, en la nube).
if hasattr(time, "tzset"):
    os.environ["TZ"] = os.environ.get("TRAZAPAN_TZ", "America/Guayaquil")
    time.tzset()


def ahora():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalizar(texto):
    """'Aceite vegetal bidón (20LT)' -> 'ACEITE VEGETAL BIDON 20 LT'. Solo para comparar nombres, nunca lotes."""
    t = unicodedata.normalize("NFD", str(texto or "").upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    t = re.sub(r"(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def sumar_meses(fecha, meses):
    anio = fecha.year + (fecha.month - 1 + meses) // 12
    mes = (fecha.month - 1 + meses) % 12 + 1
    return dt.date(anio, mes, min(fecha.day, calendar.monthrange(anio, mes)[1]))


def cuerpo():
    return request.get_json(silent=True) or {}


def persona_de(datos):
    """Quien registra: el area del dispositivo (Bodega principal, Producción o Cocina)."""
    nombre = str(datos.get("persona") or "").strip()
    return nombre or AREAS_LOGIN.get(session.get("area"), "Sin área")


def anotar(con, persona, tipo, detalle="", lote_id=None, produccion_id=None):
    con.execute("INSERT INTO movimientos(fecha_hora,persona,tipo,lote_id,produccion_id,detalle) VALUES(?,?,?,?,?,?)",
                (ahora(), persona, tipo, lote_id, produccion_id, detalle))


def numero(texto):
    """'2' -> 2.0, '79,5' -> 79.5. Devuelve None si no es un numero simple (por ejemplo '79,50 KG')."""
    t = str(texto or "").strip().replace(",", ".")
    return float(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


def fmt(x):
    return ("%.3f" % x).rstrip("0").rstrip(".")


def filas(cursor):
    return [dict(f) for f in cursor.fetchall()]


# ----------------------------------------------------------------------------
# 3. Importacion del Excel de bodega principal
# ----------------------------------------------------------------------------
SEPARADOR_LOTES = re.compile(r"\s+-\s+|\s+/\s*|\s*/\s+|\s+&\s+|\s+[Yy]\s+")


def texto_celda(valor):
    """Convierte el valor de una celda a texto. Devuelve (texto, aviso)."""
    if valor is None:
        return "", ""
    if isinstance(valor, bool):
        return str(valor), ""
    if isinstance(valor, (dt.datetime, dt.date)):
        return valor.strftime("%d/%m/%Y"), "Excel guardó este lote como fecha; confirmar el código real en el empaque."
    if isinstance(valor, float):
        return (str(int(valor)) if valor == int(valor) else repr(valor)), ""
    return str(valor).strip(), ""


def fecha_celda(valor):
    if isinstance(valor, dt.datetime):
        return valor.date().isoformat()
    if isinstance(valor, dt.date):
        return valor.isoformat()
    t = str(valor or "").strip()
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(t, formato).date().isoformat()
        except ValueError:
            pass
    return t


def ubicar_tabla(libro):
    """Busca la hoja y la fila de encabezados que tengan PRODUCTO y LOTE."""
    for hoja in libro.worksheets:
        for n_fila, fila in enumerate(hoja.iter_rows(min_row=1, max_row=15, values_only=True), 1):
            nombres = [normalizar(c) for c in fila]
            col = {}
            for i, nombre in enumerate(nombres):
                if not nombre:
                    continue
                if "LOTE" in nombre and "lote" not in col:
                    col["lote"] = i
                elif "APARIENCIA" in nombre:
                    col.setdefault("apariencia", i)
                elif "TRANSPORTE" in nombre or "CONDICIONES" in nombre:
                    col.setdefault("transporte", i)
                elif "APROBADO" in nombre:
                    col.setdefault("aprobado_por", i)
                elif "OBSERVACION" in nombre:
                    col.setdefault("observaciones", i)
                elif "UNIDAD" in nombre:
                    col.setdefault("unidad", i)
                elif any(p in nombre for p in ("PRODUCTO", "INGREDIENTE", "DESCRIPCION")) and "producto" not in col:
                    col["producto"] = i
                elif any(p in nombre for p in ("PROVEEDOR", "EMPRESA")) and "proveedor" not in col:
                    col["proveedor"] = i
                elif any(p in nombre for p in ("CADUC", "VENC", "EXPIR")) and "caducidad" not in col:
                    col["caducidad"] = i
                elif "CANTIDAD" in nombre and "cantidad" not in col:
                    col["cantidad"] = i
                elif "FECHA" in nombre and "fecha" not in col:
                    col["fecha"] = i
            if "lote" in col and "producto" in col:
                return hoja, n_fila, col
    raise ErrorApi("No encontré en el Excel una hoja con columnas PRODUCTO y LOTE.")


def alias_para(con, producto):
    """Devuelve el id del alias de un nombre de producto de bodega; lo crea si es nuevo."""
    clave = normalizar(producto)
    fila = con.execute("SELECT id FROM alias WHERE clave=?", (clave,)).fetchone()
    if fila:
        return fila["id"], False
    sugerido = sugerir_ingrediente(con, clave)
    cur = con.execute("INSERT INTO alias(clave,producto,ingrediente_id,auto) VALUES(?,?,?,?)",
                      (clave, " ".join(str(producto).split()), sugerido, 1 if sugerido else 0))
    return cur.lastrowid, True


def sugerir_ingrediente(con, clave):
    """Asignacion automatica: la palabra clave mas larga que coincida con el inicio del nombre."""
    mejor = None
    for c in con.execute("SELECT clave, ingrediente_id FROM claves"):
        if clave == c["clave"] or clave.startswith(c["clave"] + " "):
            if mejor is None or len(c["clave"]) > len(mejor["clave"]):
                mejor = c
    return mejor["ingrediente_id"] if mejor else None


def materializar(con):
    """Crea (o enlaza) los lotes de las recepciones cuyo producto ya tiene ingrediente asignado."""
    nuevos = 0
    pendientes = con.execute(
        """SELECT r.id, r.lote_texto, r.revisar, r.nota, a.ingrediente_id
           FROM recepciones r JOIN alias a ON a.id=r.alias_id
           WHERE r.lote_id IS NULL AND a.ingrediente_id IS NOT NULL AND a.ignorar=0
             AND r.anulado=0 AND r.rechazado=0""").fetchall()
    for r in pendientes:
        lote = con.execute("SELECT id, origen FROM lotes WHERE ingrediente_id=? AND lote=?",
                           (r["ingrediente_id"], r["lote_texto"])).fetchone()
        if lote is None:
            cur = con.execute(
                "INSERT INTO lotes(ingrediente_id,lote,origen,revisar,nota,creado_en,creado_por) VALUES(?,?,?,?,?,?,?)",
                (r["ingrediente_id"], r["lote_texto"], "excel", r["revisar"], r["nota"], ahora(), "Importación"))
            lote_id = cur.lastrowid
            nuevos += 1
        else:
            lote_id = lote["id"]
            con.execute("UPDATE lotes SET principal_vacio=0, agotado=0 WHERE id=?", (lote_id,))
            if lote["origen"] == "manual":
                # Un lote anotado a mano que ahora si aparece en el Excel: queda resuelto.
                con.execute("UPDATE lotes SET origen='excel', revisar=0, nota=nota || ' (confirmado luego por bodega principal)' WHERE id=?",
                            (lote_id,))
                anotar(con, "Importación", "LOTE_CONFIRMADO", "Lote anotado a mano confirmado por bodega principal", lote_id)
                con.execute("""UPDATE avisos SET resuelto_en=?, resuelto_por='Automático',
                               comentario='Bodega principal registró la recepción de este lote.'
                               WHERE lote_id=? AND resuelto_en IS NULL""", (ahora(), lote_id))
        con.execute("UPDATE recepciones SET lote_id=? WHERE id=?", (lote_id, r["id"]))
    return nuevos


def importar_excel(con, contenido, persona):
    import openpyxl
    try:
        libro = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
    except Exception:
        raise ErrorApi("No pude abrir el archivo. Debe ser un Excel .xlsx.")
    hoja, fila_enc, col = ubicar_tabla(libro)
    res = {"hoja": hoja.title, "filas_leidas": 0, "recepciones_nuevas": 0, "ya_importadas": 0,
           "sin_lote": [], "productos_nuevos": [], "avisos": []}

    def celda(fila, nombre):
        i = col.get(nombre)
        return fila[i] if i is not None and i < len(fila) else None

    for n, fila in enumerate(hoja.iter_rows(min_row=fila_enc + 1, values_only=True), fila_enc + 1):
        producto = str(celda(fila, "producto") or "").strip()
        if not producto:
            continue
        res["filas_leidas"] += 1
        lote, aviso = texto_celda(celda(fila, "lote"))
        fecha = fecha_celda(celda(fila, "fecha"))
        proveedor = str(celda(fila, "proveedor") or "").strip()
        cantidad, _ = texto_celda(celda(fila, "cantidad"))
        caducidad = fecha_celda(celda(fila, "caducidad")) if celda(fila, "caducidad") else ""
        extra = {k: texto_celda(celda(fila, k))[0] for k in ("unidad", "apariencia", "transporte", "aprobado_por", "observaciones")}
        if not lote:
            res["sin_lote"].append({"fila": n, "producto": producto, "fecha": fecha})
            continue
        if con.execute(
                """SELECT 1 FROM recepciones r JOIN alias a ON a.id=r.alias_id WHERE r.origen='app'
                   AND r.fecha_ingreso=? AND a.clave=? AND r.lote_texto=?""", (fecha, normalizar(producto), lote)).fetchone():
            res["ya_importadas"] += 1      # ya registrada en la app (por ejemplo, un Excel exportado desde aqui)
            continue
        alias_id, es_nuevo = alias_para(con, producto)
        if es_nuevo:
            res["productos_nuevos"].append(producto)
        huella = hashlib.sha1("|".join([fecha, normalizar(producto), proveedor, lote, cantidad]).encode("utf-8")).hexdigest()
        partes = [p.strip() for p in SEPARADOR_LOTES.split(lote) if p.strip()] or [lote]
        for i, parte in enumerate(partes):
            nota, revisar = aviso, 1 if aviso else 0
            if len(partes) > 1:
                revisar = 1
                nota = ("Separado automáticamente de «%s». Verificar que sean lotes distintos. " % lote) + nota
            cur = con.execute(
                """INSERT OR IGNORE INTO recepciones(fila_hash,parte,alias_id,lote_texto,texto_original,fecha_ingreso,
                   proveedor,cantidad,caducidad,revisar,nota,importado_en,unidad,apariencia,transporte,aprobado_por,
                   observaciones,origen,registrado_por,registrado_en)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'excel',?,?)""",
                (huella, i, alias_id, parte, lote, fecha, proveedor, cantidad, caducidad, revisar, nota.strip(), ahora(),
                 extra["unidad"].upper(), extra["apariencia"].upper(), extra["transporte"].upper(),
                 extra["aprobado_por"].upper(), extra["observaciones"], persona, ahora()))
            if cur.rowcount:
                res["recepciones_nuevas"] += 1
            else:
                res["ya_importadas"] += 1
    res["lotes_nuevos"] = materializar(con)
    aplicar_empaques(con)
    res["por_asignar"] = con.execute(
        "SELECT COUNT(*) FROM alias WHERE ingrediente_id IS NULL AND ignorar=0").fetchone()[0]
    anotar(con, persona, "IMPORTACION",
           "%d filas leídas, %d recepciones nuevas, %d lotes nuevos, %d filas sin lote" % (
               res["filas_leidas"], res["recepciones_nuevas"], res["lotes_nuevos"], len(res["sin_lote"])))
    return res


TABLAS_REGISTROS = ("produccion_lotes", "produccion_lineas", "armado_lotes", "despacho_lineas", "despachos", "bajas_congelador",
                    "producciones", "inventario_lotes", "inventarios", "aperturas", "salidas_cocina", "salidas_areas", "traslados",
                    "bajas", "avisos", "recepciones", "lotes", "movimientos")


@app.post("/api/admin/borrar-registros")
def api_borrar_registros():
    """Deja la app en cero para cargar datos reales: borra recepciones, lotes, hojas, despachos, inventarios e historial.
    Se conservan recetas, productos, ingredientes, destinos, contraseñas y la lista de productos de bodega.
    Antes de borrar guarda una copia completa en datos/respaldos."""
    d = cuerpo()
    if str(d.get("confirmacion") or "").strip().upper() != "BORRAR":
        raise ErrorApi("Escribe BORRAR para confirmar.")
    con = db()
    if claves_activas(con) and session.get("area") != "produccion":
        raise ErrorApi("Solo el área de Producción puede borrar los registros.", 403)
    persona = persona_de(d)
    os.makedirs(RESPALDOS, exist_ok=True)
    copia_nombre = "antes_de_borrar_%s.db" % dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    copia = sqlite3.connect(os.path.join(RESPALDOS, copia_nombre))
    con.commit()
    con.backup(copia)
    copia.close()
    cuentas = {}
    for t in TABLAS_REGISTROS:
        cuentas[t] = con.execute("DELETE FROM %s" % t).rowcount
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():   # numeracion desde 1
        con.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ",".join("?" * len(TABLAS_REGISTROS)), TABLAS_REGISTROS)
    anotar(con, persona, "REGISTROS BORRADOS", "Se borraron los registros para empezar de cero (%d recepciones, %d lotes, %d hojas). "
           "Copia guardada en respaldos/%s" % (cuentas["recepciones"], cuentas["lotes"], cuentas["producciones"], copia_nombre))
    con.commit()
    con.execute("VACUUM")
    return jsonify(ok=True, copia=copia_nombre, recepciones=cuentas["recepciones"], lotes=cuentas["lotes"], hojas=cuentas["producciones"])


@app.post("/api/importar")
def api_importar():
    persona = (request.args.get("persona") or "").strip()
    if not persona:
        raise ErrorApi("Primero elige quién eres en la pantalla de inicio.")
    contenido = request.get_data()
    if not contenido:
        raise ErrorApi("No llegó ningún archivo.")
    con = db()
    res = importar_excel(con, contenido, persona)
    con.commit()
    return jsonify(res)


# ----------------------------------------------------------------------------
# 3b. Registro de recepcion en bodega principal (reemplaza al Excel de bodega)
# ----------------------------------------------------------------------------
# Encabezado del formato BPM. Si cambia la version del registro, se edita aqui.
FORMATO_RECEPCION = {
    "empresa": "SAN FRANCISCO",
    "subempresa": "FOODSERVICE · USFQ GROUP",
    "procedimiento": "PROCEDIMIENTO DE RECEPCIÓN DE MATERIAS PRIMAS E INSUMOS",
    "registro": "REGISTRO DE RECEPCION DE MATERIA PRIMA E INSUMOS",
    "codigo": "BPM-SFFS-RRMP001",
    "version": "1",
    "fecha_elab": "10/1/2026",
}
UNIDADES = ["UNI", "KG", "GR", "LT", "ML", "CUBETA", "QUINTAL", "PAQUETE", "CAJA", "FUNDA", "BIDON"]
RESULTADOS = ("OK", "NO OK")


def unidades_lista(con):
    return [f[0] for f in con.execute("SELECT nombre FROM unidades ORDER BY id")]


def comprobar_unidad(con, unidad, anterior=None):
    """La unidad debe estar en la lista (al corregir se acepta la que ya tenia la fila)."""
    if unidad and unidad != anterior and unidad not in unidades_lista(con):
        raise ErrorApi("«%s» no está en la lista de unidades. Elígela de la lista o agrégala con «Agregar unidad nueva»." % unidad)


def fecha_valida(texto, campo):
    try:
        return dt.date.fromisoformat(str(texto or "")).isoformat()
    except ValueError:
        raise ErrorApi("Revisa la %s." % campo)


# ----------------------------------------------------------------------------
# Empaques: bodega principal, bodega secundaria y cocina cuentan en paquetes (cubetas, botellas...) y la app
# calcula lo que equivale (kg, litros, huevos). Asi nadie tiene que multiplicar.
# ----------------------------------------------------------------------------
EMPAQUES = ["PAQUETE", "CUBETA", "FUNDA", "BOTELLA", "BIDON", "CAJA"]
UNIDADES_CONTENIDO = {"kg": ("masa", 1000), "g": ("masa", 1), "lt": ("volumen", 1000), "ml": ("volumen", 1), "unidades": ("cuenta", 1),
                      "huevos": ("cuenta", 1)}
EMPAQUES_INICIALES = {      # lo que indico la panaderia (nov 2026)
    "AJONJOLI BLANCO": ("PAQUETE", 1, "kg"), "AZUCAR (KG)": ("PAQUETE", 50, "kg"), "AZUCAR MICRO IMPALPABLE (KG)": ("PAQUETE", 5, "kg"),
    "AZUCAR MORENA (KG)": ("PAQUETE", 2, "kg"), "SEMILLA CHIA": ("PAQUETE", 1, "kg"),
    "ESENCIA VAINILLA BLANCA BOTELLA (500ML)": ("BOTELLA", 500, "ml"), "HARINA DE TRIGO QUINTAL (50KG)": ("PAQUETE", 50, "kg"),
    "LEVADURA SECA PAQUETE (500GR)": ("PAQUETE", 500, "g"), "LINAZA KG": ("PAQUETE", 1, "kg"), "SAL CRISAL": ("PAQUETE", 2, "kg"),
    "ACEITE VEGETAL BIDON (20LT)": ("BIDON", 20, "lt"), "HUEVOS": ("CUBETA", 30, "huevos"),
    "JAMON AMERICANO REBANADO PAQUETE (1KG)": ("PAQUETE", 1, "kg"), "LECHE ENTERA FUNDA (1LT)": ("FUNDA", 1, "lt"),
    "MANTECA VEGETAL MANTEPAN": ("PAQUETE", 15, "kg"), "MANTEQUILLA S/SAL": ("PAQUETE", 2.2, "kg"),
    "QUESO PARMESANO NACIONAL RALLADO KG": ("PAQUETE", 250, "g"), "VINO BLANCO COCINA CARTON (1LT)": ("PAQUETE", 1.1, "lt"),
    "HARINA INTEGRAL QUINTAL (50KG)": ("PAQUETE", 50, "kg"), "CANELA POLVO": ("PAQUETE", 1, "kg"),
    "FUNDA VACIO GRANDE": ("PAQUETE", 50, "unidades"), "FUNDA VACIO MEDIANA": ("PAQUETE", 50, "unidades"),
}
_UNIDADES_ESCRITAS = {"KG": ("masa", 1000), "KGS": ("masa", 1000), "G": ("masa", 1), "GR": ("masa", 1), "GRS": ("masa", 1),
                      "LT": ("volumen", 1000), "L": ("volumen", 1000), "LTS": ("volumen", 1000), "ML": ("volumen", 1),
                      "UNI": ("cuenta", 1), "UND": ("cuenta", 1), "UNIDAD": ("cuenta", 1), "UNIDADES": ("cuenta", 1), "HUEVOS": ("cuenta", 1)}


def a_paquetes(cantidad, unidad, empaque, contenido, unidad_contenido):
    """Pasa una cantidad escrita en otra unidad (KG, UNI...) a paquetes. None si no se puede saber."""
    u = " ".join(str(unidad or "").upper().split())
    if u == empaque:
        return cantidad
    if u in EMPAQUES or u in ("QUINTAL", "SACO", "COSTAL", "BULTO"):
        return cantidad                                   # ya venia contado por empaque
    fam_c, base_c = UNIDADES_CONTENIDO[unidad_contenido]
    if u in _UNIDADES_ESCRITAS:
        fam, base = _UNIDADES_ESCRITAS[u]
        if fam == fam_c:
            return cantidad * base / (contenido * base_c)
        if fam == "cuenta":
            return cantidad                               # «20 UNI» de harina = 20 paquetes
    return None


def aplicar_empaques(con):
    """Pone el empaque indicado por la panaderia a los productos que nunca lo tuvieron y convierte lo registrado."""
    for a in con.execute("SELECT id, producto FROM alias WHERE empaque IS NULL").fetchall():
        e = EMPAQUES_INICIALES.get(a["producto"])
        if e:
            con.execute("UPDATE alias SET empaque=?, contenido=?, unidad_contenido=? WHERE id=?", (e[0], e[1], e[2], a["id"]))
    convertir_a_paquetes(con)


def convertir_a_paquetes(con, alias_id=None):
    """Recepciones, traslados, salidas a cocina y bajas registrados en otra unidad pasan a paquetes del producto."""
    donde = "a.empaque<>'' AND a.contenido>0" + (" AND a.id=%d" % alias_id if alias_id else "")
    for a in con.execute("SELECT * FROM alias a WHERE " + donde).fetchall():
        conv = lambda cant, uni: a_paquetes(cant, uni, a["empaque"], a["contenido"], a["unidad_contenido"])
        for r in con.execute("SELECT id, cantidad, unidad, nota FROM recepciones WHERE alias_id=? AND COALESCE(unidad,'')<>?",
                             (a["id"], a["empaque"])).fetchall():
            n = numero(r["cantidad"])
            p = conv(n, r["unidad"]) if n is not None else None
            if p is None:
                aviso = "Unidad «%s» sin convertir a %s: revisar la cantidad." % (r["unidad"] or "sin unidad", a["empaque"].lower())
                if aviso not in (r["nota"] or ""):
                    con.execute("UPDATE recepciones SET revisar=1, nota=TRIM(COALESCE(nota,'') || ' ' || ?) WHERE id=?", (aviso, r["id"]))
                continue
            entero = abs(p - round(p)) < 0.01
            nota = "Convertido de «%s %s»%s." % (r["cantidad"], r["unidad"], "" if entero else " (no da empaques enteros: revisar)")
            con.execute("UPDATE recepciones SET cantidad=?, unidad=?, revisar=MAX(revisar,?), nota=TRIM(COALESCE(nota,'') || ' ' || ?) WHERE id=?",
                        (fmt(round(p) if entero else round(p, 2)), a["empaque"], 0 if entero else 1, nota, r["id"]))
        lotes = [f[0] for f in con.execute("SELECT DISTINCT lote_id FROM recepciones WHERE alias_id=? AND lote_id IS NOT NULL", (a["id"],))]
        for tabla in ("traslados", "salidas_cocina", "salidas_areas", "bajas"):
            for t in con.execute("SELECT id, cantidad, unidad FROM %s WHERE lote_id IN (%s) AND COALESCE(unidad,'')<>? AND cantidad IS NOT NULL"
                                 % (tabla, ",".join("?" * len(lotes))), lotes + [a["empaque"]]).fetchall() if lotes else []:
                p = conv(t["cantidad"], t["unidad"])
                if p is not None:
                    con.execute("UPDATE %s SET cantidad=?, unidad=? WHERE id=?" % tabla, (round(p, 3), a["empaque"], t["id"]))


def empaque_de(con, producto):
    fila = con.execute("SELECT empaque, contenido, unidad_contenido FROM alias WHERE producto=? AND COALESCE(empaque,'')<>''",
                       (producto,)).fetchone()
    return dict(fila) if fila else None


def entero_paquetes(texto, empaque, que):
    n = numero(texto)
    if n is None or n <= 0 or n != int(n):
        raise ErrorApi("Escribe %s como número entero de %s (por ejemplo 3)." % (que, plural_empaque(empaque)))
    return int(n)


def cantidad_texto(n, lote):
    """'5 cubetas (150 huevos)' o '5 KG' si el producto no tiene empaque."""
    if lote.get("empaque") and lote.get("contenido"):
        total, u = n * lote["contenido"], lote["unidad_contenido"]
        if u in ("g", "ml") and total >= 1000:
            total, u = total / 1000, {"g": "kg", "ml": "lt"}[u]
        return "%s %s (%s %s)" % (fmt(n), plural_empaque(lote["empaque"]) if n != 1 else lote["empaque"].lower().replace("bidon", "bidón"), fmt(total), u)
    return ("%s %s" % (fmt(n), lote.get("unidad") or "")).strip()


def plural_empaque(e):
    e = (e or "").lower()
    return {"bidon": "bidones"}.get(e, e + "s")


def validar_recepcion(d, estricto=True):
    """Valida los campos del registro. estricto=False para corregir filas importadas de Excel antiguos."""
    texto = lambda k: " ".join(str(d.get(k) or "").split())
    v = {
        "fecha": fecha_valida(d.get("fecha"), "fecha de ingreso"),
        "proveedor": texto("proveedor").upper(),
        "lote": str(d.get("lote") or "").strip(),          # el lote se guarda tal cual se escribio
        "cantidad": texto("cantidad"),
        "unidad": texto("unidad").upper(),
        "caducidad": fecha_valida(d["caducidad"], "fecha de caducidad") if d.get("caducidad") else "",
        "apariencia": texto("apariencia").upper(),
        "transporte": texto("transporte").upper(),
        "aprobado_por": texto("aprobado_por").upper(),
        "observaciones": str(d.get("observaciones") or "").strip(),
    }
    obligatorios = [("proveedor", "proveedor"), ("lote", "lote"), ("cantidad", "cantidad")]
    if estricto:
        obligatorios += [("caducidad", "fecha de caducidad"), ("apariencia", "apariencia visual"),
                         ("transporte", "condiciones de transporte"), ("aprobado_por", "aprobado por")]
    faltan = [nombre for clave, nombre in obligatorios if not v[clave]]
    if faltan:
        raise ErrorApi("Falta: " + ", ".join(faltan) + ".")
    if estricto:
        cantidad = numero(v["cantidad"])
        if cantidad is None or cantidad <= 0:
            raise ErrorApi("La cantidad debe ser solo un número (por ejemplo 20 o 2,5).")
        v["cantidad"] = fmt(cantidad)
    for clave in ("apariencia", "transporte"):
        if v[clave] and v[clave] not in RESULTADOS:
            raise ErrorApi("Apariencia y transporte deben ser OK o NO OK.")
    v["rechazado"] = 0
    if "NO OK" in (v["apariencia"], v["transporte"]):
        if d.get("decision") not in ("aceptado", "rechazado"):
            raise ErrorApi("Algo no está OK: indica si el producto se recibió igual o se rechazó.")
        if not v["observaciones"]:
            raise ErrorApi("Algo no está OK: escribe la observación.")
        v["rechazado"] = 1 if d["decision"] == "rechazado" else 0
    return v


def desvincular(con, rec):
    """Separa una recepcion de su lote (al anularla, rechazarla o cambiarle el codigo).
    Si era la unica recepcion del lote, el lote se elimina, salvo que ya se haya movido o usado."""
    lote_id = rec["lote_id"]
    if not lote_id:
        return
    otras = con.execute("SELECT COUNT(*) FROM recepciones WHERE lote_id=? AND id<>?", (lote_id, rec["id"])).fetchone()[0]
    if otras:
        con.execute("UPDATE recepciones SET lote_id=NULL WHERE id=?", (rec["id"],))
        return
    if lote_en_uso(con, lote_id):
        raise ErrorApi("Ese lote ya pasó a bodega secundaria o se usó en producción, así que no se puede cambiar su "
                       "código ni anularlo. Anota la corrección en Observaciones.")
    con.execute("UPDATE recepciones SET lote_id=NULL WHERE lote_id=?", (lote_id,))
    # Si el lote ya aparece en un inventario, una baja o un aviso, no se borra (se perderia ese historial):
    # queda cerrado y fuera de todas las listas.
    referido = any(con.execute("SELECT 1 FROM %s WHERE lote_id=? LIMIT 1" % t, (lote_id,)).fetchone()
                   for t in ("inventario_lotes", "bajas", "avisos", "salidas_areas", "traslados", "salidas_cocina"))
    if referido:
        con.execute("UPDATE lotes SET agotado=1, principal_vacio=1, en_secundaria=0 WHERE id=?", (lote_id,))
    else:
        con.execute("DELETE FROM lotes WHERE id=?", (lote_id,))


RECEPCION_SQL = """
SELECT r.*, a.producto, a.ignorar, a.ingrediente_id, i.nombre AS ingrediente,
       CASE WHEN r.lote_id IS NULL THEN 0 ELSE
         (SELECT (l.en_secundaria=1 OR EXISTS(SELECT 1 FROM aperturas ap WHERE ap.lote_id=l.id)
                  OR EXISTS(SELECT 1 FROM produccion_lotes pl WHERE pl.lote_id=l.id)) FROM lotes l WHERE l.id=r.lote_id)
       END AS en_uso
FROM recepciones r JOIN alias a ON a.id=r.alias_id LEFT JOIN ingredientes i ON i.id=a.ingrediente_id
"""


def recepcion(con, rid):
    fila = con.execute(RECEPCION_SQL + " WHERE r.id=?", (rid,)).fetchone()
    if not fila:
        raise ErrorApi("Esa recepción no existe.", 404)
    return dict(fila)


def mes_pedido():
    mes = request.args.get("mes") or dt.date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", mes):
        raise ErrorApi("Mes inválido.")
    return mes


def recepciones_del_mes(con, mes):
    return filas(con.execute(RECEPCION_SQL + " WHERE substr(r.fecha_ingreso,1,7)=? ORDER BY r.fecha_ingreso DESC, r.id DESC",
                             (mes,)))


# ----------------------------------------------------------------------------
# Fotos de los productos de bodega: static/productos/<nombre>.webp (incluidas) o fotos/productos/ (subidas).
# ----------------------------------------------------------------------------
FOTOS_PRODUCTOS = os.path.join(FOTOS, "productos")


def nombre_archivo(texto):
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def foto_producto(producto, foto=None):
    """URL de la foto de un producto de bodega, o None. foto='-' = sin foto a propósito."""
    if foto == "-":
        return None
    if foto:
        return "/fotos/productos/" + foto
    archivo = nombre_archivo(producto) + ".webp"
    if carpeta_foto_incluida(archivo):
        return "/productos/" + archivo
    return None


# Las fotos incluidas pueden estar en static/productos o en productos/ (junto a app.py).
CARPETAS_FOTOS_INCLUIDAS = [os.path.join(BASE, "static", "productos"), os.path.join(BASE, "productos")]


def carpeta_foto_incluida(archivo):
    for carpeta in CARPETAS_FOTOS_INCLUIDAS:
        if os.path.exists(os.path.join(carpeta, archivo)):
            return carpeta
    return None


@app.get("/productos/<archivo>")
def ver_foto_incluida(archivo):
    carpeta = carpeta_foto_incluida(archivo)
    if not carpeta:
        return Response(status=404)
    return send_from_directory(carpeta, archivo, max_age=86400)


def fotos_por_ingrediente(con):
    """Foto de cada ingrediente: la del primer producto de la lista de bodega que corresponde a ese ingrediente."""
    salida = {}
    for a in con.execute("""SELECT ingrediente_id, producto, foto FROM alias WHERE ingrediente_id IS NOT NULL AND ignorar=0
                            ORDER BY en_catalogo DESC, producto"""):
        if a["ingrediente_id"] not in salida:
            url = foto_producto(a["producto"], a["foto"])
            if url:
                salida[a["ingrediente_id"]] = url
    return salida


def quitar_fondo(imagen):
    """Intenta quitar el fondo (producto al centro). Necesita OpenCV; si no está instalado, deja la foto como está."""
    try:
        import numpy as np
        import cv2
    except ImportError:
        return imagen.convert("RGBA"), False
    from PIL import Image
    rgb = imagen.convert("RGB")
    esc = 700 / max(rgb.size)
    peq = np.array(rgb.resize((max(1, int(rgb.width * esc)), max(1, int(rgb.height * esc)))))
    h, w = peq.shape[:2]
    mascara = np.zeros((h, w), np.uint8)
    fondo, frente = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(peq, cv2.COLOR_RGB2BGR), mascara, (int(w * .04), int(h * .04), int(w * .92), int(h * .92)), fondo, frente, 6,
                cv2.GC_INIT_WITH_RECT)
    m = np.where((mascara == cv2.GC_FGD) | (mascara == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    n, etiquetas, datos, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        m = np.where(etiquetas == 1 + np.argmax(datos[1:, cv2.CC_STAT_AREA]), 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    contornos, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    m = np.zeros_like(m)
    cv2.drawContours(m, contornos, -1, 255, -1)
    if m.mean() < 255 * 0.08:          # si casi no encontró producto, mejor no recortar
        return rgb.convert("RGBA"), False
    alfa = cv2.resize(cv2.GaussianBlur(m, (5, 5), 0), rgb.size, interpolation=cv2.INTER_LINEAR)
    salida = rgb.convert("RGBA")
    salida.putalpha(Image.fromarray(alfa))
    return salida.crop(salida.getbbox() or (0, 0, rgb.width, rgb.height)), True


@app.post("/api/bodega/productos/<int:alias_id>/foto")
def api_producto_foto(alias_id):
    """Cambia la foto de un producto de bodega (se intenta quitar el fondo y se guarda cuadrada y liviana)."""
    from PIL import Image, ImageOps
    con = db()
    alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not alias:
        raise ErrorApi("Ese producto no existe.", 404)
    contenido = request.get_data()
    try:
        imagen = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido)))
    except Exception:
        raise ErrorApi("No se pudo leer la foto. Usa una imagen JPG o PNG.")
    recortada, sin_fondo = quitar_fondo(imagen) if request.args.get("fondo", "1") == "1" else (imagen.convert("RGBA"), False)
    recortada.thumbnail((320, 320))
    lienzo = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    lienzo.paste(recortada, ((320 - recortada.width) // 2, (320 - recortada.height) // 2), recortada)
    os.makedirs(FOTOS_PRODUCTOS, exist_ok=True)
    nombre = "%d_%s.webp" % (alias_id, uuid.uuid4().hex[:8])
    lienzo.save(os.path.join(FOTOS_PRODUCTOS, nombre), "WEBP", quality=82)
    con.execute("UPDATE alias SET foto=? WHERE id=?", (nombre, alias_id))
    anotar(con, persona_de(request.args), "FOTO_PRODUCTO", alias["producto"])
    con.commit()
    return jsonify(ok=True, foto=foto_producto(alias["producto"], nombre), sin_fondo=sin_fondo)


@app.post("/api/bodega/productos/<int:alias_id>/foto/quitar")
def api_producto_foto_quitar(alias_id):
    con = db()
    alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not alias:
        raise ErrorApi("Ese producto no existe.", 404)
    # Si había una subida se vuelve a la incluida en la app; si no, queda sin foto.
    nuevo = None if alias["foto"] and alias["foto"] != "-" else "-"
    con.execute("UPDATE alias SET foto=? WHERE id=?", (nuevo, alias_id))
    con.commit()
    return jsonify(ok=True, foto=foto_producto(alias["producto"], nuevo))


@app.get("/api/bodega/catalogo")
def api_catalogo():
    con = db()
    productos = filas(con.execute(
        """SELECT a.id, a.producto, a.ignorar, a.foto, i.nombre AS ingrediente, a.empaque, a.contenido, a.unidad_contenido,
                  (SELECT r.unidad FROM recepciones r WHERE r.alias_id=a.id AND r.anulado=0 AND r.unidad<>''
                   ORDER BY r.fecha_ingreso DESC, r.id DESC LIMIT 1) AS unidad
           FROM alias a LEFT JOIN ingredientes i ON i.id=a.ingrediente_id WHERE a.en_catalogo=1 ORDER BY a.producto"""))
    for p in productos:
        p["foto"] = foto_producto(p["producto"], p["foto"])
    return jsonify(productos=productos, unidades=unidades_lista(con), areas=AREAS_DESTINO, dias_por_caducar=DIAS_POR_CADUCAR,
                   empaques=EMPAQUES, unidades_contenido=list(UNIDADES_CONTENIDO),
                   ingredientes=filas(con.execute("SELECT id, nombre FROM ingredientes WHERE tipo='lote' ORDER BY nombre")),
                   formato=FORMATO_RECEPCION, hoy=dt.date.today().isoformat())


@app.post("/api/bodega/productos")
def api_producto_bodega_nuevo():
    """Agrega un producto a la lista de bodega principal (el nombre queda escrito tal cual)."""
    d = cuerpo()
    persona = persona_de(d)
    nombre = " ".join(str(d.get("nombre") or "").upper().split())
    if len(nombre) < 3:
        raise ErrorApi("Escribe el nombre del producto.")
    con = db()
    previo = con.execute("SELECT producto FROM alias WHERE clave=? AND en_catalogo=1", (normalizar(nombre),)).fetchone()
    if previo:
        raise ErrorApi("Ya está en la lista como «%s»." % previo["producto"])
    empaque = datos_empaque(d) if "empaque" in d else None
    alias_id = agregar_al_catalogo(con, nombre)
    if empaque:
        con.execute("UPDATE alias SET empaque=?, contenido=?, unidad_contenido=? WHERE id=?", empaque + (alias_id,))
        convertir_a_paquetes(con, alias_id)
    ingrediente_propio(con, alias_id)
    materializar(con)
    anotar(con, persona, "PRODUCTO_BODEGA_NUEVO", nombre)
    con.commit()
    alias = con.execute("SELECT a.*, i.nombre AS ingrediente FROM alias a LEFT JOIN ingredientes i ON i.id=a.ingrediente_id WHERE a.id=?",
                        (alias_id,)).fetchone()
    return jsonify(id=alias_id, producto=nombre, ingrediente=alias["ingrediente"], aviso="")


@app.post("/api/bodega/unidades")
def api_unidad_nueva():
    """Agrega una unidad de medida a la lista de bodega principal."""
    d = cuerpo()
    persona = persona_de(d)
    nombre = " ".join(str(d.get("nombre") or "").upper().split())
    if not re.search(r"[A-Z]", normalizar(nombre)):
        raise ErrorApi("Escribe el nombre de la unidad.")
    con = db()
    previa = [u for u in unidades_lista(con) if normalizar(u) == normalizar(nombre)]
    if previa:
        raise ErrorApi("Ya está en la lista como «%s»." % previa[0])
    con.execute("INSERT INTO unidades(nombre) VALUES(?)", (nombre,))
    anotar(con, persona, "UNIDAD_NUEVA", nombre)
    con.commit()
    return jsonify(unidad=nombre, unidades=unidades_lista(con))


@app.post("/api/bodega/productos/<int:alias_id>/nombre")
def api_producto_bodega_nombre(alias_id):
    """Cambia el nombre de un producto de bodega (las recepciones anteriores quedan con el producto, con el nombre nuevo)."""
    d = cuerpo()
    persona = persona_de(d)
    nombre = " ".join(str(d.get("nombre") or "").upper().split())
    if len(nombre) < 3:
        raise ErrorApi("Escribe el nombre del producto.")
    con = db()
    alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not alias:
        raise ErrorApi("Ese producto no existe.", 404)
    if nombre == alias["producto"]:
        return jsonify(ok=True)
    otro = con.execute("SELECT producto FROM alias WHERE clave=? AND id<>?", (normalizar(nombre), alias_id)).fetchone()
    if otro:
        raise ErrorApi("Ya existe un producto llamado «%s»." % otro["producto"])
    # La foto incluida en la app depende del nombre: se copia para que no se pierda al cambiarlo.
    foto = alias["foto"]
    if not foto:
        archivo = nombre_archivo(alias["producto"]) + ".webp"
        carpeta = carpeta_foto_incluida(archivo)
        if carpeta:
            os.makedirs(FOTOS_PRODUCTOS, exist_ok=True)
            foto = "%d_%s.webp" % (alias_id, uuid.uuid4().hex[:8])
            with open(os.path.join(carpeta, archivo), "rb") as origen, open(os.path.join(FOTOS_PRODUCTOS, foto), "wb") as destino:
                destino.write(origen.read())
    con.execute("UPDATE alias SET producto=?, clave=?, foto=? WHERE id=?", (nombre, normalizar(nombre), foto, alias_id))
    renombrar_ingrediente_unico(con, alias_id)
    anotar(con, persona, "PRODUCTO_BODEGA_NOMBRE", "%s → %s" % (alias["producto"], nombre))
    con.commit()
    return jsonify(ok=True)


@app.post("/api/bodega/productos/<int:alias_id>/lista")
def api_producto_bodega_lista(alias_id):
    """Quita o vuelve a poner un producto en la lista de bodega principal."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not alias:
        raise ErrorApi("Ese producto no existe.", 404)
    en_lista = 1 if d.get("en_lista") else 0
    con.execute("UPDATE alias SET en_catalogo=? WHERE id=?", (en_lista, alias_id))
    anotar(con, persona, "PRODUCTO_BODEGA_LISTA", "%s: %s" % (alias["producto"], "agregado a la lista" if en_lista else "quitado de la lista"))
    con.commit()
    return jsonify(ok=True)


ORDEN_SEMAFORO = {"rojo": 0, "amarillo": 1, "verde": 2, None: 3}
SALIDAS_SQL = """SELECT o.id, o.area, o.cantidad, o.unidad, o.motivo, o.fecha_hora, o.persona, o.anulado, o.motivo_anulacion,
                  l.lote, i.nombre AS ingrediente,
                  (SELECT a.producto FROM recepciones r JOIN alias a ON a.id=r.alias_id WHERE r.lote_id=l.id
                   ORDER BY r.fecha_ingreso DESC LIMIT 1) AS producto_bodega
           FROM salidas_areas o JOIN lotes l ON l.id=o.lote_id JOIN ingredientes i ON i.id=l.ingrediente_id"""


@app.get("/api/inventarios.xlsx")
def api_inventarios_xlsx():
    """Inventarios (resumen y cada lote revisado), bajas y semaforo de caducidad de bodega principal."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    con = db()
    lotes = listar_lotes(con, "principal", limite=2000)
    lotes.sort(key=lambda l: (ORDEN_SEMAFORO[l["semaforo"]], l["caducidad"] or "9999", l["id"]))
    libro = openpyxl.Workbook()
    fino = Side(style="thin", color="999999")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    cabecera = PatternFill("solid", fgColor="F1E7D8")

    def tabla(hoja, titulo, columnas, filas_datos, anchos):
        hoja["A1"] = titulo
        hoja["A1"].font = Font(bold=True, size=13)
        hoja["A2"] = "San Francisco FoodService · descargado el " + dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        hoja["A2"].font = Font(italic=True, color="6B5D52")
        for c, t in enumerate(columnas, 1):
            celda = hoja.cell(row=4, column=c, value=t)
            celda.font, celda.fill, celda.border = Font(bold=True), cabecera, borde
            celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for n, valores in enumerate(filas_datos, 5):
            for c, v in enumerate(valores, 1):
                celda = hoja.cell(row=n, column=c, value=v)
                celda.border = borde
                celda.alignment = Alignment(vertical="center", wrap_text=True)
                if isinstance(v, dt.datetime):
                    celda.number_format = "dd/mm/yyyy hh:mm"
                elif isinstance(v, dt.date):
                    celda.number_format = "dd/mm/yyyy"
        for c, ancho in enumerate(anchos, 1):
            hoja.column_dimensions[openpyxl.utils.get_column_letter(c)].width = ancho
        hoja.freeze_panes = "A5"
        if filas_datos:
            hoja.auto_filter.ref = "A4:%s%d" % (openpyxl.utils.get_column_letter(len(columnas)), 4 + len(filas_datos))

    def fecha_hora(t):
        try:
            return dt.datetime.fromisoformat(t)
        except (TypeError, ValueError):
            return t

    def fecha(t):
        try:
            return dt.date.fromisoformat(str(t)[:10]) if t else None
        except ValueError:
            return t

    entero = lambda x: int(x) if x is not None and x == int(x) else x
    lugares = {"principal": "BODEGA PRINCIPAL", "secundaria": "BODEGA SECUNDARIA", "cocina": "COCINA"}
    hoja = libro.active
    hoja.title = "Inventarios"
    invs = filas(con.execute("SELECT * FROM inventarios ORDER BY id"))
    tabla(hoja, "Inventarios realizados",
          ["FECHA Y HORA", "BODEGA", "REGISTRÓ", "TIPO", "LOTES REVISADOS", "SIGUEN EN BODEGA", "YA NO ESTABAN (SE CERRARON)", "COMENTARIO"],
          [[fecha_hora(i["fecha_hora"]), lugares.get(i["lugar"], i["lugar"]), i["persona"],
            "BAJA: YA NO ESTÁ" if i["tipo"] == "suelto" else "INVENTARIO", i["revisados"], i["revisados"] - i["cerrados"], i["cerrados"], i["nota"] or None]
           for i in invs], [17, 20, 18, 18, 12, 12, 16, 30])
    detalle = filas(con.execute(
        """SELECT inv.fecha_hora, inv.lugar, inv.persona, il.esta, il.reabierto_en, il.reabierto_por, l.lote, i.nombre AS ingrediente,
                  (SELECT a.producto FROM recepciones r JOIN alias a ON a.id=r.alias_id WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS producto,
                  (SELECT r.proveedor FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS proveedor,
                  COALESCE((SELECT r.caducidad FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1), l.caducidad) AS caducidad
           FROM inventario_lotes il JOIN inventarios inv ON inv.id=il.inventario_id JOIN lotes l ON l.id=il.lote_id
           JOIN ingredientes i ON i.id=l.ingrediente_id ORDER BY inv.id, il.esta, i.nombre"""))
    hoja_d = libro.create_sheet("Lotes revisados")
    tabla(hoja_d, "Cada lote revisado en los inventarios",
          ["FECHA DEL INVENTARIO", "BODEGA", "PRODUCTO", "LOTE", "PROVEEDOR", "CADUCIDAD", "RESULTADO", "REABIERTO", "REGISTRÓ"],
          [[fecha_hora(d["fecha_hora"]), lugares.get(d["lugar"], d["lugar"]), d["producto"] or d["ingrediente"], d["lote"], d["proveedor"],
            fecha(d["caducidad"]), "ESTÁ" if d["esta"] else "YA NO ESTÁ (CERRADO)",
            ("Sí, " + d["reabierto_en"][:16].replace("T", " ") + " · " + (d["reabierto_por"] or "")) if d["reabierto_en"] else None, d["persona"]]
           for d in detalle], [17, 20, 34, 16, 20, 12, 22, 24, 18])
    for n, d in enumerate(detalle, 5):
        hoja_d.cell(row=n, column=4).number_format = "@"
        if not d["esta"]:
            hoja_d.cell(row=n, column=7).fill = PatternFill("solid", fgColor="F4C7C3")
    bajas_todas = filas(con.execute(
        """SELECT b.fecha_hora, b.lugar, i.nombre AS ingrediente, l.lote, b.cantidad, b.unidad, b.motivo, b.comentario, b.persona,
                  b.anulado, b.motivo_anulacion FROM bajas b JOIN lotes l ON l.id=b.lote_id JOIN ingredientes i ON i.id=l.ingrediente_id
           UNION ALL
           SELECT o.fecha_hora, 'principal', i.nombre, l.lote, o.cantidad, o.unidad, o.motivo, '', o.persona, o.anulado, o.motivo_anulacion
           FROM salidas_areas o JOIN lotes l ON l.id=o.lote_id JOIN ingredientes i ON i.id=l.ingrediente_id WHERE o.area='BAJA'
           ORDER BY 1"""))
    hoja_b = libro.create_sheet("Bajas")
    tabla(hoja_b, "Lotes dados de baja (caducados o contaminados)",
          ["FECHA Y HORA", "LUGAR", "INGREDIENTE", "LOTE", "CANTIDAD", "UNIDAD", "MOTIVO", "COMENTARIO", "REGISTRÓ", "ESTADO", "MOTIVO DE ANULACIÓN"],
          [[fecha_hora(b["fecha_hora"]), lugares.get(b["lugar"], b["lugar"]), b["ingrediente"], b["lote"], entero(b["cantidad"]) if b["cantidad"] else None,
            b["unidad"], b["motivo"].split(":")[0], b["comentario"] or (b["motivo"].split(":", 1)[1].strip() if ":" in b["motivo"] else None),
            b["persona"], "ANULADA" if b["anulado"] else "VIGENTE", b["motivo_anulacion"] or None] for b in bajas_todas],
          [17, 18, 24, 16, 10, 10, 14, 30, 18, 11, 26])
    for n in range(5, 5 + len(bajas_todas)):
        hoja_b.cell(row=n, column=4).number_format = "@"
    hoja2 = libro.create_sheet("Semáforo de caducidad")
    nombre_color = {"rojo": "CADUCADO", "amarillo": "POR CADUCAR", "verde": "BUEN TIEMPO", None: "SIN FECHA"}
    tabla(hoja2, "Lotes en bodega principal (semáforo de caducidad)",
          ["SEMÁFORO", "PRODUCTO", "LOTE", "PROVEEDOR", "INGRESÓ", "CADUCIDAD", "DÍAS PARA CADUCAR"],
          [[nombre_color[l["semaforo"]], l["producto_bodega"] or l["ingrediente"], l["lote"], l["proveedor"], fecha(l["fecha_ingreso"]),
            fecha(l["caducidad"]), l["dias_caducidad"]] for l in lotes],
          [15, 34, 16, 20, 12, 12, 11])
    colores = {"rojo": "F4C7C3", "amarillo": "FFF0B3", "verde": "CDEBD6"}
    for n, l in enumerate(lotes, 5):
        if l["semaforo"] in colores:
            hoja2.cell(row=n, column=1).fill = PatternFill("solid", fgColor=colores[l["semaforo"]])
        hoja2.cell(row=n, column=3).number_format = "@"
    salida = io.BytesIO()
    libro.save(salida)
    salida.seek(0)
    return send_file(salida, as_attachment=True, download_name="Inventarios_%s.xlsx" % dt.date.today().strftime("%Y-%m-%d"),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----------------------------------------------------------------------------
# Inventario (cada mes): se revisa cada bodega y los lotes que ya no estan se cierran.
# ----------------------------------------------------------------------------
DIAS_INVENTARIO = 30
LUGARES_INVENTARIO = {"principal": "bodega principal", "secundaria": "bodega secundaria"}


def ultimo_inventario(con, lugar):
    fila = con.execute("SELECT * FROM inventarios WHERE lugar=? AND COALESCE(tipo,'')='' ORDER BY id DESC LIMIT 1", (lugar,)).fetchone()
    if not fila:
        return None
    d = dict(fila)
    d["dias"] = (dt.date.today() - dt.date.fromisoformat(d["fecha_hora"][:10])).days
    return d


@app.get("/api/inventarios")
def api_inventarios():
    lugar = request.args.get("lugar", "principal")
    if lugar not in LUGARES_INVENTARIO:
        raise ErrorApi("Lugar desconocido.")
    con = db()
    lista = filas(con.execute("SELECT * FROM inventarios WHERE lugar=? ORDER BY id DESC LIMIT 1000", (lugar,)))
    for inv in lista:
        inv["cerrados_lotes"] = filas(con.execute(
            """SELECT il.id, il.reabierto_en, il.reabierto_por, l.id AS lote_id, l.lote, i.nombre AS ingrediente,
                      (SELECT a.producto FROM recepciones r JOIN alias a ON a.id=r.alias_id WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS producto
               FROM inventario_lotes il JOIN lotes l ON l.id=il.lote_id JOIN ingredientes i ON i.id=l.ingrediente_id
               WHERE il.inventario_id=? AND il.esta=0 ORDER BY i.nombre""", (inv["id"],)))
    return jsonify(inventarios=lista, ultimo=ultimo_inventario(con, lugar), dias_inventario=DIAS_INVENTARIO,
                   lotes=listar_lotes(con, lugar, limite=2000))


# Cuando el inventario dice que un lote «ya no está», lo que la app creia que quedaba se descuenta (cierre).
# Asi, si despues llega otra entrega con el mismo codigo de lote, empieza desde cero y no revive lo anterior.
AREA_CIERRE = "CIERRE INVENTARIO"
MOTIVO_CIERRE = "Inventario N.º %d: ya no estaba"
_TIEMPO_RECEPCION = "COALESCE(NULLIF(r.registrado_en,''), NULLIF(r.importado_en,''), r.fecha_ingreso)"


def saldo_al(con, lote_id, lugar, hasta=None):
    """Lo que la app cree que queda del lote en esa bodega (hasta un momento dado, o ahora)."""
    t = hasta or "9999"
    q = lambda sql: con.execute(sql, (lote_id, t)).fetchone()[0] or 0
    if lugar == "principal":
        cantidades = [numero(f[0]) for f in con.execute(
            "SELECT r.cantidad FROM recepciones r WHERE r.lote_id=? AND r.anulado=0 AND " + _TIEMPO_RECEPCION + " <= ?", (lote_id, t))]
        if None in cantidades:
            return 0
        return (sum(cantidades) - q("SELECT SUM(cantidad) FROM traslados WHERE lote_id=? AND anulado=0 AND fecha_hora<=?")
                - q("SELECT SUM(cantidad) FROM salidas_areas WHERE lote_id=? AND anulado=0 AND fecha_hora<=?"))
    return (q("SELECT SUM(cantidad) FROM traslados WHERE lote_id=? AND anulado=0 AND fecha_hora<=?")
            - q("SELECT SUM(cantidad) FROM salidas_cocina WHERE lote_id=? AND anulado=0 AND fecha_hora<=?")
            - q("SELECT SUM(cantidad) FROM bajas WHERE lote_id=? AND lugar='secundaria' AND anulado=0 AND fecha_hora<=?"))


def cerrar_saldo(con, lote_id, lugar, inventario_id, persona, cuando=None):
    cuando = cuando or ahora()
    cant = max(round(saldo_al(con, lote_id, lugar, cuando), 3), 0)
    unidad = (con.execute("SELECT unidad FROM recepciones WHERE lote_id=? ORDER BY id DESC LIMIT 1", (lote_id,)).fetchone() or [""])[0]
    if lugar == "principal":
        con.execute("INSERT INTO salidas_areas(lote_id,cantidad,unidad,area,motivo,fecha_hora,persona) VALUES(?,?,?,?,?,?,?)",
                    (lote_id, cant, unidad, AREA_CIERRE, MOTIVO_CIERRE % inventario_id, cuando, persona))
    else:
        con.execute("INSERT INTO bajas(lote_id,lugar,cantidad,unidad,motivo,comentario,fecha_hora,persona) VALUES(?,?,?,?,?,?,?,?)",
                    (lote_id, "secundaria", cant, unidad, MOTIVO_CIERRE % inventario_id, AREA_CIERRE, cuando, persona))


def ajustar_cierres(con, lote_id):
    """Un cierre («ya no estaba») se lleva solo lo que había del lote en ese momento. Si después se anula o corrige una
    entrega anterior, el cierre se recalcula: así nunca se «come» una entrega nueva del mismo lote (que quedaría oculta)."""
    if not lote_id:
        return False
    cambio = False
    for lugar in ("principal", "secundaria"):
        if lugar == "principal":
            tabla = "salidas_areas"
            cierres = con.execute("SELECT id, cantidad, fecha_hora FROM salidas_areas WHERE lote_id=? AND area=? AND anulado=0 "
                                  "ORDER BY fecha_hora, id", (lote_id, AREA_CIERRE)).fetchall()
        else:
            tabla = "bajas"
            cierres = con.execute("SELECT id, cantidad, fecha_hora FROM bajas WHERE lote_id=? AND lugar='secundaria' AND comentario=? "
                                  "AND anulado=0 ORDER BY fecha_hora, id", (lote_id, AREA_CIERRE)).fetchall()
        actual = {c["id"]: c["cantidad"] or 0 for c in cierres}
        for k, c in enumerate(cierres):
            # saldo_al(hora) ya resta este cierre y los de la misma hora que vienen después: se suman de vuelta.
            antes = saldo_al(con, lote_id, lugar, c["fecha_hora"]) + sum(
                actual[x["id"]] for x in cierres[k:] if x["fecha_hora"] <= c["fecha_hora"])
            # Solo se achica: si una entrega anterior se anuló o se corrigió a menos, el cierre ya no se la puede llevar.
            nuevo = min(actual[c["id"]], max(round(antes, 3), 0))
            if abs(nuevo - actual[c["id"]]) > 1e-9:
                con.execute("UPDATE %s SET cantidad=? WHERE id=?" % tabla, (nuevo, c["id"]))
                actual[c["id"]] = nuevo
                cambio = True
    if cambio:
        l = un_lote(con, lote_id)
        if l.get("disponible") is not None:
            con.execute("UPDATE lotes SET principal_vacio=? WHERE id=? AND agotado=0", (0 if l["disponible"] > 1e-9 else 1, lote_id))
    return cambio


def reparar_inventario(con):
    """Una sola vez: (1) los productos de la lista de bodega marcados como «no son de panadería» (jamón, fundas) reciben
    su propio ingrediente, para que lo que se recibe se vea en el inventario; (2) se recalculan los cierres de inventario
    que se llevaban entregas anuladas o corregidas después."""
    if con.execute("SELECT 1 FROM meta WHERE clave='v31_reparar_inventario'").fetchone():
        return
    for a in con.execute("SELECT id FROM alias WHERE en_catalogo=1 AND (ignorar=1 OR ingrediente_id IS NULL)").fetchall():
        con.execute("UPDATE alias SET ignorar=0 WHERE id=?", (a["id"],))
        ingrediente_propio(con, a["id"])
    materializar(con)
    for f in con.execute("SELECT DISTINCT lote_id FROM salidas_areas WHERE area=? UNION SELECT DISTINCT lote_id FROM bajas WHERE comentario=?",
                         (AREA_CIERRE, AREA_CIERRE)).fetchall():
        ajustar_cierres(con, f[0])
    con.execute("INSERT INTO meta(clave, valor) VALUES('v31_reparar_inventario', ?)", (ahora(),))


def cierres_anteriores(con):
    """Una sola vez: los lotes que ya se cerraron en inventarios anteriores reciben su cierre."""
    if con.execute("SELECT 1 FROM meta WHERE clave='v26_cierres'").fetchone():
        return
    for f in con.execute("""SELECT il.lote_id, il.inventario_id, inv.lugar, inv.fecha_hora, inv.persona FROM inventario_lotes il
                            JOIN inventarios inv ON inv.id=il.inventario_id
                            WHERE il.esta=0 AND il.reabierto_en IS NULL ORDER BY inv.fecha_hora, il.id""").fetchall():
        cerrar_saldo(con, f["lote_id"], f["lugar"], f["inventario_id"], f["persona"], f["fecha_hora"])
    con.execute("INSERT INTO meta(clave, valor) VALUES('v26_cierres', '1')")


def ultimo_cierre(con, lote_id):
    fila = con.execute("SELECT MAX(fecha_hora) FROM salidas_areas WHERE lote_id=? AND area=? AND anulado=0", (lote_id, AREA_CIERRE)).fetchone()
    return fila[0] if fila else None


@app.post("/api/inventarios")
def api_inventario_guardar():
    """Guarda un inventario: items = [{lote_id, esta}]. Los que ya no estan salen de esa bodega."""
    datos = cuerpo()
    persona = persona_de(datos)
    lugar = datos.get("lugar")
    if lugar not in LUGARES_INVENTARIO:
        raise ErrorApi("Lugar desconocido.")
    items = datos.get("items") or []
    if not items:
        raise ErrorApi("No hay lotes para revisar.")
    con = db()
    actuales = {l["id"] for l in listar_lotes(con, lugar, limite=5000)}
    inv = con.execute("INSERT INTO inventarios(lugar,fecha_hora,persona) VALUES(?,?,?)", (lugar, ahora(), persona)).lastrowid
    revisados = cerrados = 0
    for it in items:
        lote_id = int(it.get("lote_id") or 0)
        if lote_id not in actuales:
            continue                       # ya no estaba en la lista (otro dispositivo lo movio)
        esta = 1 if it.get("esta", True) else 0
        con.execute("INSERT INTO inventario_lotes(inventario_id,lote_id,esta) VALUES(?,?,?)", (inv, lote_id, esta))
        revisados += 1
        if not esta:
            cerrados += 1
            cerrar_saldo(con, lote_id, lugar, inv, persona)
            con.execute("UPDATE lotes SET principal_vacio=1 WHERE id=?" if lugar == "principal"
                        else "UPDATE lotes SET en_secundaria=0 WHERE id=?", (lote_id,))
    con.execute("UPDATE inventarios SET revisados=?, cerrados=? WHERE id=?", (revisados, cerrados, inv))
    anotar(con, persona, "INVENTARIO", "Inventario de %s: %d lotes revisados, %d ya no estaban" % (LUGARES_INVENTARIO[lugar], revisados, cerrados))
    con.commit()
    return jsonify(id=inv, revisados=revisados, cerrados=cerrados)


@app.post("/api/inventarios/lotes/<int:il_id>/reabrir")
def api_inventario_reabrir(il_id):
    """Un lote se marco por error como «ya no está»: vuelve a la bodega."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    fila = con.execute("""SELECT il.*, inv.lugar, l.lote FROM inventario_lotes il JOIN inventarios inv ON inv.id=il.inventario_id
                          JOIN lotes l ON l.id=il.lote_id WHERE il.id=?""", (il_id,)).fetchone()
    if not fila or fila["esta"]:
        raise ErrorApi("Ese lote no se cerró en el inventario.", 404)
    if fila["reabierto_en"]:
        raise ErrorApi("Ese lote ya se había vuelto a poner.")
    con.execute("UPDATE lotes SET principal_vacio=0 WHERE id=?" if fila["lugar"] == "principal"
                else "UPDATE lotes SET en_secundaria=1 WHERE id=?", (fila["lote_id"],))
    motivo = MOTIVO_CIERRE % fila["inventario_id"]
    if fila["lugar"] == "principal":
        con.execute("UPDATE salidas_areas SET anulado=1, motivo_anulacion='Se marcó por error en el inventario' "
                    "WHERE lote_id=? AND area=? AND motivo=? AND anulado=0", (fila["lote_id"], AREA_CIERRE, motivo))
    else:
        con.execute("UPDATE bajas SET anulado=1, motivo_anulacion='Se marcó por error en el inventario' "
                    "WHERE lote_id=? AND lugar='secundaria' AND motivo=? AND anulado=0", (fila["lote_id"], motivo))
    con.execute("UPDATE inventario_lotes SET reabierto_en=?, reabierto_por=? WHERE id=?", (ahora(), persona, il_id))
    anotar(con, persona, "INVENTARIO_REABRIR", "Lote %s vuelve a %s" % (fila["lote"], LUGARES_INVENTARIO[fila["lugar"]]), fila["lote_id"])
    con.commit()
    return jsonify(ok=True)


@app.get("/api/bodega/lotes")
def api_bodega_lotes():
    """Lotes que todavia tienen saldo en bodega principal (primero los caducados y los que caducan antes)
    y las ultimas salidas a otras areas."""
    con = db()
    lotes = listar_lotes(con, "principal", limite=500)
    lotes.sort(key=lambda l: (ORDEN_SEMAFORO[l["semaforo"]], l["caducidad"] or "9999", l["ingrediente"], l["id"]))
    salidas = filas(con.execute(SALIDAS_SQL + " ORDER BY o.id DESC LIMIT 40"))
    return jsonify(lotes=lotes, salidas=salidas, areas=AREAS_DESTINO, dias_por_caducar=DIAS_POR_CADUCAR)


def lote_de_principal(con, lote_id):
    lote = un_lote(con, lote_id)
    if lote["origen"] == "interno" or lote["agotado"] or lote["principal_vacio"]:
        raise ErrorApi("El lote %s ya no está en bodega principal." % lote["lote"])
    return lote


@app.post("/api/bodega/lotes/<int:lote_id>/terminar")
def api_lote_terminar(lote_id):
    """'Se terminó': cierra en bodega principal un lote cuyas cuentas no cuadran. Lo que quedaba se anota como ajuste."""
    d = cuerpo()
    persona = persona_de(d)
    motivo = " ".join(str(d.get("motivo") or "").split())
    if len(motivo) < 3:
        raise ErrorApi("Escribe el motivo (por ejemplo: merma, se rompió una funda, salida que no se anotó).")
    con = db()
    lote = lote_de_principal(con, lote_id)
    unidad = lote["unidad"] or ""
    sobrante = max(lote["disponible"] or 0, 0)
    con.execute("INSERT INTO salidas_areas(lote_id,cantidad,unidad,area,motivo,fecha_hora,persona) VALUES(?,?,?,?,?,?,?)",
                (lote["id"], sobrante, unidad, AREA_AJUSTE, motivo, ahora(), persona))
    con.execute("UPDATE lotes SET principal_vacio=1 WHERE id=?", (lote["id"],))
    anotar(con, persona, "LOTE_TERMINADO_PRINCIPAL", "%s lote %s cerrado en bodega principal%s. Motivo: %s" % (
        lote["ingrediente"], lote["lote"],
        " con %s %s sin registrar" % (fmt(sobrante), unidad) if sobrante else "", motivo), lote["id"])
    con.commit()
    return jsonify(ok=True)


@app.post("/api/bodega/salidas/<int:salida_id>/anular")
def api_salida_anular(salida_id):
    """Deshace una salida (o un cierre) hecho por error: el lote vuelve a tener ese saldo en bodega principal."""
    d = cuerpo()
    persona = persona_de(d)
    motivo = " ".join(str(d.get("motivo") or "").split())
    if len(motivo) < 3:
        raise ErrorApi("Escribe el motivo de la anulación.")
    con = db()
    salida = con.execute("SELECT * FROM salidas_areas WHERE id=?", (salida_id,)).fetchone()
    if not salida:
        raise ErrorApi("Esa salida no existe.", 404)
    if salida["anulado"]:
        raise ErrorApi("Esa salida ya estaba anulada.")
    con.execute("UPDATE salidas_areas SET anulado=1, motivo_anulacion=? WHERE id=?", (motivo, salida_id))
    ajustar_cierres(con, con.execute("SELECT lote_id FROM salidas_areas WHERE id=?", (salida_id,)).fetchone()[0])
    lote = un_lote(con, salida["lote_id"])
    if lote["disponible"] is None or lote["disponible"] > 1e-9:
        con.execute("UPDATE lotes SET principal_vacio=0 WHERE id=?", (lote["id"],))
    anotar(con, persona, "SALIDA_ANULADA", "%s lote %s: %s %s a %s. Motivo: %s" % (
        lote["ingrediente"], lote["lote"], fmt(salida["cantidad"]), salida["unidad"], salida["area"], motivo), lote["id"])
    con.commit()
    return jsonify(ok=True)


MOTIVOS_BAJA = ("CADUCADO", "CONTAMINADO")
MOTIVO_NO_ESTA = "NO_ESTA"


def baja_no_esta(con, lote_id, lugar, persona, comentario):
    """«Ya no está»: el lote sale de esa bodega sin esperar al inventario del mes. Se guarda como el cierre de un
    inventario de un solo lote (tipo 'suelto'): sale en el Historial y se deshace con «Se marcó por error»."""
    if lugar not in LUGARES_INVENTARIO:
        raise ErrorApi("«Ya no está» se usa en bodega principal o en bodega secundaria.")
    donde = LUGARES_INVENTARIO[lugar]
    lote = un_lote(con, lote_id)
    if lote["id"] not in {l["id"] for l in listar_lotes(con, lugar, limite=5000)}:
        raise ErrorApi("El lote %s ya no está en %s." % (lote["lote"], donde))
    quedaba = max(round(saldo_al(con, lote["id"], lugar), 3), 0)
    inv = con.execute("INSERT INTO inventarios(lugar,fecha_hora,persona,revisados,cerrados,tipo,nota) VALUES(?,?,?,1,1,'suelto',?)",
                      (lugar, ahora(), persona, comentario)).lastrowid
    con.execute("INSERT INTO inventario_lotes(inventario_id,lote_id,esta) VALUES(?,?,0)", (inv, lote["id"]))
    cerrar_saldo(con, lote["id"], lugar, inv, persona)
    con.execute("UPDATE lotes SET principal_vacio=1 WHERE id=?" if lugar == "principal"
                else "UPDATE lotes SET en_secundaria=0 WHERE id=?", (lote["id"],))
    anotar(con, persona, "BAJA", "%s lote %s: ya no está en %s%s%s" % (
        lote["ingrediente"], lote["lote"], donde,
        " (la app contaba %s)" % cantidad_texto(quedaba, lote) if quedaba else "", ": " + comentario if comentario else ""), lote["id"])
    con.commit()
    return jsonify(ok=True, vacio=True, no_esta=True)


def dia_texto(fecha):
    try:
        return dt.date.fromisoformat(str(fecha)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return fecha or "sin fecha"


@app.post("/api/bajas")
def api_baja():
    """Dar de baja un lote (o parte) por caducado o contaminado en bodega principal, bodega secundaria o cocina."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    lugar = d.get("lugar")
    motivo = str(d.get("motivo") or "").upper()
    comentario = " ".join(str(d.get("comentario") or "").split())
    if motivo == MOTIVO_NO_ESTA:
        return baja_no_esta(con, d.get("lote_id"), lugar, persona, comentario)
    if motivo not in MOTIVOS_BAJA:
        raise ErrorApi("Elige el motivo.")
    lote = un_lote(con, d.get("lote_id"))
    unidad = lote["unidad"] or ""
    cantidad = numero(d.get("cantidad")) if str(d.get("cantidad") or "").strip() else None
    if str(d.get("cantidad") or "").strip() and (cantidad is None or cantidad <= 0):
        raise ErrorApi("La cantidad debe ser solo un número.")
    donde = {"principal": "bodega principal", "secundaria": "bodega secundaria", "cocina": "cocina"}.get(lugar)
    if not donde:
        raise ErrorApi("Lugar no válido.")
    if lugar == "principal":
        lote = lote_de_principal(con, lote["id"])
        disponible = lote["disponible"]
        if disponible is not None:
            if cantidad is None:
                raise ErrorApi("Escribe cuánto se da de baja.")
            if cantidad > disponible + 1e-9:
                raise ErrorApi("Del lote %s solo quedan %s %s en bodega principal." % (lote["lote"], fmt(disponible), unidad))
        # Se guarda como salida "BAJA": asi descuenta el saldo y aparece en el historial de bodega principal.
        con.execute("INSERT INTO salidas_areas(lote_id,cantidad,unidad,area,motivo,fecha_hora,persona) VALUES(?,?,?,?,?,?,?)",
                    (lote["id"], cantidad or 0, unidad, "BAJA", motivo + (": " + comentario if comentario else ""), ahora(), persona))
        vacio = disponible is None or cantidad >= disponible - 1e-9
        if vacio:
            con.execute("UPDATE lotes SET principal_vacio=1 WHERE id=?", (lote["id"],))
    elif lugar == "secundaria":
        if not lote["en_secundaria"] or lote["agotado"]:
            raise ErrorApi("El lote %s ya no está en bodega secundaria." % lote["lote"])
        disponible = lote["disponible_cocina"]
        if disponible is not None:
            if cantidad is None:
                raise ErrorApi("Escribe cuánto se da de baja.")
            if cantidad > disponible + 1e-9:
                raise ErrorApi("Del lote %s solo quedan %s %s en bodega secundaria." % (lote["lote"], fmt(disponible), unidad))
        con.execute("INSERT INTO bajas(lote_id,lugar,cantidad,unidad,motivo,comentario,fecha_hora,persona) VALUES(?,?,?,?,?,?,?,?)",
                    (lote["id"], lugar, cantidad, unidad, motivo, comentario, ahora(), persona))
        vacio = disponible is None or cantidad >= disponible - 1e-9
        if vacio:
            con.execute("UPDATE lotes SET en_secundaria=0 WHERE id=?", (lote["id"],))
    else:
        if not con.execute("SELECT 1 FROM aperturas WHERE lote_id=? AND cerrado_en IS NULL", (lote["id"],)).fetchone():
            raise ErrorApi("El lote %s no está abierto en cocina." % lote["lote"])
        con.execute("INSERT INTO bajas(lote_id,lugar,cantidad,unidad,motivo,comentario,fecha_hora,persona) VALUES(?,?,?,?,?,?,?,?)",
                    (lote["id"], lugar, cantidad, unidad, motivo, comentario, ahora(), persona))
        # Sale de cocina: deja de anotarse en los panes. Una subreceta dada de baja ya no se puede volver a abrir.
        con.execute("UPDATE aperturas SET cerrado_en=?, cerrado_por=? WHERE lote_id=? AND cerrado_en IS NULL", (ahora(), persona, lote["id"]))
        if lote["origen"] == "interno":
            con.execute("UPDATE lotes SET agotado=1 WHERE id=?", (lote["id"],))
        vacio = True
    anotar(con, persona, "BAJA", "%s lote %s dado de baja en %s por %s%s%s" % (
        lote["ingrediente"], lote["lote"], donde, motivo.lower(), " (%s %s)" % (fmt(cantidad), unidad) if cantidad else "",
        ": " + comentario if comentario else ""), lote["id"])
    con.commit()
    return jsonify(ok=True, vacio=vacio)


@app.post("/api/bajas/<int:baja_id>/anular")
def api_baja_anular(baja_id):
    """Deshace una baja de bodega secundaria o cocina registrada por error."""
    d = cuerpo()
    persona = persona_de(d)
    motivo = " ".join(str(d.get("motivo") or "").split())
    if len(motivo) < 3:
        raise ErrorApi("Escribe el motivo de la anulación.")
    con = db()
    b = con.execute("SELECT * FROM bajas WHERE id=?", (baja_id,)).fetchone()
    if not b:
        raise ErrorApi("Esa baja no existe.", 404)
    if b["anulado"]:
        raise ErrorApi("Esa baja ya estaba anulada.")
    con.execute("UPDATE bajas SET anulado=1, motivo_anulacion=? WHERE id=?", (motivo, baja_id))
    ajustar_cierres(con, con.execute("SELECT lote_id FROM bajas WHERE id=?", (baja_id,)).fetchone()[0])
    lote = un_lote(con, b["lote_id"])
    if b["lugar"] == "secundaria" and (lote["disponible_cocina"] is None or lote["disponible_cocina"] > 1e-9):
        con.execute("UPDATE lotes SET en_secundaria=1, agotado=0 WHERE id=?", (lote["id"],))
    anotar(con, persona, "BAJA_ANULADA", "%s lote %s: baja en %s anulada. Motivo: %s" % (
        lote["ingrediente"], lote["lote"], b["lugar"], motivo), lote["id"])
    con.commit()
    return jsonify(ok=True)


@app.get("/api/bodega/sugerir")
def api_sugerir():
    return jsonify(ingrediente_id=sugerir_ingrediente(db(), normalizar(request.args.get("producto") or "")))


@app.get("/api/recepciones")
def api_recepciones():
    return jsonify(recepciones_del_mes(db(), mes_pedido()))


@app.post("/api/recepciones")
def api_recepcion_nueva():
    d = cuerpo()
    persona = persona_de(d)
    producto = " ".join(str(d.get("producto") or "").split())
    if not producto:
        raise ErrorApi("Elige el producto de la lista.")
    v = validar_recepcion(d)
    con = db()
    alias = con.execute("SELECT * FROM alias WHERE en_catalogo=1 AND producto=?", (producto,)).fetchone()
    if not alias:
        raise ErrorApi("«%s» no está en la lista de productos. Elígelo de la lista o agrégalo con «Agregar producto nuevo»." % producto)
    alias_id = alias["id"]
    if not alias["empaque"]:
        raise ErrorApi("«%s» no tiene empaque. Defínelo en Administrar > Productos de bodega > Empaque." % producto)
    if alias["ignorar"] or not alias["ingrediente_id"]:
        # Todo lo que llega a bodega principal se ve en su inventario: el producto recibe su propio ingrediente.
        con.execute("UPDATE alias SET ignorar=0 WHERE id=?", (alias_id,))
        ingrediente_propio(con, alias_id)
        alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    v["unidad"] = alias["empaque"]
    v["cantidad"] = str(entero_paquetes(v["cantidad"], alias["empaque"], "la cantidad"))
    previo = con.execute("SELECT origen FROM lotes WHERE ingrediente_id=? AND lote=?",
                         (alias["ingrediente_id"], v["lote"])).fetchone() if alias["ingrediente_id"] else None
    cur = con.execute(
        """INSERT INTO recepciones(fila_hash,parte,alias_id,lote_texto,texto_original,fecha_ingreso,proveedor,cantidad,
           caducidad,unidad,apariencia,transporte,aprobado_por,observaciones,rechazado,origen,registrado_por,registrado_en,
           importado_en) VALUES(?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,'app',?,?,?)""",
        ("app-" + uuid.uuid4().hex, alias_id, v["lote"], v["lote"], v["fecha"], v["proveedor"], v["cantidad"],
         v["caducidad"], v["unidad"], v["apariencia"], v["transporte"], v["aprobado_por"], v["observaciones"],
         v["rechazado"], persona, ahora(), ahora()))
    materializar(con)
    rec = recepcion(con, cur.lastrowid)
    if rec["lote_id"]:
        ajustar_cierres(con, rec["lote_id"])
        l = un_lote(con, rec["lote_id"])
        if l.get("disponible") is not None and l["disponible"] < 0:
            con.execute("UPDATE lotes SET revisar=1, nota=TRIM(COALESCE(nota,'') || ' ' || ?) WHERE id=?",
                        ("Bodega principal registró menos de lo que ya había pasado a bodega secundaria: revisar cantidades.", l["id"]))
        if l.get("disponible") is not None and l["disponible"] <= 0:
            con.execute("UPDATE lotes SET principal_vacio=1 WHERE id=?", (l["id"],))     # todo ya paso a bodega secundaria
    if d.get("aviso_id"):        # se registro desde el aviso de un lote anotado a mano
        if rec["lote_id"] and (un_lote(con, rec["lote_id"]).get("disponible") or 0) >= 0:
            con.execute("UPDATE lotes SET revisar=0 WHERE id=?", (rec["lote_id"],))
        con.execute("UPDATE avisos SET resuelto_en=?, resuelto_por=?, comentario=? WHERE id=? AND resuelto_en IS NULL",
                    (ahora(), persona, "Se registró la recepción (%s %s en total)." % (v["cantidad"], v["unidad"].lower()), int(d["aviso_id"])))
    anotar(con, persona, "RECEPCION_BODEGA", "%s lote %s · %s %s%s" % (
        producto, v["lote"], v["cantidad"], v["unidad"], " · RECHAZADO" if v["rechazado"] else ""), rec["lote_id"])
    con.commit()
    if v["rechazado"]:
        rec["aviso"] = "Registrado como rechazado: este lote no pasa a la panadería."
    elif alias["ignorar"]:
        rec["aviso"] = ""
    elif not alias["ingrediente_id"]:
        rec["aviso"] = ""      # Produccion lo asigna en Administrar → Pendientes
    elif previo and previo["origen"] == "manual":
        rec["aviso"] = "Cocina había anotado este lote a mano; quedó confirmado."
    elif previo:
        rec["aviso"] = "Este lote ya estaba registrado; se sumó como otra entrega del mismo lote."
    else:
        rec["aviso"] = ""
    return jsonify(rec)


CAMPOS_RECEPCION = [("fecha", "fecha_ingreso", "fecha"), ("proveedor", "proveedor", "proveedor"), ("lote", "lote_texto", "lote"),
                    ("cantidad", "cantidad", "cantidad"), ("unidad", "unidad", "unidad"), ("caducidad", "caducidad", "caducidad"),
                    ("apariencia", "apariencia", "apariencia"), ("transporte", "transporte", "transporte"),
                    ("aprobado_por", "aprobado_por", "aprobado por"), ("observaciones", "observaciones", "observaciones"),
                    ("rechazado", "rechazado", "rechazado")]


@app.post("/api/recepciones/<int:rid>/editar")
def api_recepcion_editar(rid):
    d = cuerpo()
    persona = persona_de(d)
    motivo = str(d.get("motivo") or "").strip()
    con = db()
    rec = recepcion(con, rid)
    if rec["anulado"]:
        raise ErrorApi("Esa recepción está anulada.")
    v = validar_recepcion(d, estricto=rec["origen"] == "app")
    emp = con.execute("SELECT empaque FROM alias WHERE id=? AND COALESCE(empaque,'')<>''", (rec["alias_id"],)).fetchone()
    if emp:
        v["unidad"] = emp["empaque"]
        if rec["origen"] == "app" or numero(v["cantidad"]) != numero(rec["cantidad"]):
            v["cantidad"] = str(entero_paquetes(v["cantidad"], emp["empaque"], "la cantidad"))
    else:
        comprobar_unidad(con, v["unidad"], rec["unidad"])
    cambiados = [k for k, col, nombre in CAMPOS_RECEPCION if str(rec[col] or "") != str(v[k] or "")]
    cambios = ["%s: «%s» → «%s»" % (nombre, rec[col] or "", v[k]) for k, col, nombre in CAMPOS_RECEPCION if k in cambiados]
    if not cambios:
        raise ErrorApi("No cambiaste nada.")
    if not motivo and cambiados == ["caducidad"] and not rec["caducidad"]:
        motivo = "Se agregó la fecha de caducidad"      # solo se completo un dato que faltaba
    if not motivo:
        raise ErrorApi("Escribe el motivo de la corrección.")
    if v["lote"] != rec["lote_texto"] or (v["rechazado"] and not rec["rechazado"]):
        desvincular(con, rec)
    con.execute(
        """UPDATE recepciones SET fecha_ingreso=?, proveedor=?, lote_texto=?, cantidad=?, unidad=?, caducidad=?, apariencia=?,
           transporte=?, aprobado_por=?, observaciones=?, rechazado=?,
           texto_original=CASE WHEN origen='app' THEN ? ELSE texto_original END WHERE id=?""",
        (v["fecha"], v["proveedor"], v["lote"], v["cantidad"], v["unidad"], v["caducidad"], v["apariencia"], v["transporte"],
         v["aprobado_por"], v["observaciones"], v["rechazado"], v["lote"], rid))
    materializar(con)
    for lid in {rec["lote_id"], recepcion(con, rid)["lote_id"]}:
        if lid and con.execute("SELECT 1 FROM lotes WHERE id=?", (lid,)).fetchone():
            ajustar_cierres(con, lid)
    anotar(con, persona, "RECEPCION_CORREGIDA", "%s lote %s. %s. Motivo: %s" % (
        rec["producto"], v["lote"], "; ".join(cambios), motivo), recepcion(con, rid)["lote_id"])
    con.commit()
    return jsonify(recepcion(con, rid))


@app.get("/api/lotes/<int:lote_id>/recepciones")
def api_recepciones_de_lote(lote_id):
    """Las recepciones de bodega principal de un lote (para corregirlas desde el semaforo o el inventario)."""
    con = db()
    todas = filas(con.execute(RECEPCION_SQL + " WHERE r.lote_id=? AND r.anulado=0 ORDER BY r.fecha_ingreso DESC, r.id DESC", (lote_id,)))
    cierre = ultimo_cierre(con, lote_id)
    if cierre:      # solo las entregas que llegaron despues de que el lote se cerro en un inventario
        nuevas = [r for r in todas if (r.get("registrado_en") or r.get("importado_en") or r.get("fecha_ingreso") or "") >= cierre]
        todas = nuevas or todas
    return jsonify(todas)


FOTOS_RECEPCIONES = os.path.join(FOTOS, "recepciones")
TIPOS_FOTO_RECEPCION = {"lote": ("foto_lote", "etiqueta del lote"), "requisicion": ("foto_requisicion", "requisición")}


@app.post("/api/recepciones/<int:rid>/foto")
def api_recepcion_foto(rid):
    """Foto de la etiqueta del lote o de la requisicion del proveedor (se guarda liviana pero legible)."""
    from PIL import Image, ImageOps
    tipo = request.args.get("tipo", "")
    if tipo not in TIPOS_FOTO_RECEPCION:
        raise ErrorApi("Tipo de foto no válido.")
    con = db()
    rec = recepcion(con, rid)
    try:
        imagen = ImageOps.exif_transpose(Image.open(io.BytesIO(request.get_data())))
    except Exception:
        raise ErrorApi("No se pudo leer la foto. Usa una imagen JPG o PNG.")
    if imagen.mode in ("RGBA", "LA", "P"):          # fondo blanco donde la imagen es transparente
        imagen = imagen.convert("RGBA")
        fondo = Image.new("RGB", imagen.size, (255, 255, 255))
        fondo.paste(imagen, mask=imagen.getchannel("A"))
        imagen = fondo
    imagen = imagen.convert("RGB")
    imagen.thumbnail((1800, 1800))
    os.makedirs(FOTOS_RECEPCIONES, exist_ok=True)
    nombre = "rec_%d_%s_%s.jpg" % (rid, tipo, uuid.uuid4().hex[:6])
    imagen.save(os.path.join(FOTOS_RECEPCIONES, nombre), "JPEG", quality=82, optimize=True)
    col, texto = TIPOS_FOTO_RECEPCION[tipo]
    con.execute("UPDATE recepciones SET %s=? WHERE id=?" % col, ("recepciones/" + nombre, rid))
    anotar(con, persona_de(request.args), "FOTO_RECEPCION", "%s lote %s: foto de la %s" % (rec["producto"], rec["lote_texto"], texto),
           rec["lote_id"])
    con.commit()
    return jsonify(recepcion(con, rid))


@app.post("/api/recepciones/<int:rid>/anular")
def api_recepcion_anular(rid):
    d = cuerpo()
    persona = persona_de(d)
    motivo = str(d.get("motivo") or "").strip()
    if not motivo:
        raise ErrorApi("Escribe el motivo de la anulación.")
    con = db()
    rec = recepcion(con, rid)
    if rec["anulado"]:
        raise ErrorApi("Esa recepción ya estaba anulada.")
    desvincular(con, rec)
    con.execute("UPDATE recepciones SET anulado=1, motivo_anulacion=? WHERE id=?", (motivo, rid))
    if rec["lote_id"] and con.execute("SELECT 1 FROM lotes WHERE id=?", (rec["lote_id"],)).fetchone():
        ajustar_cierres(con, rec["lote_id"])
    anotar(con, persona, "RECEPCION_ANULADA", "%s lote %s. Motivo: %s" % (rec["producto"], rec["lote_texto"], motivo))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/recepciones.xlsx")
def api_recepciones_xlsx():
    """El registro del mes en el mismo formato BPM-SFFS-RRMP001 (sin las filas anuladas)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, Side
    mes = mes_pedido()
    datos = [r for r in recepciones_del_mes(db(), mes) if not r["anulado"]]
    datos.reverse()
    F = FORMATO_RECEPCION
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Recepción " + mes
    hoja.merge_cells("A1:B3")
    hoja["A1"] = F["empresa"] + "\n" + F["subempresa"]
    hoja["A1"].font = Font(bold=True, size=13, color="B23A2E")
    try:   # logo de la empresa (necesita el paquete pillow; si no esta, queda el nombre en texto)
        from openpyxl.drawing.image import Image as ImagenExcel
        logo = ImagenExcel(os.path.join(app.static_folder, "logo.png"))
        logo.width, logo.height = int(logo.width * 52 / logo.height), 52
        hoja.add_image(logo, "A1")
        hoja["A1"] = None
    except Exception:
        pass
    hoja.merge_cells("C1:I1")
    hoja["C1"] = F["procedimiento"]
    hoja.merge_cells("C2:I3")
    hoja["C2"] = F["registro"]
    for celda, valor in (("J1", "CÓDIGO:"), ("K1", F["codigo"]), ("J2", "VERSIÓN:"), ("K2", F["version"]),
                         ("J3", "FECHA ELAB:"), ("K3", F["fecha_elab"])):
        hoja[celda] = valor
    titulos = ["FECHA INGRESO", "PRODUCTO", "PROVEEDOR", "LOTE", "CANTIDAD", "UNIDAD DE MEDIDA",
               "APARIENCIA VISUAL PRODUCTO*", "CONDICIONES DE TRANSPORTE**", "APROBADO POR", "OBSERVACIONES", "",
               "FECHA DE CADUCIDAD"]
    for c, t in enumerate(titulos, 1):
        hoja.cell(row=5, column=c, value=t)
    hoja.merge_cells("J5:K5")
    n = 5
    for r in datos:
        n += 1
        try:
            fecha = dt.date.fromisoformat(r["fecha_ingreso"])
        except (TypeError, ValueError):
            fecha = r["fecha_ingreso"]
        cantidad = r["cantidad"]
        if re.fullmatch(r"\d+([.,]\d+)?", cantidad or ""):
            cantidad = float(cantidad.replace(",", "."))
            cantidad = int(cantidad) if cantidad == int(cantidad) else cantidad
        try:
            caducidad = dt.date.fromisoformat(r["caducidad"]) if r["caducidad"] else None
        except ValueError:
            caducidad = r["caducidad"]
        obs = ("RECHAZADO. " if r["rechazado"] else "") + (r["observaciones"] or "")
        valores = [fecha, r["producto"], r["proveedor"], r["lote_texto"], cantidad, r["unidad"], r["apariencia"],
                   r["transporte"], r["aprobado_por"], obs, None, caducidad]
        for c, valor in enumerate(valores, 1):
            hoja.cell(row=n, column=c, value=valor)
        hoja.merge_cells(start_row=n, start_column=10, end_row=n, end_column=11)
        hoja.cell(row=n, column=1).number_format = "mm-dd-yy"     # fecha corta segun la configuracion de Windows
        hoja.cell(row=n, column=12).number_format = "mm-dd-yy"
        hoja.cell(row=n, column=4).number_format = "@"
    fino = Side(style="thin", color="000000")
    for fila in hoja.iter_rows(min_row=1, max_row=max(n, 6), min_col=1, max_col=12):
        for celda in fila:
            if celda.row == 4:
                continue
            celda.border = Border(left=fino, right=fino, top=fino, bottom=fino)
            celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for c, ancho in enumerate([12, 38, 30, 18, 11, 13, 13, 15, 11, 18, 18, 13], 1):
        hoja.column_dimensions[openpyxl.utils.get_column_letter(c)].width = ancho
    hoja.row_dimensions[5].height = 45
    salida = io.BytesIO()
    libro.save(salida)
    salida.seek(0)
    return send_file(salida, as_attachment=True, download_name="Registro_recepcion_%s.xlsx" % mes,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----------------------------------------------------------------------------
# 4. Lotes: bodega secundaria y cocina
# ----------------------------------------------------------------------------
LOTE_SQL = """
SELECT l.id, l.lote, l.origen, l.en_secundaria, l.agotado, l.revisar, l.nota, l.foto, l.creado_en, l.creado_por,
       l.ingrediente_id, i.nombre AS ingrediente, i.tipo, i.refrigerado,
       (SELECT r.proveedor FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS proveedor,
       (SELECT MAX(r.fecha_ingreso) FROM recepciones r WHERE r.lote_id=l.id) AS fecha_ingreso,
       COALESCE((SELECT r.caducidad FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1), l.caducidad) AS caducidad,
       (SELECT r.cantidad FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS cantidad,
       (SELECT a.producto FROM recepciones r JOIN alias a ON a.id=r.alias_id WHERE r.lote_id=l.id
        ORDER BY r.fecha_ingreso DESC LIMIT 1) AS producto_bodega,
       (SELECT ap.abierto_en FROM aperturas ap WHERE ap.lote_id=l.id AND ap.cerrado_en IS NULL LIMIT 1) AS abierto_en,
       (SELECT ap.abierto_por FROM aperturas ap WHERE ap.lote_id=l.id AND ap.cerrado_en IS NULL LIMIT 1) AS abierto_por,
       (SELECT COALESCE(SUM(t.cantidad), 0) FROM traslados t WHERE t.lote_id=l.id AND t.anulado=0) AS trasladado,
       (SELECT COUNT(*) FROM traslados t WHERE t.lote_id=l.id AND t.anulado=0) AS n_traslados,
       (SELECT COALESCE(SUM(s.cantidad), 0) FROM salidas_cocina s WHERE s.lote_id=l.id AND s.anulado=0) AS a_cocina,
       (SELECT GROUP_CONCAT(r.cantidad, '|') FROM recepciones r WHERE r.lote_id=l.id) AS cantidades,
       COALESCE((SELECT r.unidad FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC, r.id DESC LIMIT 1),
                (SELECT t.unidad FROM traslados t WHERE t.lote_id=l.id AND t.anulado=0 ORDER BY t.id DESC LIMIT 1)) AS unidad,
       (SELECT r.foto_lote FROM recepciones r WHERE r.lote_id=l.id AND r.foto_lote IS NOT NULL AND r.anulado=0
        ORDER BY r.fecha_ingreso DESC, r.id DESC LIMIT 1) AS foto_recepcion,
       COALESCE((SELECT a.empaque || '|' || a.contenido || '|' || a.unidad_contenido FROM recepciones r JOIN alias a ON a.id=r.alias_id
        WHERE r.lote_id=l.id AND COALESCE(a.empaque,'')<>'' ORDER BY r.fecha_ingreso DESC, r.id DESC LIMIT 1),
                (SELECT a.empaque || '|' || a.contenido || '|' || a.unidad_contenido FROM alias a
        WHERE a.ingrediente_id=l.ingrediente_id AND a.en_catalogo=1 AND COALESCE(a.empaque,'')<>'' ORDER BY a.id LIMIT 1)) AS empaque_txt,
       (SELECT COALESCE(SUM(o.cantidad), 0) FROM salidas_areas o WHERE o.lote_id=l.id AND o.anulado=0) AS otras_areas,
       (SELECT COALESCE(SUM(b.cantidad), 0) FROM bajas b WHERE b.lote_id=l.id AND b.lugar='secundaria' AND b.anulado=0) AS bajas_secundaria,
       l.principal_vacio, i.recipientes AS n_recipientes
FROM lotes l JOIN ingredientes i ON i.id=l.ingrediente_id
"""
ORDEN_LOTES = " ORDER BY COALESCE(fecha_ingreso, substr(l.creado_en,1,10)) DESC, l.id DESC"

FILTROS = {
    # En bodega principal y todavia no pasados a bodega secundaria.
    "principal": "l.origen<>'interno' AND l.agotado=0 AND l.principal_vacio=0",
    # En bodega secundaria.
    "secundaria": "l.en_secundaria=1 AND l.agotado=0",
    # Lo que cocina puede abrir: lo que hay en bodega secundaria (secos y refrigerados), subrecetas y lo ya abierto.
    "abribles": """l.agotado=0 AND (l.en_secundaria=1 OR l.origen='interno'
                   OR EXISTS(SELECT 1 FROM aperturas ap WHERE ap.lote_id=l.id AND ap.cerrado_en IS NULL))""",
    # Abiertos ahora en cocina.
    "abiertos": "EXISTS(SELECT 1 FROM aperturas ap WHERE ap.lote_id=l.id AND ap.cerrado_en IS NULL)",
    "todos": "1=1",
}


# Semaforo de caducidad: rojo = caducado, amarillo = caduca en DIAS_POR_CADUCAR dias o menos, verde = mas tiempo.
DIAS_POR_CADUCAR = 30
# Areas a las que bodega principal despacha ademas de panaderia (panaderia se registra en bodega secundaria).
AREAS_DESTINO = ["PASTELERIA", "CARNICERIA"]
AREA_AJUSTE = "AJUSTE"        # cierre de un lote con motivo (merma, salida que no se anoto, diferencia de medida)


# Subrecetas (relleno, glace, masa madre): duran pocos dias, se avisa cuando les quedan DIAS_POR_CADUCAR_SUB o menos.
DIAS_SUBRECETA = 7
DIAS_POR_CADUCAR_SUB = 2


def semaforo_caducidad(caducidad, subreceta=False):
    try:
        dias = (dt.date.fromisoformat(str(caducidad or "")[:10]) - dt.date.today()).days
    except ValueError:
        return {"semaforo": None, "dias_caducidad": None}
    limite = DIAS_POR_CADUCAR_SUB if subreceta else DIAS_POR_CADUCAR
    return {"semaforo": "rojo" if dias < 0 else "amarillo" if dias <= limite else "verde", "dias_caducidad": dias}


def con_saldo(lote):
    """Agrega recibido (suma de las recepciones), trasladado y disponible en bodega principal.
    Si alguna cantidad no es un numero simple, recibido y disponible quedan en None (desconocidos)."""
    partes = [p for p in (lote.pop("cantidades", None) or "").split("|") if p.strip()]
    numeros = [numero(p) for p in partes]
    lote["recibido"] = round(sum(numeros), 3) if partes and None not in numeros else None
    lote["trasladado"] = round(lote.get("trasladado") or 0, 3)
    lote["a_cocina"] = round(lote.get("a_cocina") or 0, 3)
    lote["otras_areas"] = round(lote.get("otras_areas") or 0, 3)
    # Lo que queda en bodega principal: recibido - lo que paso a panaderia - lo despachado a otras areas.
    lote["disponible"] = (round(lote["recibido"] - lote["trasladado"] - lote["otras_areas"], 3)
                          if lote["recibido"] is not None else None)
    lote.update(semaforo_caducidad(lote.get("caducidad"), lote.get("origen") == "interno"))
    partes_emp = (lote.pop("empaque_txt", None) or "").split("|")
    if len(partes_emp) == 3 and lote.get("unidad") == partes_emp[0]:
        lote["empaque"], lote["contenido"], lote["unidad_contenido"] = partes_emp[0], float(partes_emp[1]), partes_emp[2]
    else:
        lote["empaque"] = lote["contenido"] = lote["unidad_contenido"] = None
    # Lo que cocina puede tomar de bodega secundaria (secos y refrigerados).
    # None = no se conoce (lotes pasados antes de registrar cantidades o anotados a mano): no se pone limite.
    if lote.get("origen") == "interno":
        lote["disponible_cocina"] = None
    else:
        lote["disponible_cocina"] = (round(lote["trasladado"] - lote["a_cocina"] - (lote.get("bajas_secundaria") or 0), 3)
                                     if lote.get("n_traslados") else None)
    return lote


def listar_lotes(con, donde, ingrediente_id=None, limite=300):
    sql = LOTE_SQL + " WHERE " + FILTROS[donde]
    args = []
    if ingrediente_id:
        sql += " AND l.ingrediente_id=?"
        args.append(ingrediente_id)
    return [con_saldo(f) for f in filas(con.execute(sql + ORDEN_LOTES + " LIMIT ?", args + [limite]))]


def un_lote(con, lote_id):
    fila = con.execute(LOTE_SQL + " WHERE l.id=?", (lote_id,)).fetchone()
    if not fila:
        raise ErrorApi("Ese lote no existe.", 404)
    return con_saldo(dict(fila))


@app.get("/api/ingredientes")
def api_ingredientes():
    con = db()
    datos = filas(con.execute("SELECT * FROM ingredientes ORDER BY nombre"))
    for donde in ("principal", "secundaria", "abribles", "abiertos"):
        cuenta = {f["ingrediente_id"]: f["n"] for f in con.execute(
            "SELECT l.ingrediente_id, COUNT(*) n FROM lotes l JOIN ingredientes i ON i.id=l.ingrediente_id WHERE "
            + FILTROS[donde] + " GROUP BY l.ingrediente_id")}
        for ing in datos:
            ing["n_" + donde] = cuenta.get(ing["id"], 0)
    fotos = fotos_por_ingrediente(con)
    # Un ingrediente de proveedor sigue activo mientras su producto este en la lista de bodega principal
    # (si se elimina el producto, deja de aparecer en bodega secundaria, cocina y recetas).
    activos = {f[0] for f in con.execute("SELECT DISTINCT ingrediente_id FROM alias WHERE en_catalogo=1 AND ignorar=0 AND ingrediente_id IS NOT NULL")}
    empaques = {f["ingrediente_id"]: dict(f) for f in con.execute(
        "SELECT ingrediente_id, empaque, contenido, unidad_contenido FROM alias WHERE en_catalogo=1 AND COALESCE(empaque,'')<>'' "
        "AND ingrediente_id IS NOT NULL ORDER BY id")}
    for ing in datos:
        ing["foto"] = fotos.get(ing["id"])
        ing["activo"] = ing["tipo"] != "lote" or ing["id"] in activos
        e = empaques.get(ing["id"]) or {}
        ing["empaque"], ing["contenido"], ing["unidad_contenido"] = e.get("empaque"), e.get("contenido"), e.get("unidad_contenido")
    return jsonify(datos)


@app.get("/api/lotes")
def api_lotes():
    donde = request.args.get("donde", "todos")
    if donde not in FILTROS:
        raise ErrorApi("Filtro desconocido.")
    return jsonify(listar_lotes(db(), donde, request.args.get("ingrediente_id", type=int)))


@app.post("/api/secundaria/ingreso")
def api_ingreso_secundaria():
    """Registra cuanto de cada lote pasa de bodega principal a bodega secundaria."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    items = datos.get("items") or []
    if not items:
        raise ErrorApi("Elige al menos un lote.")
    for item in items:
        lote = un_lote(con, item.get("lote_id"))
        if lote["agotado"]:
            raise ErrorApi("El lote %s está marcado como agotado." % lote["lote"])
        cantidad = numero(item.get("cantidad"))
        if cantidad is None or cantidad <= 0:
            raise ErrorApi("Escribe cuánto del lote %s pasa a bodega secundaria (solo el número)." % lote["lote"])
        if lote["empaque"]:
            cantidad = entero_paquetes(item.get("cantidad"), lote["empaque"], "lo que pasa del lote %s" % lote["lote"])
        unidad = lote["unidad"] or ""
        if lote["disponible"] is not None and cantidad > lote["disponible"] + 1e-9:
            raise ErrorApi("Del lote %s solo quedan %s en bodega principal. Revisa con bodega principal si se registró bien."
                           % (lote["lote"], cantidad_texto(lote["disponible"], lote)))
        # Sale de la lista de bodega principal si ya no queda nada (o si lo indican cuando no se conoce lo recibido).
        vacio = bool(item.get("vacio")) or (lote["disponible"] is not None and cantidad >= lote["disponible"] - 1e-9)
        con.execute("INSERT INTO traslados(lote_id,cantidad,unidad,fecha_hora,persona) VALUES(?,?,?,?,?)",
                    (lote["id"], cantidad, unidad, ahora(), persona))
        con.execute("UPDATE lotes SET en_secundaria=1, principal_vacio=? WHERE id=?", (1 if vacio else 0, lote["id"]))
        anotar(con, persona, "INGRESO_SECUNDARIA", "%s lote %s: %s %s%s" % (
            lote["ingrediente"], lote["lote"], fmt(cantidad), unidad,
            " (ya no queda en bodega principal)" if vacio else ""), lote["id"])
    con.commit()
    return jsonify(ok=True, registrados=len(items))


@app.post("/api/secundaria/devolver")
def api_devolver():
    """Deshace un ingreso a bodega secundaria hecho por error."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    lote = un_lote(con, datos.get("lote_id"))
    if lote["abierto_en"] or con.execute("SELECT 1 FROM salidas_cocina WHERE lote_id=?", (lote["id"],)).fetchone():
        raise ErrorApi("Cocina ya tomó del lote %s, así que no fue un registro por error. Si hay que sacarlo, elige Caducado o Contaminado." % lote["lote"])
    con.execute("UPDATE traslados SET anulado=1 WHERE lote_id=? AND anulado=0", (lote["id"],))
    ajustar_cierres(con, lote["id"])
    con.execute("UPDATE lotes SET en_secundaria=0, principal_vacio=0 WHERE id=?", (lote["id"],))
    anotar(con, persona, "INGRESO_ANULADO", "%s lote %s" % (lote["ingrediente"], lote["lote"]), lote["id"])
    con.commit()
    return jsonify(ok=True)


def cerrar_apertura(con, lote, persona, agotado):
    con.execute("UPDATE aperturas SET cerrado_en=?, cerrado_por=? WHERE lote_id=? AND cerrado_en IS NULL",
                (ahora(), persona, lote["id"]))
    if agotado:
        # Se acabo en la panaderia. Si bodega principal aun tiene de este lote, sigue disponible alli.
        queda_en_principal = (lote["tipo"] == "lote" and lote["origen"] != "interno"
                              and not lote.get("principal_vacio"))
        con.execute("UPDATE lotes SET agotado=?, en_secundaria=0 WHERE id=?", (0 if queda_en_principal else 1, lote["id"]))
    anotar(con, persona, "LOTE_AGOTADO" if agotado else "CIERRE_COCINA",
           "%s lote %s" % (lote["ingrediente"], lote["lote"]), lote["id"])


@app.post("/api/lotes/agotar")
def api_agotar():
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    lote = un_lote(con, datos.get("lote_id"))
    cerrar_apertura(con, lote, persona, True)
    con.commit()
    return jsonify(ok=True)


@app.post("/api/lotes/reactivar")
def api_reactivar():
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    lote = un_lote(con, datos.get("lote_id"))
    con.execute("UPDATE lotes SET agotado=0 WHERE id=?", (lote["id"],))
    anotar(con, persona, "LOTE_REACTIVADO", "%s lote %s" % (lote["ingrediente"], lote["lote"]), lote["id"])
    con.commit()
    return jsonify(ok=True)


MAX_LOTES_RECIPIENTE = 3     # mas lotes mezclados en un recipiente: la app avisa


def abrir_lote(con, lote, persona, recipiente=1):
    """Pone un lote en un recipiente de cocina (o abre su empaque) y lo anota en las producciones en curso."""
    if con.execute("SELECT 1 FROM aperturas WHERE lote_id=? AND recipiente=? AND cerrado_en IS NULL",
                   (lote["id"], recipiente)).fetchone():
        return False
    if lote["agotado"]:
        raise ErrorApi("El lote %s está marcado como agotado." % lote["lote"])
    if lote["tipo"] == "lote" and not lote["en_secundaria"]:
        raise ErrorApi("El lote %s todavía no se registró en bodega secundaria." % lote["lote"])
    momento = ahora()
    con.execute("INSERT INTO aperturas(lote_id,abierto_en,abierto_por,recipiente) VALUES(?,?,?,?)",
                (lote["id"], momento, persona, recipiente))
    anotar(con, persona, "APERTURA_COCINA", "%s lote %s%s" % (lote["ingrediente"], lote["lote"],
           " (recipiente %d)" % recipiente if (lote.get("n_recipientes") or 0) > 1 else ""), lote["id"])
    lineas = con.execute(
        """SELECT li.id, li.produccion_id FROM produccion_lineas li JOIN producciones p ON p.id=li.produccion_id
           WHERE p.estado='en_curso' AND li.ingrediente_id=?
             AND ((p.masa_lista_en IS NULL AND COALESCE(li.despues_reposo,0)=0)
                  OR (p.masa_lista_en IS NOT NULL AND li.despues_reposo=1))""", (lote["ingrediente_id"],)).fetchall()
    # Masa en reposo: lo que se abre despues ya no entra en esa masa (solo en los renglones que se agregan al terminar).
    for li in lineas:
        ya = con.execute("SELECT 1 FROM produccion_lotes WHERE linea_id=? AND lote_id=? AND quitado_en IS NULL",
                         (li["id"], lote["id"])).fetchone()
        if not ya:
            con.execute("INSERT INTO produccion_lotes(linea_id,lote_id,origen,agregado_en,agregado_por) VALUES(?,?,?,?,?)",
                        (li["id"], lote["id"], "apertura", momento, persona))
            con.execute("UPDATE produccion_lineas SET no_utilizado=0 WHERE id=?", (li["id"],))
    return True


def recipientes_abiertos(con, ingrediente_id=None):
    """Lo que hay ahora en cada recipiente de cocina: lotes abiertos agrupados por ingrediente y recipiente."""
    filas_ = con.execute(
        """SELECT ap.recipiente, ap.abierto_en, ap.abierto_por, l.id AS lote_id, l.lote, l.origen,
                  COALESCE((SELECT r.caducidad FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1),
                           l.caducidad) AS caducidad,
                  i.id AS ingrediente_id, i.nombre AS ingrediente, i.recipientes, i.tipo
           FROM aperturas ap JOIN lotes l ON l.id=ap.lote_id JOIN ingredientes i ON i.id=l.ingrediente_id
           WHERE ap.cerrado_en IS NULL""" + (" AND i.id=?" if ingrediente_id else "") +
        " ORDER BY i.nombre, ap.recipiente, ap.abierto_en", (ingrediente_id,) if ingrediente_id else ()).fetchall()
    grupos = {}
    for f in filas_:
        g = grupos.setdefault((f["ingrediente_id"], f["recipiente"]), {
            "ingrediente_id": f["ingrediente_id"], "ingrediente": f["ingrediente"], "recipiente": f["recipiente"],
            "n_recipientes": f["recipientes"], "tipo": f["tipo"], "lotes": []})
        g["lotes"].append(dict({"id": f["lote_id"], "lote": f["lote"], "origen": f["origen"], "caducidad": f["caducidad"],
                                "abierto_en": f["abierto_en"], "abierto_por": f["abierto_por"]},
                               **semaforo_caducidad(f["caducidad"], f["origen"] == "interno")))
    for g in grupos.values():
        g["exceso"] = g["n_recipientes"] != 0 and len(g["lotes"]) > MAX_LOTES_RECIPIENTE
    return list(grupos.values())


def anteriores_de(con, puestos):
    """Despues de poner lotes nuevos: lo que ya estaba en esos mismos recipientes (para preguntar si se mezclo)."""
    nuevos = {lote_id for lote_id, _, _ in puestos}
    salida = []
    for ingrediente_id, recipiente in {(i, r) for _, i, r in puestos}:
        for g in recipientes_abiertos(con, ingrediente_id):
            if g["recipiente"] == recipiente:
                g["anteriores"] = [l for l in g["lotes"] if l["id"] not in nuevos]
                g["nuevos"] = [l for l in g["lotes"] if l["id"] in nuevos]
                if g["anteriores"] or g["exceso"]:
                    salida.append(g)
    return salida


@app.get("/api/cocina/recipientes")
def api_recipientes():
    con = db()
    if podar_masa_madre(con, "Cocina"):
        con.commit()
    return jsonify(grupos=recipientes_abiertos(con), maximo=MAX_LOTES_RECIPIENTE)


@app.post("/api/cocina/vaciar")
def api_vaciar():
    """El recipiente quedo vacio (o se termino un empaque): se cierran sus lotes en cocina."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    recipiente = int(datos.get("recipiente") or 1)
    ids = [int(x) for x in datos.get("lote_ids") or []]
    grupo = [g for g in recipientes_abiertos(con, datos.get("ingrediente_id")) if g["recipiente"] == recipiente]
    if not grupo:
        raise ErrorApi("Ese recipiente ya no tiene lotes abiertos.")
    cerrar = [l for l in grupo[0]["lotes"] if not ids or l["id"] in ids]
    terminado = bool(datos.get("terminado"))
    for l in cerrar:
        con.execute("UPDATE aperturas SET cerrado_en=?, cerrado_por=? WHERE lote_id=? AND recipiente=? AND cerrado_en IS NULL",
                    (ahora(), persona, l["id"], recipiente))
        if l["origen"] == "interno":
            # Subreceta: "Se terminó" la cierra; si no, lo que sobro queda guardado para otro dia.
            con.execute("UPDATE lotes SET agotado=? WHERE id=?", (1 if terminado else 0, l["id"]))
            anotar(con, persona, "SUBRECETA_TERMINADA" if terminado else "SUBRECETA_GUARDADA",
                   "%s lote %s%s" % (grupo[0]["ingrediente"], l["lote"], "" if terminado else ": lo que sobró queda guardado"), l["id"])
    g = grupo[0]
    donde = "empaque" if g["n_recipientes"] == 0 else ("recipiente %d" % recipiente if g["n_recipientes"] > 1 else "recipiente")
    anotar(con, persona, "RECIPIENTE_VACIO", "%s (%s): se cierran %s" % (g["ingrediente"], donde, ", ".join(l["lote"] for l in cerrar)))
    con.commit()
    return jsonify(ok=True, cerrados=len(cerrar))


@app.post("/api/cocina/abrir")
def api_abrir():
    """Cocina toma unidades de un lote: lo abre (si no estaba abierto) y descuenta la cantidad."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    items = datos.get("items") or []
    if not items:
        raise ErrorApi("Elige al menos un lote.")
    puestos = []
    for item in items:
        lote = un_lote(con, item.get("lote_id"))
        n_rec = lote["n_recipientes"] or 0
        recipiente = 1
        if n_rec > 1:
            try:
                recipiente = int(item.get("recipiente") or 0)
            except (TypeError, ValueError):
                recipiente = 0
            if not 1 <= recipiente <= n_rec:
                raise ErrorApi("Elige en qué recipiente se pone el lote %s." % lote["lote"])
        cantidad = None
        if lote["tipo"] == "lote":
            cantidad = numero(item.get("cantidad"))
            if cantidad is None or cantidad <= 0:
                raise ErrorApi("Escribe cuánto del lote %s toma cocina (solo el número)." % lote["lote"])
            if lote["empaque"]:
                cantidad = entero_paquetes(item.get("cantidad"), lote["empaque"], "lo que toma cocina del lote %s" % lote["lote"])
            disponible = lote["disponible_cocina"]
            if disponible is not None and cantidad > disponible + 1e-9:
                raise ErrorApi("Del lote %s solo hay %s en bodega secundaria." % (lote["lote"], cantidad_texto(max(disponible, 0), lote)))
        if lote["semaforo"] == "rojo":
            raise ErrorApi("El lote %s de %s está caducado (%s): no se puede usar. Dalo de baja." % (
                lote["lote"], lote["ingrediente"], dia_texto(lote["caducidad"])))
        if abrir_lote(con, lote, persona, recipiente):
            puestos.append((lote["id"], lote["ingrediente_id"], recipiente))
        if cantidad:
            unidad = lote["unidad"] or ""
            con.execute("INSERT INTO salidas_cocina(lote_id,cantidad,unidad,fecha_hora,persona) VALUES(?,?,?,?,?)",
                        (lote["id"], cantidad, unidad, ahora(), persona))
            if disponible is not None and cantidad >= disponible - 1e-9:
                # Se tomo todo lo que habia: el lote ya no esta en bodega secundaria.
                con.execute("UPDATE lotes SET en_secundaria=0 WHERE id=?", (lote["id"],))
            anotar(con, persona, "SALIDA_COCINA", "%s lote %s: %s %s" % (lote["ingrediente"], lote["lote"], fmt(cantidad), unidad),
                   lote["id"])
    con.commit()
    return jsonify(ok=True, anteriores=anteriores_de(con, puestos))


@app.post("/api/cocina/cerrar")
def api_cerrar():
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    lote = un_lote(con, datos.get("lote_id"))
    cerrar_apertura(con, lote, persona, bool(datos.get("agotado")))
    con.commit()
    return jsonify(ok=True)


@app.post("/api/lotes/manual")
def api_lote_manual():
    """'No encuentro el lote en la lista': se anota a mano y queda pendiente de revision."""
    datos = cuerpo()
    persona = persona_de(datos)
    codigo = str(datos.get("lote") or "").strip()
    if not codigo:
        raise ErrorApi("Escribe el código de lote tal como está en el empaque.")
    con = db()
    ing = con.execute("SELECT * FROM ingredientes WHERE id=?", (datos.get("ingrediente_id"),)).fetchone()
    if not ing:
        raise ErrorApi("Elige el ingrediente.")
    # Lo que entra en este momento (asi cocina no puede tomar sin limite) y su caducidad
    emp = con.execute("SELECT empaque, contenido, unidad_contenido FROM alias WHERE ingrediente_id=? AND en_catalogo=1 "
                      "AND COALESCE(empaque,'')<>'' ORDER BY id LIMIT 1", (ing["id"],)).fetchone()
    if emp:
        cantidad = entero_paquetes(datos.get("cantidad"), emp["empaque"], "cuánto entra")
        unidad = emp["empaque"]
    else:
        cantidad, unidad = numero(datos.get("cantidad")), ""
        if not cantidad or cantidad <= 0:
            raise ErrorApi("Escribe cuánto entra (solo el número).")
    caducidad = str(datos.get("caducidad") or "")[:10]
    try:
        dt.date.fromisoformat(caducidad)
    except ValueError:
        raise ErrorApi("Escribe la fecha de caducidad que dice el empaque.")
    existente = con.execute("SELECT id FROM lotes WHERE ingrediente_id=? AND lote=?", (ing["id"], codigo)).fetchone()
    if existente:
        lote_id = existente["id"]
        con.execute("UPDATE lotes SET agotado=0 WHERE id=?", (lote_id,))
    else:
        cur = con.execute(
            "INSERT INTO lotes(ingrediente_id,lote,origen,revisar,nota,creado_en,creado_por) VALUES(?,?,?,?,?,?,?)",
            (ing["id"], codigo, "manual", 1,
             ("No estaba en la lista de bodega principal. " + str(datos.get("nota") or "")).strip(), ahora(), persona))
        lote_id = cur.lastrowid
        anotar(con, persona, "LOTE_MANUAL", "%s lote %s (no estaba en la lista)" % (ing["nombre"], codigo), lote_id)
    con.execute("UPDATE lotes SET en_secundaria=1, principal_vacio=1, revisar=1, caducidad=COALESCE(NULLIF(caducidad,''), ?) WHERE id=?",
                (caducidad, lote_id))
    # Entra a bodega secundaria como un traslado; si lo anota cocina, cocina ya lo tomo.
    con.execute("INSERT INTO traslados(lote_id,cantidad,unidad,fecha_hora,persona) VALUES(?,?,?,?,?)",
                (lote_id, cantidad, unidad, ahora(), persona))
    if datos.get("destino") == "cocina":
        con.execute("INSERT INTO salidas_cocina(lote_id,cantidad,unidad,fecha_hora,persona) VALUES(?,?,?,?,?)",
                    (lote_id, cantidad, unidad, ahora(), persona))
    texto_cant = cantidad_texto(cantidad, {"empaque": emp["empaque"], "contenido": emp["contenido"],
                                           "unidad_contenido": emp["unidad_contenido"]} if emp else {"unidad": unidad})
    donde = "en cocina" if datos.get("destino") == "cocina" else "en bodega secundaria"
    nota = str(datos.get("nota") or "").strip()
    comentario = (" Comentario: «%s»." % nota) if nota else ""
    detalle = "%s, caduca el %s" % (texto_cant, dt.date.fromisoformat(caducidad).strftime("%d/%m/%Y"))
    # Bodega secundaria no lo encontro -> avisa a bodega principal. Cocina no lo encontro -> avisa a bodega secundaria.
    if datos.get("destino") == "cocina":
        avisos = [("produccion", "%s anotó a mano el lote %s de %s (%s) porque no aparecía en bodega secundaria. "
                                 "Registra cuánto entró a bodega secundaria de ese lote.%s"
                   % (persona, codigo, ing["nombre"], detalle, comentario), "manual_cocina")]
    else:
        avisos = [("bodega", "%s anotó a mano el lote %s de %s en bodega secundaria (%s) porque no aparecía en bodega principal. "
                             "Registra su recepción con el mismo código y la cantidad total que llegó.%s"
                   % (persona, codigo, ing["nombre"], detalle, comentario), "manual_secundaria")]
    for area_aviso, texto, tipo in avisos:
        con.execute("INSERT INTO avisos(area,lote_id,texto,creado_en,creado_por,tipo) VALUES(?,?,?,?,?,?)",
                    (area_aviso, lote_id, texto, ahora(), persona, tipo))
    anteriores = []
    if datos.get("destino") == "cocina":
        if abrir_lote(con, un_lote(con, lote_id), persona):
            anteriores = anteriores_de(con, [(lote_id, ing["id"], 1)])
        if (un_lote(con, lote_id).get("disponible_cocina") or 0) <= 0:
            con.execute("UPDATE lotes SET en_secundaria=0 WHERE id=?", (lote_id,))     # cocina se lo llevo todo
    con.commit()
    return jsonify(ok=True, lote_id=lote_id, anteriores=anteriores)


@app.post("/api/lotes/<int:lote_id>/foto")
def api_foto(lote_id):
    con = db()
    un_lote(con, lote_id)
    contenido = request.get_data()
    if not contenido or not contenido[:3] == b"\xff\xd8\xff":
        raise ErrorApi("La foto debe ser una imagen JPG.")
    nombre = "lote_%d_%s.jpg" % (lote_id, dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    with open(os.path.join(FOTOS, nombre), "wb") as f:
        f.write(contenido)
    con.execute("UPDATE lotes SET foto=? WHERE id=?", (nombre, lote_id))
    con.commit()
    return jsonify(ok=True, foto=nombre)


@app.get("/fotos/<path:nombre>")
def ver_foto(nombre):
    return send_from_directory(FOTOS, nombre)


# ----------------------------------------------------------------------------
# 5. Producciones y hoja de produccion
# ----------------------------------------------------------------------------
def hoja_de(con, produccion_id):
    p = con.execute(
        """SELECT p.*, pr.codigo, pr.vida_util_meses, pr.vida_util_dias, pr.produce_ingrediente_id, pr.armado
           FROM producciones p JOIN productos pr ON pr.id=p.producto_id WHERE p.id=?""", (produccion_id,)).fetchone()
    if not p:
        raise ErrorApi("Esa producción no existe.", 404)
    p = dict(p)
    p["fecha_vencimiento"] = vencimiento(p)
    p["armado"] = bool(p["armado"])
    # Mix Bread: los lotes de pan que lo forman.
    p["componentes"] = filas(con.execute(
        """SELECT al.id, al.origen_id, al.unidades, o.producto, o.lote_final, o.fecha, o.vence, o.estado,
                  pr.vida_util_meses
           FROM armado_lotes al JOIN producciones o ON o.id=al.origen_id JOIN productos pr ON pr.id=o.producto_id
           WHERE al.produccion_id=? ORDER BY al.id""", (produccion_id,)))
    for c in p["componentes"]:
        c["fecha_vencimiento"] = vencimiento(c)
    # Pan normal: en que Mix Bread se uso.
    p["usado_mix"] = filas(con.execute(
        """SELECT al.unidades, m.id AS produccion_id, m.producto, m.lote_final, m.fecha
           FROM armado_lotes al JOIN producciones m ON m.id=al.produccion_id
           WHERE al.origen_id=? AND m.estado<>'anulada' ORDER BY m.id""", (produccion_id,)))
    p["unidades_mix"] = round(sum(u["unidades"] for u in p["usado_mix"]), 3)
    p["lineas"] = filas(con.execute(
        """SELECT li.*, i.nombre AS ingrediente FROM produccion_lineas li
           LEFT JOIN ingredientes i ON i.id=li.ingrediente_id
           WHERE li.produccion_id=? ORDER BY li.orden, li.id""", (produccion_id,)))
    p["faltantes"] = []
    for li in p["lineas"]:
        li["lotes"] = filas(con.execute(
            """SELECT pl.id AS pl_id, pl.lote_id, pl.origen AS anotado_por_sistema, pl.agregado_en, pl.agregado_por,
                      pl.quitado_en, pl.quitado_por, pl.motivo, l.lote, l.origen AS origen_lote, l.revisar,
                      (SELECT r.proveedor FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS proveedor,
                      (SELECT MAX(r.fecha_ingreso) FROM recepciones r WHERE r.lote_id=l.id) AS fecha_ingreso,
                      (SELECT r.caducidad FROM recepciones r WHERE r.lote_id=l.id ORDER BY r.fecha_ingreso DESC LIMIT 1) AS caducidad
               FROM produccion_lotes pl JOIN lotes l ON l.id=pl.lote_id
               WHERE pl.linea_id=? ORDER BY pl.id""", (li["id"],)))
        vigentes = [l for l in li["lotes"] if not l["quitado_en"]]
        li["falta_lote"] = (li["tipo"] != "no_aplica" and not li["no_utilizado"] and not vigentes
                            and not (li.get("despues_reposo") and p["estado"] == "en_curso"))
        if li["falta_lote"]:
            p["faltantes"].append(li["etiqueta"])
    return p


def vencimiento(p):
    """Fecha de vencimiento: la fija (Mix Bread) o la elaboracion mas la vida util del producto."""
    if p.get("vence"):
        return p["vence"]
    if p.get("vida_util_dias"):
        return (dt.date.fromisoformat(p["fecha"]) + dt.timedelta(days=p["vida_util_dias"])).isoformat()
    return sumar_meses(dt.date.fromisoformat(p["fecha"]), p.get("vida_util_meses") or 1).isoformat()


def resumen_producciones(con, where="1=1", args=(), limite=3000, desde=0):
    datos = filas(con.execute(
        """SELECT p.id, p.producto_id, p.producto, p.fecha, p.lote_final, p.estado, p.iniciado_en, p.iniciado_por,
                  p.terminado_en, p.unidades, p.n_etiquetas, p.masa_lista_en, p.masa_lista_por,
                  (SELECT lleva_reposo FROM productos pr WHERE pr.id=p.producto_id) AS lleva_reposo,
                  (SELECT COUNT(*) FROM produccion_lineas li WHERE li.produccion_id=p.id AND li.tipo<>'no_aplica'
                     AND li.no_utilizado=0 AND NOT (COALESCE(li.despues_reposo,0)=1 AND p.estado='en_curso')
                     AND NOT EXISTS(SELECT 1 FROM produccion_lotes pl
                         WHERE pl.linea_id=li.id AND pl.quitado_en IS NULL)) AS faltantes
           FROM producciones p WHERE """ + where + " ORDER BY p.id DESC LIMIT ? OFFSET ?", tuple(args) + (limite, desde)))
    return datos


@app.post("/api/productos/<int:pid>/activo")
def api_producto_activo(pid):
    """Eliminar un producto (pan, subreceta o Mix Bread) o volver a producirlo. Las hojas ya hechas no se tocan."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    prod = con.execute("SELECT * FROM productos WHERE id=?", (pid,)).fetchone()
    if not prod:
        raise ErrorApi("Ese producto no existe.", 404)
    activo = 1 if d.get("activo") else 0
    con.execute("UPDATE productos SET activo=? WHERE id=?", (activo, pid))
    anotar(con, persona, "PRODUCTO_" + ("RESTAURADO" if activo else "ELIMINADO"), prod["nombre"])
    con.commit()
    return jsonify(ok=True)


@app.get("/api/productos")
def api_productos():
    con = db()
    productos = filas(con.execute(
        """SELECT p.*, i.nombre AS produce FROM productos p
           LEFT JOIN ingredientes i ON i.id=p.produce_ingrediente_id ORDER BY p.activo DESC, p.nombre"""))
    for p in productos:
        p["lineas"] = filas(con.execute(
            """SELECT r.id, r.orden, r.etiqueta, r.ingrediente_id, i.nombre AS ingrediente, i.tipo, COALESCE(r.despues_reposo,0) AS despues_reposo
               FROM receta r JOIN ingredientes i ON i.id=r.ingrediente_id WHERE r.producto_id=? ORDER BY r.orden""",
            (p["id"],)))
        p["componentes"] = filas(con.execute(
            """SELECT c.componente_id AS id, pr.nombre FROM composicion c JOIN productos pr ON pr.id=c.componente_id
               WHERE c.producto_id=? ORDER BY c.orden""", (p["id"],)))
        if p["se_alimenta"]:
            p["ultima_alimentacion"] = con.execute("SELECT MAX(iniciado_en) FROM producciones WHERE producto_id=? AND estado<>'anulada'",
                                                   (p["id"],)).fetchone()[0]
            p["alimentaciones_guardadas"] = con.execute("SELECT COUNT(*) FROM lotes WHERE ingrediente_id=? AND agotado=0",
                                                        (p["produce_ingrediente_id"],)).fetchone()[0]
    return jsonify(productos)


@app.get("/api/producciones")
def api_producciones():
    estado = request.args.get("estado")
    con = db()
    if request.args.get("pagina"):
        # Pantalla Hojas: todas las abiertas y las completas de 5 en 5 (no se manda todo el historial).
        pagina = max(1, request.args.get("pagina", type=int) or 1)
        total = con.execute("SELECT COUNT(*) FROM producciones WHERE estado='completa'").fetchone()[0]
        return jsonify(abiertas=resumen_producciones(con, "p.estado IN ('en_curso','por_completar')"),
                       completas=resumen_producciones(con, "p.estado='completa'", (), POR_PAGINA_HOJAS, (pagina - 1) * POR_PAGINA_HOJAS),
                       total_completas=total, pagina=pagina, por_pagina=POR_PAGINA_HOJAS)
    if estado:
        return jsonify(resumen_producciones(con, "p.estado=?", (estado,)))
    return jsonify(resumen_producciones(con, "p.estado<>'anulada'"))


@app.get("/api/producciones.xlsx")
def api_producciones_xlsx():
    """Todas las hojas de produccion hasta hoy (sin anuladas): una hoja de Excel con cada produccion y otra con cada ingrediente y su lote."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    con = db()
    ids = [f[0] for f in con.execute("SELECT id FROM producciones WHERE estado<>'anulada' ORDER BY fecha, id")]
    hojas = [hoja_de(con, i) for i in ids]
    libro = openpyxl.Workbook()
    fino = Side(style="thin", color="999999")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    cab = PatternFill("solid", fgColor="2C344E")
    alinear = Alignment(vertical="center", wrap_text=True)
    t = lambda x: (x or "")[:16].replace("T", " ") or None

    def tabla(hoja, titulo, columnas, datos, anchos):
        hoja["A1"] = titulo
        hoja["A1"].font = Font(bold=True, size=13)
        hoja["A2"] = "San Francisco FoodService · descargado el " + dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        hoja["A2"].font = Font(italic=True, color="5F677B")
        for c, txt in enumerate(columnas, 1):
            celda = hoja.cell(row=4, column=c, value=txt)
            celda.font, celda.fill, celda.border = Font(bold=True, color="FFFFFF"), cab, borde
            celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        texto = {3, 5} if hoja.title != "Hojas de producción" else {3}      # codigos de lote: como texto
        # El formato se arma una vez y se copia (asignarlo celda por celda es muy lento con miles de filas).
        modelo = hoja.cell(row=1, column=60)
        modelo.border, modelo.alignment = borde, alinear
        normal = copy.copy(modelo._style)
        modelo.number_format = "@"
        como_texto = copy.copy(modelo._style)
        del hoja._cells[(1, 60)]
        for n, fila in enumerate(datos, 5):
            for c, v in enumerate(fila, 1):
                celda = hoja.cell(row=n, column=c, value=v)
                celda._style = copy.copy(como_texto if c in texto else normal)
        for c, ancho in enumerate(anchos, 1):
            hoja.column_dimensions[openpyxl.utils.get_column_letter(c)].width = ancho
        hoja.freeze_panes = "A5"
        if datos:
            hoja.auto_filter.ref = "A4:%s%d" % (openpyxl.utils.get_column_letter(len(columnas)), 4 + len(datos))

    estados = {"en_curso": "EN CURSO", "por_completar": "POR COMPLETAR", "completa": "COMPLETA"}
    h1 = libro.active
    h1.title = "Hojas de producción"
    tabla(h1, "Hojas de producción",
          ["FECHA ELABORACIÓN", "PRODUCTO", "LOTE", "TIPO", "ESTADO", "VENCE", "INICIO DE MASA", "MASA LISTA", "FIN DE PRODUCCIÓN",
           "HOJA CERRADA", "UNIDADES PRODUCIDAS", "UNIDADES PARA EL LOTE", "UNIDAD", "HORNEADO", "ETIQUETAS", "OBSERVACIONES", "RESPONSABLE"],
          [[dt.date.fromisoformat(p["fecha"]).strftime("%d/%m/%Y"), p["producto"], p["lote_final"],
            "Mix Bread" if p["armado"] else "Subreceta" if p["produce_ingrediente_id"] else "Pan",
            estados.get(p["estado"], p["estado"]), dt.date.fromisoformat(p["fecha_vencimiento"]).strftime("%d/%m/%Y") if p.get("fecha_vencimiento") else None,
            t(p["iniciado_en"]), t(p.get("masa_lista_en")), t(p["terminado_en"]), t(p.get("completado_en")),
            p["unidades"] or None, p.get("unidades_lote") or None, p.get("unidad_sub") or None, p["horneado"] or None,
            p["n_etiquetas"], p["observaciones"] or None, p["iniciado_por"]] for p in hojas],
          [13, 28, 14, 11, 15, 12, 16, 16, 16, 16, 12, 12, 9, 18, 10, 30, 16])
    filas_ing = []
    for p in hojas:
        for li in p["lineas"]:
            vig = [l["lote"] for l in li["lotes"] if not l["quitado_en"]]
            filas_ing.append([dt.date.fromisoformat(p["fecha"]).strftime("%d/%m/%Y"), p["producto"], p["lote_final"], li["etiqueta"],
                              "No aplica" if li["tipo"] == "no_aplica" else ", ".join(vig) if vig else "No se utilizó" if li["no_utilizado"] else "SIN LOTE",
                              li["cantidad"] or None])
        for comp in p.get("componentes") or []:
            filas_ing.append([dt.date.fromisoformat(p["fecha"]).strftime("%d/%m/%Y"), p["producto"], p["lote_final"], comp.get("producto"),
                              comp.get("lote_final"), comp.get("unidades")])
    h2 = libro.create_sheet("Ingredientes y lotes")
    tabla(h2, "Ingredientes y lotes de cada hoja", ["FECHA ELABORACIÓN", "PRODUCTO", "LOTE DEL PAN", "INGREDIENTE", "LOTE(S) DEL INGREDIENTE", "CANTIDAD UTILIZADA"],
          filas_ing, [13, 28, 14, 26, 26, 16])
    salida = io.BytesIO()
    libro.save(salida)
    salida.seek(0)
    return send_file(salida, as_attachment=True, download_name="Hojas_de_produccion_%s.xlsx" % dt.date.today().isoformat(),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/producciones/<int:pid>")
def api_hoja(pid):
    return jsonify(hoja_de(db(), pid))


CANDADO_INICIO = threading.Lock()     # evita que dos toques seguidos inicien el mismo pan dos veces


@app.post("/api/producciones")
def api_iniciar():
    with CANDADO_INICIO:
        return iniciar_produccion()


def iniciar_produccion():
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    prod = con.execute("SELECT * FROM productos WHERE id=?", (datos.get("producto_id"),)).fetchone()
    if not prod:
        raise ErrorApi("Elige el producto.")
    if not prod["activo"]:
        raise ErrorApi("%s fue eliminado de las recetas: no se puede empezar." % prod["nombre"])
    if prod["se_alimenta"]:
        raise ErrorApi("%s no se empieza: se alimenta (botón «Alimentar»)." % prod["nombre"])
    podar_masa_madre(con, persona)
    # No se empieza si en cocina hay abierto un lote caducado de algun ingrediente de la receta.
    de_receta = {f[0] for f in con.execute("SELECT ingrediente_id FROM receta WHERE producto_id=?", (prod["id"],))}
    caducados = ["%s de %s (caducó el %s)" % (l["lote"], g["ingrediente"], dia_texto(l["caducidad"]))
                 for g in recipientes_abiertos(con) if g["ingrediente_id"] in de_receta for l in g["lotes"] if l["semaforo"] == "rojo"]
    if caducados:
        raise ErrorApi("No se puede empezar %s: en cocina está abierto un lote caducado: %s. Dalo de baja en Cocina y vuelve a intentarlo." % (
            prod["nombre"], "; ".join(caducados)))
    # Un pan no puede empezar otra vez mientras su produccion anterior siga en curso.
    # (Un pan con reposo cuya masa ya esta lista si puede empezar otra masa.)
    previa = con.execute("SELECT lote_final, iniciado_en, iniciado_por FROM producciones WHERE producto_id=? AND estado='en_curso' AND masa_lista_en IS NULL",
                         (prod["id"],)).fetchone()
    if previa:
        raise ErrorApi("%s ya está en producción (lote %s, empezó %s%s). %s" % (
            prod["nombre"], previa["lote_final"], previa["iniciado_en"][11:16], " · " + previa["iniciado_por"] if previa["iniciado_por"] else "",
            "Cuando la masa quede en reposo, toca «Masa lista» y podrás empezar otra." if prod["lleva_reposo"]
            else "Cuando termine, toca «Terminó» y recién podrás empezar otra."))
    if prod["armado"]:
        raise ErrorApi("«%s» se arma con panes ya producidos: lo registra el encargado en Hojas de producción." % prod["nombre"])
    receta = con.execute(
        """SELECT r.orden, r.etiqueta, r.ingrediente_id, i.tipo, COALESCE(r.despues_reposo,0) AS despues_reposo FROM receta r
           JOIN ingredientes i ON i.id=r.ingrediente_id WHERE r.producto_id=? ORDER BY r.orden""", (prod["id"],)).fetchall()
    if not receta:
        raise ErrorApi("«%s» todavía no tiene receta. Agrégala en Administrar → Recetas." % prod["nombre"])
    hoy = dt.date.today()
    lote_final = hoy.strftime("%d%m%y") + prod["codigo"]
    if prod["produce_ingrediente_id"]:
        # Subreceta: cada tanda tiene su propio lote, aunque se haga dos veces el mismo dia (141126RE, 141126RE-2...).
        base, n = lote_final, 1
        while con.execute("SELECT 1 FROM lotes WHERE ingrediente_id=? AND lote=?", (prod["produce_ingrediente_id"], lote_final)).fetchone():
            n += 1
            lote_final = "%s-%d" % (base, n)
    momento = ahora()
    cur = con.execute(
        "INSERT INTO producciones(producto_id,producto,fecha,lote_final,iniciado_en,iniciado_por) VALUES(?,?,?,?,?,?)",
        (prod["id"], prod["nombre"], hoy.isoformat(), lote_final, momento, persona))
    pid = cur.lastrowid
    for r in receta:
        despues = 1 if prod["lleva_reposo"] and r["despues_reposo"] else 0
        lin = con.execute(
            "INSERT INTO produccion_lineas(produccion_id,orden,etiqueta,ingrediente_id,tipo,despues_reposo) VALUES(?,?,?,?,?,?)",
            (pid, r["orden"], r["etiqueta"], r["ingrediente_id"], r["tipo"], despues))
        if r["tipo"] == "no_aplica" or despues:
            continue                # lo que va despues del reposo se anota desde «Masa lista» hasta «Terminó»
        for lote in listar_lotes(con, "abiertos", r["ingrediente_id"]):
            con.execute("INSERT INTO produccion_lotes(linea_id,lote_id,origen,agregado_en,agregado_por) VALUES(?,?,?,?,?)",
                        (lin.lastrowid, lote["id"], "inicio", momento, persona))
    anotar(con, persona, "INICIO_PRODUCCION", "%s lote %s" % (prod["nombre"], lote_final), produccion_id=pid)

    # Subreceta (masa madre, relleno, glace...): su lote queda disponible como ingrediente interno.
    anteriores = []
    if prod["produce_ingrediente_id"]:
        ing_id = prod["produce_ingrediente_id"]
        # Antes de empezar se respondio que pasa con los lotes anteriores de esta subreceta:
        # terminado (se acabo o se boto), guardado (queda aparte para otro dia) o mezcla (va junto con el nuevo).
        respuestas = {int(a.get("lote_id") or 0): a.get("accion") for a in datos.get("anteriores") or []}
        mezclados = []
        for lote_id, accion in respuestas.items():
            previo = con.execute(LOTE_SQL + " WHERE l.id=? AND l.ingrediente_id=? AND l.agotado=0", (lote_id, ing_id)).fetchone()
            if not previo or previo["lote"] == lote_final:
                continue
            previo = con_saldo(dict(previo))
            if accion == "mezcla" and previo["semaforo"] == "rojo":
                raise ErrorApi("El lote %s está caducado: no se puede mezclar con el nuevo. Elige «Se terminó»." % previo["lote"])
            if accion == "mezcla":
                abrir_lote(con, previo, persona)
                mezclados.append(previo)
            elif accion in ("terminado", "guardado"):
                con.execute("UPDATE aperturas SET cerrado_en=?, cerrado_por=? WHERE lote_id=? AND cerrado_en IS NULL",
                            (momento, persona, lote_id))
                con.execute("UPDATE lotes SET agotado=? WHERE id=?", (1 if accion == "terminado" else 0, lote_id))
                anotar(con, persona, "SUBRECETA_TERMINADA" if accion == "terminado" else "SUBRECETA_GUARDADA",
                       "%s lote %s (al empezar %s)" % (previo["ingrediente"], previo["lote"], lote_final), lote_id)
        con.execute("INSERT OR IGNORE INTO lotes(ingrediente_id,lote,origen,creado_en,creado_por) VALUES(?,?,?,?,?)",
                    (ing_id, lote_final, "interno", momento, persona))
        interno = con.execute("SELECT id FROM lotes WHERE ingrediente_id=? AND lote=?", (ing_id, lote_final)).fetchone()
        caduca = (hoy + dt.timedelta(days=prod["vida_util_dias"] or DIAS_SUBRECETA)).isoformat()
        # Si se mezcla con un lote anterior, la mezcla caduca cuando caduca el mas antiguo.
        caduca = min([caduca] + [m["caducidad"][:10] for m in mezclados if m.get("caducidad")])
        con.execute("UPDATE lotes SET agotado=0, caducidad=? WHERE id=?", (caduca, interno["id"]))
        if mezclados:
            anotar(con, persona, "SUBRECETA_MEZCLADA", "%s lote %s se mezcló con %s" % (
                prod["nombre"], lote_final, ", ".join(m["lote"] for m in mezclados)), interno["id"])
        if abrir_lote(con, un_lote(con, interno["id"]), persona) and "anteriores" not in datos:
            anteriores = anteriores_de(con, [(interno["id"], ing_id, 1)])
    con.commit()
    hoja = hoja_de(con, pid)
    hoja["anteriores"] = anteriores
    otras = con.execute("SELECT COUNT(*) FROM producciones WHERE lote_final=? AND id<>? AND estado<>'anulada'",
                        (lote_final, pid)).fetchone()[0]
    hoja["mismo_codigo"] = otras
    return jsonify(hoja)


DIAS_MEMORIA_MASA_MADRE = 30


def podar_masa_madre(con, persona):
    """Las alimentaciones de hace mas de vida_util_dias dias salen de la masa madre: sus lotes de harina dejan de
    anotarse en los panes. Devuelve cuantos lotes salieron."""
    hoy = dt.date.today()
    n = 0
    for prod in con.execute("SELECT * FROM productos WHERE se_alimenta=1 AND produce_ingrediente_id IS NOT NULL").fetchall():
        limite = (hoy - dt.timedelta(days=prod["vida_util_dias"] or DIAS_MEMORIA_MASA_MADRE)).isoformat()
        for l in con.execute("SELECT id, lote FROM lotes WHERE ingrediente_id=? AND agotado=0 AND substr(creado_en,1,10)<=?",
                             (prod["produce_ingrediente_id"], limite)).fetchall():
            con.execute("UPDATE aperturas SET cerrado_en=?, cerrado_por=? WHERE lote_id=? AND cerrado_en IS NULL", (ahora(), persona, l["id"]))
            con.execute("UPDATE lotes SET agotado=1 WHERE id=?", (l["id"],))
            anotar(con, persona, "MASA_MADRE_SALE", "%s: la alimentación %s ya tiene más de %d días y sale de la masa madre" % (
                prod["nombre"], l["lote"], prod["vida_util_dias"] or DIAS_MEMORIA_MASA_MADRE), l["id"])
            n += 1
    return n


@app.post("/api/productos/<int:producto_id>/alimentar")
def api_alimentar(producto_id):
    """Alimentar la masa madre: se anotan los lotes de harina abiertos en cocina en este momento. Cada alimentacion es un
    lote interno (231126MM, 231126MM-2...) que se junta con las alimentaciones del ultimo mes; las mas antiguas salen."""
    with CANDADO_INICIO:
        datos = cuerpo()
        persona = persona_de(datos)
        con = db()
        prod = con.execute("SELECT * FROM productos WHERE id=?", (producto_id,)).fetchone()
        if not prod or not prod["se_alimenta"] or not prod["produce_ingrediente_id"]:
            raise ErrorApi("Ese producto no se alimenta.")
        if not prod["activo"]:
            raise ErrorApi("%s fue eliminado de las recetas." % prod["nombre"])
        receta = con.execute(
            """SELECT r.orden, r.etiqueta, r.ingrediente_id, i.tipo, i.nombre FROM receta r JOIN ingredientes i ON i.id=r.ingrediente_id
               WHERE r.producto_id=? ORDER BY r.orden""", (prod["id"],)).fetchall()
        if not receta:
            raise ErrorApi("«%s» todavía no tiene receta. Agrégala en Administrar → Recetas." % prod["nombre"])
        abiertos = {r["ingrediente_id"]: listar_lotes(con, "abiertos", r["ingrediente_id"]) for r in receta if r["tipo"] != "no_aplica"}
        faltan = [r["nombre"] for r in receta if r["tipo"] != "no_aplica" and not abiertos[r["ingrediente_id"]]]
        if faltan:
            raise ErrorApi("No hay ningún lote abierto en cocina de: %s. Ábrelo en Cocina y vuelve a alimentar." % ", ".join(faltan))
        caducados = ["%s de %s (caducó el %s)" % (l["lote"], l["ingrediente"], dia_texto(l["caducidad"]))
                     for ls in abiertos.values() for l in ls if l["semaforo"] == "rojo"]
        if caducados:
            raise ErrorApi("No se puede alimentar: en cocina está abierto un lote caducado: %s. Dalo de baja en Cocina." % "; ".join(caducados))
        salieron = podar_masa_madre(con, persona)
        hoy = dt.date.today()
        ing_id = prod["produce_ingrediente_id"]
        base = lote_final = hoy.strftime("%d%m%y") + prod["codigo"]
        n = 1
        while con.execute("SELECT 1 FROM lotes WHERE ingrediente_id=? AND lote=?", (ing_id, lote_final)).fetchone():
            n += 1
            lote_final = "%s-%d" % (base, n)
        momento = ahora()
        pid = con.execute(
            """INSERT INTO producciones(producto_id,producto,fecha,lote_final,estado,iniciado_en,iniciado_por,terminado_en,terminado_por,
                                        completado_en,completado_por,observaciones) VALUES(?,?,?,?,'completa',?,?,?,?,?,?,?)""",
            (prod["id"], prod["nombre"], hoy.isoformat(), lote_final, momento, persona, momento, persona, momento, persona, "Alimentación")).lastrowid
        for r in receta:
            lin = con.execute("INSERT INTO produccion_lineas(produccion_id,orden,etiqueta,ingrediente_id,tipo) VALUES(?,?,?,?,?)",
                              (pid, r["orden"], r["etiqueta"], r["ingrediente_id"], r["tipo"]))
            for lote in abiertos.get(r["ingrediente_id"], []):
                con.execute("INSERT INTO produccion_lotes(linea_id,lote_id,origen,agregado_en,agregado_por) VALUES(?,?,?,?,?)",
                            (lin.lastrowid, lote["id"], "inicio", momento, persona))
        dias = prod["vida_util_dias"] or DIAS_MEMORIA_MASA_MADRE
        con.execute("INSERT INTO lotes(ingrediente_id,lote,origen,creado_en,creado_por,caducidad) VALUES(?,?,?,?,?,?)",
                    (ing_id, lote_final, "interno", momento, persona, (hoy + dt.timedelta(days=dias)).isoformat()))
        # La masa madre es una sola: la alimentacion nueva y las del ultimo mes quedan juntas en cocina.
        # No se agrega a los panes que ya empezaron: su masa madre se tomo antes de alimentarla.
        juntos = 0
        for l in con.execute("SELECT id FROM lotes WHERE ingrediente_id=? AND agotado=0 ORDER BY id", (ing_id,)).fetchall():
            if not con.execute("SELECT 1 FROM aperturas WHERE lote_id=? AND cerrado_en IS NULL", (l["id"],)).fetchone():
                con.execute("INSERT INTO aperturas(lote_id,abierto_en,abierto_por,recipiente) VALUES(?,?,?,1)", (l["id"], momento, persona))
            juntos += 1
        harinas = ", ".join("%s %s" % (l["ingrediente"], l["lote"]) for ls in abiertos.values() for l in ls)
        anotar(con, persona, "ALIMENTACION", "%s alimentada (lote %s) con %s" % (prod["nombre"], lote_final, harinas), produccion_id=pid)
        con.commit()
        return jsonify(ok=True, lote_final=lote_final, produccion_id=pid, harinas=harinas, alimentaciones=juntos, salieron=salieron)


@app.post("/api/producciones/<int:pid>/terminar")
def api_terminar(pid):
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    p = hoja_de(con, pid)
    if p["estado"] != "en_curso":
        raise ErrorApi("Esa producción ya estaba terminada.")
    momento = ahora()
    # Renglones que se agregan despues del reposo (ej. relleno): lo que esta abierto al terminar.
    for li in con.execute("SELECT id, ingrediente_id FROM produccion_lineas WHERE produccion_id=? AND despues_reposo=1 AND tipo<>'no_aplica'",
                          (pid,)).fetchall():
        for lote in listar_lotes(con, "abiertos", li["ingrediente_id"]):
            if not con.execute("SELECT 1 FROM produccion_lotes WHERE linea_id=? AND lote_id=? AND quitado_en IS NULL", (li["id"], lote["id"])).fetchone():
                con.execute("INSERT INTO produccion_lotes(linea_id,lote_id,origen,agregado_en,agregado_por) VALUES(?,?,?,?,?)",
                            (li["id"], lote["id"], "al terminar", momento, persona))
    con.execute("UPDATE producciones SET estado='por_completar', terminado_en=?, terminado_por=?, masa_lista_en=COALESCE(masa_lista_en,?) WHERE id=?",
                (momento, persona, momento, pid))
    anotar(con, persona, "FIN_PRODUCCION", "%s lote %s" % (p["producto"], p["lote_final"]), produccion_id=pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


@app.post("/api/producciones/<int:pid>/masa-lista")
def api_masa_lista(pid):
    """Pan con reposo: la masa ya se hizo. Desde aqui los lotes que se abran no se anotan en esta masa."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    p = hoja_de(con, pid)
    if p["estado"] != "en_curso":
        raise ErrorApi("Esa producción ya estaba terminada.")
    if p.get("masa_lista_en"):
        raise ErrorApi("La masa ya estaba marcada como lista.")
    con.execute("UPDATE producciones SET masa_lista_en=?, masa_lista_por=? WHERE id=?", (ahora(), persona, pid))
    anotar(con, persona, "MASA_LISTA", "%s lote %s: masa en reposo" % (p["producto"], p["lote_final"]), produccion_id=pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


@app.post("/api/producciones/<int:pid>/anular")
def api_anular(pid):
    datos = cuerpo()
    persona = persona_de(datos)
    motivo = str(datos.get("motivo") or "").strip()
    if not motivo:
        raise ErrorApi("Escribe el motivo de la anulación.")
    con = db()
    p = hoja_de(con, pid)
    if p["usado_mix"]:
        raise ErrorApi("Este lote se usó en " + ", ".join("%s %s" % (u["producto"], u["lote_final"]) for u in p["usado_mix"])
                       + ". Anula primero ese Mix Bread.")
    con.execute("UPDATE producciones SET estado='anulada' WHERE id=?", (pid,))
    anotar(con, persona, "PRODUCCION_ANULADA", "%s lote %s. Motivo: %s" % (p["producto"], p["lote_final"], motivo),
           produccion_id=pid)
    con.commit()
    return jsonify(ok=True)


@app.post("/api/producciones/<int:pid>/guardar")
def api_guardar(pid):
    """Guarda los campos que se llenan a mano. Con cerrar=true valida y da la hoja por completa."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    p = hoja_de(con, pid)
    if p["estado"] == "anulada":
        raise ErrorApi("Esa producción está anulada.")
    if p["armado"]:
        raise ErrorApi("Un Mix Bread no se edita: si hay un error, anúlalo y vuelve a armarlo.")
    try:
        etiquetas = max(0, int(datos.get("n_etiquetas", p["n_etiquetas"]) or 0))
    except (TypeError, ValueError):
        raise ErrorApi("El número de etiquetas debe ser un número entero.")
    unidades = str(datos.get("unidades", p["unidades"]) or "").strip()
    unidades_lote = str(datos.get("unidades_lote", p["unidades_lote"]) or "").strip()
    if p["produce_ingrediente_id"]:
        # Subreceta: solo la cantidad total producida (opcional). El lote se cierra cuando cocina toca "Se terminó".
        unidad_sub = " ".join(str(datos.get("unidad_sub", p["unidad_sub"]) or "").upper().split())
        if unidades and numero(unidades) is None:
            raise ErrorApi("La cantidad total producida debe ser solo un número.")
        con.execute("UPDATE producciones SET unidad_sub=? WHERE id=?", (unidad_sub, pid))
    total, propio = numero(unidades), numero(unidades_lote)
    if total is not None and (propio or 0) + p["unidades_mix"] > total + 1e-9:
        raise ErrorApi("Las unidades del lote (%s) más las usadas en Mix Bread (%s) suman más que lo producido (%s)." % (
            fmt(propio or 0), fmt(p["unidades_mix"]), fmt(total)))
    con.execute("UPDATE producciones SET unidades_lote=? WHERE id=?", (unidades_lote, pid))
    con.execute("UPDATE producciones SET unidades=?, horneado=?, observaciones=?, n_etiquetas=? WHERE id=?",
                (unidades,
                 str(datos.get("horneado", p["horneado"]) or "").strip(),
                 str(datos.get("observaciones", p["observaciones"]) or "").strip(), etiquetas, pid))
    propias = {li["id"]: li for li in p["lineas"]}
    for li in datos.get("lineas") or []:
        if li.get("id") in propias:
            no_usado = 1 if li.get("no_utilizado") else 0
            cantidad = "" if no_usado else str(li.get("cantidad") or "").strip()
            if cantidad and numero(cantidad) is None:
                raise ErrorApi("La cantidad utilizada de %s debe ser solo un número." % propias[li["id"]]["etiqueta"])
            con.execute("UPDATE produccion_lineas SET cantidad=?, no_utilizado=? WHERE id=?", (cantidad, no_usado, li["id"]))
    if datos.get("cerrar"):
        p = hoja_de(con, pid)
        if p["estado"] == "en_curso":
            raise ErrorApi("Primero hay que terminar la producción.")
        if p["faltantes"]:
            con.commit()
            raise ErrorApi("Guardado, pero la hoja no se puede cerrar: falta el lote de " + ", ".join(p["faltantes"])
                           + ". Agrega el lote o marca «No se utilizó».")
        # Para cerrar, todos los campos llenos (menos observaciones).
        vacios = []
        if p["produce_ingrediente_id"]:
            pass        # subrecetas (relleno, glace, masa madre): la cantidad es opcional
        else:
            for campo, nombre in (("unidades", "unidades producidas"), ("unidades_lote", "unidades para el lote"), ("horneado", "horneado")):
                if not str(p[campo] or "").strip():
                    vacios.append(nombre)
            if not p["n_etiquetas"]:
                vacios.append("etiquetas a imprimir")
        sin_cantidad = [] if p["produce_ingrediente_id"] else [
            li["etiqueta"] for li in p["lineas"] if not li["no_utilizado"] and not str(li["cantidad"] or "").strip()]
        if sin_cantidad:
            vacios.append("cantidad utilizada de " + ", ".join(sin_cantidad))
        if vacios:
            con.commit()
            raise ErrorApi("Guardado, pero la hoja no se puede cerrar. Falta llenar: " + "; ".join(vacios) + ".")
        con.execute("UPDATE producciones SET estado='completa', completado_en=?, completado_por=? WHERE id=?",
                    (ahora(), persona, pid))
        anotar(con, persona, "HOJA_COMPLETA", "%s lote %s" % (p["producto"], p["lote_final"]), produccion_id=pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


@app.post("/api/producciones/<int:pid>/reabrir")
def api_reabrir(pid):
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    p = hoja_de(con, pid)
    if p["estado"] != "completa" or p["armado"]:
        raise ErrorApi("Solo se puede reabrir la hoja completa de un pan (un Mix Bread se anula y se vuelve a armar).")
    con.execute("UPDATE producciones SET estado='por_completar' WHERE id=?", (pid,))
    anotar(con, persona, "HOJA_REABIERTA", "%s lote %s" % (p["producto"], p["lote_final"]), produccion_id=pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


@app.post("/api/producciones/<int:pid>/agregar_lote")
def api_agregar_lote(pid):
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    linea = con.execute("SELECT * FROM produccion_lineas WHERE id=? AND produccion_id=?",
                        (datos.get("linea_id"), pid)).fetchone()
    if not linea:
        raise ErrorApi("Ese renglón no existe.")
    lote = un_lote(con, datos.get("lote_id"))
    if lote["ingrediente_id"] != linea["ingrediente_id"]:
        raise ErrorApi("Ese lote es de otro ingrediente.")
    if con.execute("SELECT 1 FROM produccion_lotes WHERE linea_id=? AND lote_id=? AND quitado_en IS NULL",
                   (linea["id"], lote["id"])).fetchone():
        raise ErrorApi("Ese lote ya está anotado en el renglón.")
    con.execute("INSERT INTO produccion_lotes(linea_id,lote_id,origen,agregado_en,agregado_por) VALUES(?,?,?,?,?)",
                (linea["id"], lote["id"], "correccion", ahora(), persona))
    con.execute("UPDATE produccion_lineas SET no_utilizado=0 WHERE id=?", (linea["id"],))
    anotar(con, persona, "CORRECCION_AGREGA", "%s: se agregó el lote %s" % (linea["etiqueta"], lote["lote"]),
           lote["id"], pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


@app.post("/api/producciones/<int:pid>/quitar_lote")
def api_quitar_lote(pid):
    datos = cuerpo()
    persona = persona_de(datos)
    motivo = str(datos.get("motivo") or "").strip()
    if not motivo:
        raise ErrorApi("Escribe por qué se quita el lote.")
    con = db()
    fila = con.execute(
        """SELECT pl.id, pl.lote_id, li.etiqueta, l.lote FROM produccion_lotes pl
           JOIN produccion_lineas li ON li.id=pl.linea_id JOIN lotes l ON l.id=pl.lote_id
           WHERE pl.id=? AND li.produccion_id=? AND pl.quitado_en IS NULL""", (datos.get("pl_id"), pid)).fetchone()
    if not fila:
        raise ErrorApi("Ese lote ya no está en la hoja.")
    con.execute("UPDATE produccion_lotes SET quitado_en=?, quitado_por=?, motivo=? WHERE id=?",
                (ahora(), persona, motivo, fila["id"]))
    anotar(con, persona, "CORRECCION_QUITA", "%s: se quitó el lote %s. Motivo: %s" % (fila["etiqueta"], fila["lote"], motivo),
           fila["lote_id"], pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


# ----------------------------------------------------------------------------
# 5b. Mix Bread: se arma con unidades de lotes de pan ya producidos
# ----------------------------------------------------------------------------
DIAS_MIX = 7     # se pueden usar panes producidos hasta una semana antes (la pantalla muestra primero los 2 mas recientes)
POR_PAGINA_HOJAS = 5     # hojas completas por pagina en la pantalla Hojas de produccion


def disponibles_mix(con, producto_id):
    mix = con.execute("SELECT * FROM productos WHERE id=? AND armado=1 AND activo=1", (producto_id,)).fetchone()
    if not mix:
        raise ErrorApi("Ese producto no es un Mix Bread.")
    desde = (dt.date.today() - dt.timedelta(days=DIAS_MIX - 1)).isoformat()
    grupos = []
    for comp in con.execute("""SELECT pr.id, pr.nombre FROM composicion c JOIN productos pr ON pr.id=c.componente_id
                               WHERE c.producto_id=? ORDER BY c.orden""", (producto_id,)).fetchall():
        lotes = filas(con.execute(
            """SELECT p.id, p.producto, p.lote_final, p.fecha, p.estado, p.unidades, p.unidades_lote, p.vence, pr.vida_util_meses,
                      (SELECT COALESCE(SUM(al.unidades),0) FROM armado_lotes al JOIN producciones m ON m.id=al.produccion_id
                       WHERE al.origen_id=p.id AND m.estado<>'anulada') AS usado_mix
               FROM producciones p JOIN productos pr ON pr.id=p.producto_id
               WHERE p.producto_id=? AND p.estado IN ('por_completar','completa') AND p.fecha>=?
               ORDER BY p.fecha DESC, p.id DESC""", (comp["id"], desde)))
        for l in lotes:
            total, propio = numero(l["unidades"]), numero(l["unidades_lote"])
            l["usado_mix"] = round(l["usado_mix"], 3)
            l["disponible"] = round(total - (propio or 0) - l["usado_mix"], 3) if total is not None else None
            l["fecha_vencimiento"] = vencimiento(l)
        grupos.append({"producto_id": comp["id"], "producto": comp["nombre"], "lotes": lotes})
    return dict(mix), grupos


@app.get("/api/mix/<int:producto_id>")
def api_mix_disponibles(producto_id):
    mix, grupos = disponibles_mix(db(), producto_id)
    return jsonify(producto=mix, grupos=grupos, dias=DIAS_MIX)


@app.post("/api/mix/<int:producto_id>")
def api_mix_registrar(producto_id):
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    mix, grupos = disponibles_mix(con, producto_id)
    lotes = {l["id"]: l for g in grupos for l in g["lotes"]}
    items = datos.get("items") or []
    if not items:
        raise ErrorApi("Elige al menos un lote de pan.")
    usados = []
    for item in items:
        origen = lotes.get(item.get("origen_id"))
        if not origen:
            raise ErrorApi("Ese lote de pan no se puede usar en este Mix Bread.")
        unidades = numero(item.get("unidades"))
        if unidades is None or unidades <= 0:
            raise ErrorApi("Escribe cuántas unidades del lote %s entran al Mix Bread." % origen["lote_final"])
        if origen["disponible"] is not None and unidades > origen["disponible"] + 1e-9:
            raise ErrorApi("Del lote %s %s quedan %s unidades disponibles." % (origen["producto"], origen["lote_final"],
                                                                                fmt(max(origen["disponible"], 0))))
        usados.append((origen, unidades))
    try:
        etiquetas = max(0, int(datos.get("n_etiquetas") or 1))
    except (TypeError, ValueError):
        raise ErrorApi("El número de etiquetas debe ser un número entero.")
    hoy, momento = dt.date.today(), ahora()
    vence = min(o["fecha_vencimiento"] for o, _ in usados)         # el mas temprano de sus panes
    pid = con.execute(
        """INSERT INTO producciones(producto_id,producto,fecha,lote_final,estado,iniciado_en,iniciado_por,terminado_en,
           terminado_por,completado_en,completado_por,unidades,observaciones,n_etiquetas,vence)
           VALUES(?,?,?,?,'completa',?,?,?,?,?,?,?,?,?,?)""",
        (mix["id"], mix["nombre"], hoy.isoformat(), hoy.strftime("%d%m%y") + mix["codigo"], momento, persona, momento, persona,
         momento, persona, str(datos.get("unidades") or "").strip(), str(datos.get("observaciones") or "").strip(),
         etiquetas, vence)).lastrowid
    for origen, unidades in usados:
        con.execute("INSERT INTO armado_lotes(produccion_id,origen_id,unidades) VALUES(?,?,?)", (pid, origen["id"], unidades))
    anotar(con, persona, "MIX_ARMADO", "%s: %s" % (mix["nombre"], "; ".join(
        "%s %s x %s" % (o["producto"], o["lote_final"], fmt(u)) for o, u in usados)), produccion_id=pid)
    con.commit()
    return jsonify(hoja_de(con, pid))


# ----------------------------------------------------------------------------
# 6. Etiquetas (exportacion para Zebra)
# ----------------------------------------------------------------------------
def etiquetas_de(con, desde=None, hasta=None):
    """Producciones (no anuladas) entre dos fechas de elaboracion; sin fechas = todo el historial."""
    donde, args = ["p.estado<>'anulada'"], []
    if desde:
        donde.append("p.fecha>=?"); args.append(desde)
    if hasta:
        donde.append("p.fecha<=?"); args.append(hasta)
    datos = filas(con.execute(
        """SELECT p.id, p.producto, p.lote_final, p.fecha, p.estado, p.n_etiquetas, p.unidades, p.vence,
                  pr.vida_util_meses, pr.vida_util_dias, pr.produce_ingrediente_id
           FROM producciones p JOIN productos pr ON pr.id=p.producto_id
           WHERE %s ORDER BY p.fecha, p.id""" % " AND ".join(donde), args))
    for d in datos:
        fe = dt.date.fromisoformat(d["fecha"])
        fv = dt.date.fromisoformat(vencimiento(d))
        d["fe"] = fe.strftime("%d-%m-%Y")
        d["fv"] = fv.strftime("%d-%m-%Y")
        d["es_subreceta"] = bool(d.pop("produce_ingrediente_id"))
    return datos


def rango_etiquetas():
    """Lee desde/hasta (o fecha, un solo dia) de la consulta. todo=1: todo el historial."""
    a = request.args
    if a.get("todo") == "1":
        return None, None
    if a.get("fecha"):
        return a["fecha"], a["fecha"]
    hoy = dt.date.today().isoformat()
    return a.get("desde") or hoy, a.get("hasta") or hoy


@app.get("/api/etiquetas")
def api_etiquetas():
    return jsonify(etiquetas_de(db(), *rango_etiquetas()))


ESTADOS_TEXTO = {"en_curso": "En curso", "por_completar": "Por completar", "completa": "Completa"}


def filas_etiquetas(con):
    """Filas para ZebraDesigner. ids=1,2,3 limita a las elegidas; todo=1 agrega tipo y estado (historial)."""
    a = request.args
    desde, hasta = rango_etiquetas()
    historial = a.get("todo") == "1"
    con_sub = a.get("subrecetas") == "1" or historial
    ids = {int(x) for x in a.get("ids", "").split(",") if x.strip().isdigit()}
    salida = [["LOTE", "PRODUCTO", "FE", "FV", "CANTIDAD"] + (["TIPO", "HOJA"] if historial else [])]
    for d in etiquetas_de(con, desde, hasta):
        if (d["es_subreceta"] and not con_sub) or (ids and d["id"] not in ids):
            continue
        fila = [d["lote_final"], d["producto"], d["fe"], d["fv"], d["n_etiquetas"]]
        if historial:
            fila += ["Subreceta" if d["es_subreceta"] else "Pan", ESTADOS_TEXTO.get(d["estado"], d["estado"])]
        salida.append(fila)
    return salida


def nombre_etiquetas():
    desde, hasta = rango_etiquetas()
    if desde is None:
        return "lotes_historial_%s" % dt.date.today().isoformat()
    return "etiquetas_%s" % desde if desde == hasta else "etiquetas_%s_a_%s" % (desde, hasta)


@app.get("/api/etiquetas.csv")
def api_etiquetas_csv():
    texto = io.StringIO()
    csv.writer(texto, delimiter=";", lineterminator="\r\n").writerows(filas_etiquetas(db()))
    return Response("\ufeff" + texto.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=%s.csv" % nombre_etiquetas()})


@app.get("/api/etiquetas.xlsx")
def api_etiquetas_xlsx():
    import openpyxl
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Etiquetas" if rango_etiquetas()[0] else "Historial"
    for fila in filas_etiquetas(db()):
        hoja.append(fila)
    for celda in hoja["A"] + hoja["C"] + hoja["D"]:
        celda.number_format = "@"
    for col, ancho in zip("ABCDEFG", (12, 32, 12, 12, 11, 11, 14)):
        hoja.column_dimensions[col].width = ancho
    salida = io.BytesIO()
    libro.save(salida)
    salida.seek(0)
    return send_file(salida, as_attachment=True, download_name=nombre_etiquetas() + ".xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----------------------------------------------------------------------------
# 6b. Congelador y despachos: a donde sale cada lote de pan (trazabilidad hacia adelante)
# ----------------------------------------------------------------------------
DIAS_POR_CADUCAR_PAN = 7        # amarillo en el congelador: el pan vence en 7 dias o menos
POR_PAGINA_DESPACHOS = 15
MOTIVOS_BAJA_CONGELADOR = {"CADUCADO": "Caducado", "DAÑADO": "Dañado", "CONTAMINADO": "Contaminado",
                           "NO ESTÁ": "No está en el congelador", "ANTERIOR A LA APP": "Salió antes de usar Despachos"}
CAMPOS_DESTINO = ("nombre", "ruc", "contacto", "telefono", "correo", "direccion", "notas")

SALDO_SQL = """
    COALESCE(p.n_etiquetas,0)
    - COALESCE((SELECT SUM(dl.paquetes) FROM despacho_lineas dl JOIN despachos d ON d.id=dl.despacho_id
                WHERE dl.produccion_id=p.id AND d.anulado=0),0)
    - COALESCE((SELECT SUM(b.paquetes) FROM bajas_congelador b WHERE b.produccion_id=p.id AND b.anulado=0),0)"""


def paquetes(texto, que="paquetes"):
    t = str(texto if texto is not None else "").strip()
    if not re.fullmatch(r"\d+", t) or int(t) <= 0:
        raise ErrorApi("Escribe los %s solo con números enteros (mayor que 0)." % que)
    return int(t)


def congelador(con, solo_id=None):
    """Lotes de pan con hoja cerrada que todavia tienen paquetes sin despachar."""
    donde = "p.id=?" if solo_id else "1=1"
    datos = filas(con.execute(
        """SELECT p.id AS produccion_id, p.producto, p.producto_id, p.lote_final, p.fecha, p.vence, p.unidades, p.estado,
                  COALESCE(p.n_etiquetas,0) AS paquetes, pr.vida_util_meses, pr.vida_util_dias, %s AS saldo
           FROM producciones p JOIN productos pr ON pr.id=p.producto_id
           WHERE p.estado='completa' AND pr.produce_ingrediente_id IS NULL AND %s""" % (SALDO_SQL, donde),
        (solo_id,) if solo_id else ()))
    salida = []
    for d in datos:
        if not solo_id and d["saldo"] <= 0:
            continue
        d["caducidad"] = vencimiento(d)
        dias = (dt.date.fromisoformat(d["caducidad"]) - dt.date.today()).days
        d["dias_caducidad"] = dias
        d["semaforo"] = "rojo" if dias < 0 else "amarillo" if dias <= DIAS_POR_CADUCAR_PAN else "verde"
        for k in ("vida_util_meses", "vida_util_dias", "vence"):
            d.pop(k, None)
        salida.append(d)
    orden = {"rojo": 0, "amarillo": 1, "verde": 2}
    salida.sort(key=lambda d: (orden[d["semaforo"]], d["caducidad"], d["fecha"], d["produccion_id"]))
    return salida


def un_destino(con, destino_id):
    fila = con.execute("SELECT * FROM destinos WHERE id=?", (destino_id,)).fetchone()
    if not fila:
        raise ErrorApi("No se encontró el destino.", 404)
    return dict(fila)


def despachos_detalle(con, where="1=1", args=(), limite=None, desde=0):
    sql = """SELECT d.*, de.nombre AS destino, de.ruc, de.contacto, de.telefono, de.correo, de.direccion
             FROM despachos d JOIN destinos de ON de.id=d.destino_id WHERE %s ORDER BY d.id DESC""" % where
    if limite:
        sql += " LIMIT %d OFFSET %d" % (limite, desde)
    datos = filas(con.execute(sql, args))
    for d in datos:
        d["lineas"] = filas(con.execute(
            """SELECT dl.produccion_id, dl.paquetes, p.producto, p.lote_final, p.fecha, p.vence,
                      pr.vida_util_meses, pr.vida_util_dias
               FROM despacho_lineas dl JOIN producciones p ON p.id=dl.produccion_id JOIN productos pr ON pr.id=p.producto_id
               WHERE dl.despacho_id=? ORDER BY dl.id""", (d["id"],)))
        for li in d["lineas"]:
            li["caducidad"] = vencimiento(li)
            for k in ("vida_util_meses", "vida_util_dias", "vence"):
                li.pop(k, None)
        d["total"] = sum(li["paquetes"] for li in d["lineas"])
    return datos


def despachos_de_produccion(con, pid):
    """A donde salio un lote de pan (sin los despachos anulados), sus bajas en el congelador y lo que queda."""
    entregas = filas(con.execute(
        """SELECT d.id AS despacho_id, d.fecha, dl.paquetes, de.nombre AS destino, de.ruc, de.contacto, de.telefono, de.correo, de.direccion
           FROM despacho_lineas dl JOIN despachos d ON d.id=dl.despacho_id JOIN destinos de ON de.id=d.destino_id
           WHERE dl.produccion_id=? AND d.anulado=0 ORDER BY d.fecha, d.id""", (pid,)))
    bajas = filas(con.execute("SELECT id, paquetes, motivo, comentario, fecha_hora, persona FROM bajas_congelador "
                              "WHERE produccion_id=? AND anulado=0 ORDER BY id", (pid,)))
    for b in bajas:
        b["motivo_texto"] = MOTIVOS_BAJA_CONGELADOR.get(b["motivo"], b["motivo"])
    fila = con.execute("SELECT %s AS saldo FROM producciones p WHERE p.id=?" % SALDO_SQL, (pid,)).fetchone()
    return entregas, bajas, (fila["saldo"] if fila else 0)


@app.get("/api/destinos")
def api_destinos():
    con = db()
    donde = "1=1" if request.args.get("todos") == "1" else "de.activo=1"
    return jsonify(filas(con.execute(
        """SELECT de.*, (SELECT COUNT(*) FROM despachos d WHERE d.destino_id=de.id AND d.anulado=0) AS n_despachos,
                  (SELECT MAX(d.fecha) FROM despachos d WHERE d.destino_id=de.id AND d.anulado=0) AS ultimo
           FROM destinos de WHERE %s ORDER BY de.activo DESC, de.nombre""" % donde)))


def datos_destino(d):
    datos = {k: " ".join(str(d.get(k) or "").split()) for k in CAMPOS_DESTINO}
    datos["nombre"] = datos["nombre"].upper()
    if not datos["nombre"]:
        raise ErrorApi("Escribe el nombre del destino o cliente.")
    if datos["ruc"] and not re.fullmatch(r"\d{10}(\d{3})?", datos["ruc"]):
        raise ErrorApi("El RUC o la cédula debe tener 10 o 13 números.")
    if datos["telefono"] and not re.fullmatch(r"[\d +()-]{6,20}", datos["telefono"]):
        raise ErrorApi("El teléfono solo puede tener números.")
    if datos["correo"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", datos["correo"]):
        raise ErrorApi("El correo no es válido.")
    return datos


@app.post("/api/destinos")
def api_destino_nuevo():
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    datos = datos_destino(d)
    previo = con.execute("SELECT id, activo FROM destinos WHERE nombre=?", (datos["nombre"],)).fetchone()
    if previo and previo["activo"]:
        raise ErrorApi("Ya existe un destino con el nombre %s." % datos["nombre"])
    if previo:          # estaba eliminado: vuelve con los datos nuevos
        con.execute("UPDATE destinos SET %s, activo=1 WHERE id=?" % ", ".join(k + "=?" for k in CAMPOS_DESTINO),
                    [datos[k] for k in CAMPOS_DESTINO] + [previo["id"]])
        nuevo_id = previo["id"]
    else:
        nuevo_id = con.execute("INSERT INTO destinos(%s, creado_en, creado_por) VALUES(%s,?,?)" % (
            ",".join(CAMPOS_DESTINO), ",".join("?" * len(CAMPOS_DESTINO))),
            [datos[k] for k in CAMPOS_DESTINO] + [ahora(), persona]).lastrowid
    anotar(con, persona, "DESTINO", "Destino agregado: %s" % datos["nombre"])
    con.commit()
    return jsonify(un_destino(con, nuevo_id))


@app.post("/api/destinos/<int:destino_id>")
def api_destino_editar(destino_id):
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    actual = un_destino(con, destino_id)
    if d.get("eliminar"):
        con.execute("UPDATE destinos SET activo=0 WHERE id=?", (destino_id,))
        anotar(con, persona, "DESTINO", "Destino eliminado de la lista: %s (sus despachos siguen en el historial)" % actual["nombre"])
        con.commit()
        return jsonify(ok=True)
    datos = datos_destino(d)
    otro = con.execute("SELECT id FROM destinos WHERE nombre=? AND id<>?", (datos["nombre"], destino_id)).fetchone()
    if otro:
        raise ErrorApi("Ya existe otro destino con el nombre %s." % datos["nombre"])
    con.execute("UPDATE destinos SET %s WHERE id=?" % ", ".join(k + "=?" for k in CAMPOS_DESTINO),
                [datos[k] for k in CAMPOS_DESTINO] + [destino_id])
    anotar(con, persona, "DESTINO", "Destino editado: %s" % datos["nombre"])
    con.commit()
    return jsonify(un_destino(con, destino_id))


@app.get("/api/congelador")
def api_congelador():
    con = db()
    return jsonify(lotes=congelador(con), motivos=MOTIVOS_BAJA_CONGELADOR, dias_por_caducar=DIAS_POR_CADUCAR_PAN,
                   primer_uso=not con.execute("SELECT 1 FROM despachos").fetchone())


@app.post("/api/despachos")
def api_despacho_nuevo():
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    destino = un_destino(con, d.get("destino_id"))
    if not destino["activo"]:
        raise ErrorApi("El destino %s fue eliminado de la lista." % destino["nombre"])
    fecha = str(d.get("fecha") or dt.date.today().isoformat())[:10]
    try:
        if dt.date.fromisoformat(fecha) > dt.date.today():
            raise ErrorApi("La fecha del despacho no puede ser futura.")
    except ValueError:
        raise ErrorApi("Fecha no válida.")
    items, vistos = [], set()
    for it in d.get("items") or []:
        if not str(it.get("paquetes") or "").strip():
            continue
        pid = int(it.get("produccion_id") or 0)
        if pid in vistos:
            raise ErrorApi("Un lote aparece dos veces en el despacho.")
        vistos.add(pid)
        lote = congelador(con, pid)
        if not lote:
            raise ErrorApi("Ese lote no está en el congelador (la hoja debe estar cerrada).")
        lote = lote[0]
        n = paquetes(it.get("paquetes"))
        if n > lote["saldo"]:
            raise ErrorApi("Del lote %s (%s) solo quedan %d paquetes en el congelador." % (lote["lote_final"], lote["producto"], max(lote["saldo"], 0)))
        if fecha < lote["fecha"]:
            raise ErrorApi("El lote %s se elaboró el %s: no puede salir antes." % (lote["lote_final"], lote["fecha"]))
        items.append((lote, n))
    if not items:
        raise ErrorApi("Escribe cuántos paquetes salen de al menos un lote.")
    obs = " ".join(str(d.get("observaciones") or "").split())
    nuevo = con.execute("INSERT INTO despachos(destino_id,fecha,fecha_hora,persona,observaciones) VALUES(?,?,?,?,?)",
                        (destino["id"], fecha, ahora(), persona, obs)).lastrowid
    for lote, n in items:
        con.execute("INSERT INTO despacho_lineas(despacho_id,produccion_id,paquetes) VALUES(?,?,?)", (nuevo, lote["produccion_id"], n))
        anotar(con, persona, "DESPACHO", "Despacho N.º %d a %s: %d paquete(s) de %s lote %s" % (
            nuevo, destino["nombre"], n, lote["producto"], lote["lote_final"]), produccion_id=lote["produccion_id"])
    con.commit()
    return jsonify(despachos_detalle(con, "d.id=?", (nuevo,))[0])


@app.get("/api/despachos")
def api_despachos():
    con = db()
    pagina = max(1, request.args.get("pagina", type=int) or 1)
    total = con.execute("SELECT COUNT(*) FROM despachos").fetchone()[0]
    return jsonify(datos=despachos_detalle(con, limite=POR_PAGINA_DESPACHOS, desde=(pagina - 1) * POR_PAGINA_DESPACHOS),
                   total=total, pagina=pagina, por_pagina=POR_PAGINA_DESPACHOS)


@app.get("/api/despachos/<int:despacho_id>")
def api_despacho(despacho_id):
    datos = despachos_detalle(db(), "d.id=?", (despacho_id,))
    if not datos:
        raise ErrorApi("No se encontró el despacho.", 404)
    return jsonify(datos[0])


@app.post("/api/despachos/<int:despacho_id>/anular")
def api_despacho_anular(despacho_id):
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    motivo = " ".join(str(d.get("motivo") or "").split())
    if not motivo:
        raise ErrorApi("Escribe por qué se anula el despacho.")
    desp = despachos_detalle(con, "d.id=?", (despacho_id,))
    if not desp:
        raise ErrorApi("No se encontró el despacho.", 404)
    if desp[0]["anulado"]:
        raise ErrorApi("Ese despacho ya estaba anulado.")
    con.execute("UPDATE despachos SET anulado=1, anulado_en=?, anulado_por=?, motivo_anulacion=? WHERE id=?",
                (ahora(), persona, motivo, despacho_id))
    anotar(con, persona, "DESPACHO ANULADO", "Despacho N.º %d a %s anulado (%s): los paquetes vuelven al congelador" % (
        despacho_id, desp[0]["destino"], motivo))
    con.commit()
    return jsonify(ok=True)


@app.post("/api/congelador/bajas")
def api_congelador_baja():
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    motivo = str(d.get("motivo") or "").upper()
    if motivo not in MOTIVOS_BAJA_CONGELADOR or motivo == "ANTERIOR A LA APP":
        raise ErrorApi("Elige el motivo.")
    lote = congelador(con, int(d.get("produccion_id") or 0))
    if not lote:
        raise ErrorApi("Ese lote no está en el congelador.")
    lote = lote[0]
    n = paquetes(d.get("paquetes"))
    if n > lote["saldo"]:
        raise ErrorApi("Del lote %s solo quedan %d paquetes en el congelador." % (lote["lote_final"], max(lote["saldo"], 0)))
    comentario = " ".join(str(d.get("comentario") or "").split())
    con.execute("INSERT INTO bajas_congelador(produccion_id,paquetes,motivo,comentario,fecha_hora,persona) VALUES(?,?,?,?,?,?)",
                (lote["produccion_id"], n, motivo, comentario, ahora(), persona))
    anotar(con, persona, "BAJA CONGELADOR", "%s lote %s: %d paquete(s) dados de baja (%s)%s" % (
        lote["producto"], lote["lote_final"], n, MOTIVOS_BAJA_CONGELADOR[motivo].lower(), ": " + comentario if comentario else ""),
        produccion_id=lote["produccion_id"])
    con.commit()
    return jsonify(ok=True)


@app.post("/api/congelador/bajas/<int:baja_id>/anular")
def api_congelador_baja_anular(baja_id):
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    motivo = " ".join(str(d.get("motivo") or "").split())
    if not motivo:
        raise ErrorApi("Escribe por qué se anula.")
    fila = con.execute("SELECT b.*, p.producto, p.lote_final FROM bajas_congelador b JOIN producciones p ON p.id=b.produccion_id "
                       "WHERE b.id=? AND b.anulado=0", (baja_id,)).fetchone()
    if not fila:
        raise ErrorApi("No se encontró la baja.", 404)
    con.execute("UPDATE bajas_congelador SET anulado=1, anulado_en=?, anulado_por=? WHERE id=?", (ahora(), persona, baja_id))
    anotar(con, persona, "BAJA CONGELADOR ANULADA", "%s lote %s: baja de %d paquete(s) anulada (%s)" % (
        fila["producto"], fila["lote_final"], fila["paquetes"], motivo), produccion_id=fila["produccion_id"])
    con.commit()
    return jsonify(ok=True)


@app.post("/api/congelador/limpiar")
def api_congelador_limpiar():
    """Puesta en marcha: los lotes elaborados hasta una fecha salieron antes de usar Despachos."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    hasta = str(d.get("hasta") or "")[:10]
    try:
        dt.date.fromisoformat(hasta)
    except ValueError:
        raise ErrorApi("Elige la fecha.")
    n = 0
    for lote in congelador(con):
        if lote["fecha"] <= hasta:
            con.execute("INSERT INTO bajas_congelador(produccion_id,paquetes,motivo,comentario,fecha_hora,persona) VALUES(?,?,?,?,?,?)",
                        (lote["produccion_id"], lote["saldo"], "ANTERIOR A LA APP", "Puesta en marcha de Despachos", ahora(), persona))
            n += 1
    anotar(con, persona, "BAJA CONGELADOR", "Puesta en marcha: %d lote(s) elaborados hasta el %s salieron antes de usar Despachos" % (n, hasta))
    con.commit()
    return jsonify(ok=True, lotes=n)


@app.get("/api/despachos.xlsx")
def api_despachos_xlsx():
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    con = db()
    libro = openpyxl.Workbook()
    linea = Side(style="thin", color="B7BDCB")
    borde = Border(left=linea, right=linea, top=linea, bottom=linea)
    cab = PatternFill("solid", fgColor="2C344E")
    alinear = Alignment(vertical="center", wrap_text=True)

    def tabla(hoja, titulo, encabezados, datos, anchos):
        hoja["A1"] = titulo
        hoja["A1"].font = Font(bold=True, size=14, color="2C344E")
        hoja["A2"] = "San Francisco FoodService USFQ · descargado el %s" % dt.datetime.now().strftime("%d/%m/%Y %H:%M")
        hoja["A2"].font = Font(italic=True, size=9, color="5F677B")
        for c, texto in enumerate(encabezados, 1):
            celda = hoja.cell(row=4, column=c, value=texto)
            celda.font, celda.fill, celda.border = Font(bold=True, color="FFFFFF"), cab, borde
            celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for n, fila in enumerate(datos, 5):
            for c, v in enumerate(fila, 1):
                celda = hoja.cell(row=n, column=c, value=v)
                celda.border, celda.alignment = borde, alinear
        for c, ancho in enumerate(anchos, 1):
            hoja.column_dimensions[openpyxl.utils.get_column_letter(c)].width = ancho
        hoja.freeze_panes = "A5"
        hoja.auto_filter.ref = "A4:%s%d" % (openpyxl.utils.get_column_letter(len(encabezados)), max(4, len(datos) + 4))

    d_ = lambda f: dt.date.fromisoformat(f[:10]).strftime("%d/%m/%Y") if f else None
    filas1 = []
    for d in reversed(despachos_detalle(con)):
        for li in d["lineas"]:
            filas1.append(["%04d" % d["id"], d_(d["fecha"]), d["destino"], d["ruc"] or None, d["telefono"] or None,
                           li["lote_final"], li["producto"], d_(li["fecha"]), d_(li["caducidad"]), li["paquetes"],
                           d["persona"], "Anulado: " + d["motivo_anulacion"] if d["anulado"] else "Vigente", d["observaciones"] or None])
    h1 = libro.active
    h1.title = "Despachos"
    tabla(h1, "Despachos de pan", ["N.º despacho", "Fecha", "Destino", "RUC / cédula", "Teléfono", "Lote", "Producto",
                                   "Elaboración", "Vence", "Paquetes", "Registrado por", "Estado", "Observaciones"],
          filas1, (11, 12, 24, 15, 14, 13, 28, 12, 12, 10, 16, 22, 28))
    h2 = libro.create_sheet("Congelador")
    tabla(h2, "Lotes en el congelador", ["Lote", "Producto", "Elaboración", "Vence", "Paquetes del lote", "Quedan", "Semáforo"],
          [[l["lote_final"], l["producto"], d_(l["fecha"]), d_(l["caducidad"]), l["paquetes"], l["saldo"],
            {"rojo": "Caducado", "amarillo": "Por caducar", "verde": "Con buen tiempo"}[l["semaforo"]]] for l in congelador(con)],
          (13, 28, 12, 12, 12, 10, 16))
    h3 = libro.create_sheet("Bajas congelador")
    tabla(h3, "Bajas en el congelador", ["Fecha", "Lote", "Producto", "Paquetes", "Motivo", "Comentario", "Registrado por", "Estado"],
          [[b["fecha_hora"][:16], b["lote_final"], b["producto"], b["paquetes"], MOTIVOS_BAJA_CONGELADOR.get(b["motivo"], b["motivo"]),
            b["comentario"] or None, b["persona"], "Anulada" if b["anulado"] else "Vigente"]
           for b in con.execute("SELECT b.*, p.lote_final, p.producto FROM bajas_congelador b JOIN producciones p ON p.id=b.produccion_id ORDER BY b.id")],
          (17, 13, 28, 10, 26, 30, 16, 10))
    h4 = libro.create_sheet("Destinos")
    tabla(h4, "Destinos y clientes", ["Nombre", "RUC / cédula", "Contacto", "Teléfono", "Correo", "Dirección", "Notas", "Despachos", "En la lista"],
          [[x["nombre"], x["ruc"] or None, x["contacto"] or None, x["telefono"] or None, x["correo"] or None, x["direccion"] or None,
            x["notas"] or None, x["n"], "Sí" if x["activo"] else "No"]
           for x in con.execute("SELECT de.*, (SELECT COUNT(*) FROM despachos d WHERE d.destino_id=de.id AND d.anulado=0) AS n "
                                "FROM destinos de ORDER BY de.nombre")],
          (26, 15, 20, 14, 24, 30, 24, 10, 10))
    for hoja in (h1, h2, h3):
        for fila in hoja.iter_rows(min_row=5):
            for celda in fila:
                if celda.column_letter in ("A", "F") and hoja is h1 or celda.column_letter == "A" and hoja is h2 or celda.column_letter == "B" and hoja is h3:
                    celda.number_format = "@"
    salida = io.BytesIO()
    libro.save(salida)
    salida.seek(0)
    return send_file(salida, as_attachment=True, download_name="Despachos_%s.xlsx" % dt.date.today().strftime("%d-%m-%Y"),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----------------------------------------------------------------------------
# 7. Consulta de trazabilidad
# ----------------------------------------------------------------------------
def traza_atras(con, pid, vistos):
    """Hoja de una produccion con sus lotes; si usa una subreceta, incluye la produccion de esa subreceta."""
    hoja = hoja_de(con, pid)
    for c in hoja["componentes"]:          # Mix Bread: la hoja completa de cada pan que lo forma
        if c["origen_id"] not in vistos:
            vistos.add(c["origen_id"])
            c["produccion"] = traza_atras(con, c["origen_id"], vistos)
    for li in hoja["lineas"]:
        for lote in li["lotes"]:
            if lote["quitado_en"] or lote["origen_lote"] != "interno":
                continue
            sub = con.execute(
                """SELECT p.id FROM producciones p JOIN productos pr ON pr.id=p.producto_id
                   WHERE p.lote_final=? AND pr.produce_ingrediente_id=? AND p.estado<>'anulada'""",
                (lote["lote"], li["ingrediente_id"])).fetchall()
            lote["subproducciones"] = []
            for s in sub:
                if s["id"] not in vistos:
                    vistos.add(s["id"])
                    lote["subproducciones"].append(traza_atras(con, s["id"], vistos))
    return hoja


@app.get("/api/traza/final")
def api_traza_final():
    codigo = (request.args.get("lote") or "").strip()
    if not codigo:
        raise ErrorApi("Escribe el código de lote del pan.")
    con = db()
    ids = [f["id"] for f in con.execute(
        "SELECT id FROM producciones WHERE UPPER(REPLACE(lote_final,' ',''))=UPPER(REPLACE(?,' ','')) AND estado<>'anulada' ORDER BY id",
        (codigo,))]
    vistos = set(ids)
    producciones = []
    for i in ids:
        hoja = traza_atras(con, i, vistos)
        hoja["despachos"], hoja["bajas_congelador"], hoja["en_congelador"] = despachos_de_produccion(con, i)
        producciones.append(hoja)
    return jsonify(lote=codigo, producciones=producciones)


def traza_adelante(con, lote_id, camino, vistos, salida):
    """Producciones que usaron un lote, directamente o a traves de una subreceta."""
    usos = con.execute(
        """SELECT DISTINCT p.id, p.producto, p.fecha, p.lote_final, p.estado, p.unidades, li.etiqueta,
                  pr.produce_ingrediente_id
           FROM produccion_lotes pl JOIN produccion_lineas li ON li.id=pl.linea_id
           JOIN producciones p ON p.id=li.produccion_id JOIN productos pr ON pr.id=p.producto_id
           WHERE pl.lote_id=? AND pl.quitado_en IS NULL AND p.estado<>'anulada' ORDER BY p.id""", (lote_id,)).fetchall()
    for u in usos:
        clave = (u["id"], lote_id)
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append({"produccion_id": u["id"], "producto": u["producto"], "fecha": u["fecha"],
                       "lote_final": u["lote_final"], "estado": u["estado"], "unidades": u["unidades"],
                       "renglon": u["etiqueta"], "via": camino, "es_subreceta": bool(u["produce_ingrediente_id"])})
        mixes_de(con, u["id"], camino + ["%s %s" % (u["producto"], u["lote_final"])], vistos, salida)
        if u["produce_ingrediente_id"]:
            interno = con.execute("SELECT id FROM lotes WHERE ingrediente_id=? AND lote=?",
                                  (u["produce_ingrediente_id"], u["lote_final"])).fetchone()
            if interno:
                traza_adelante(con, interno["id"], camino + ["%s %s" % (u["producto"], u["lote_final"])], vistos, salida)


def mixes_de(con, produccion_id, camino, vistos, salida):
    """Mix Bread que usaron unidades de una produccion."""
    for m in con.execute(
            """SELECT m.id, m.producto, m.fecha, m.lote_final, m.estado, m.unidades, al.unidades AS tomadas
               FROM armado_lotes al JOIN producciones m ON m.id=al.produccion_id
               WHERE al.origen_id=? AND m.estado<>'anulada' ORDER BY m.id""", (produccion_id,)).fetchall():
        if ("mix", m["id"], produccion_id) in vistos:
            continue
        vistos.add(("mix", m["id"], produccion_id))
        salida.append({"produccion_id": m["id"], "producto": m["producto"], "fecha": m["fecha"], "lote_final": m["lote_final"],
                       "estado": m["estado"], "unidades": m["unidades"], "renglon": "%s unidades" % fmt(m["tomadas"]),
                       "via": camino, "es_subreceta": False})


@app.get("/api/traza/ingrediente")
def api_traza_ingrediente():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        raise ErrorApi("Escribe al menos 2 caracteres del lote del ingrediente.")
    con = db()
    lotes = filas(con.execute(LOTE_SQL + " WHERE l.lote LIKE ? ORDER BY l.id DESC LIMIT 30", ("%" + q + "%",)))
    for l in lotes:
        l["usos"] = []
        traza_adelante(con, l["id"], [], set(), l["usos"])
        destinos = {}
        for u in l["usos"]:
            u["despachos"], _, u["en_congelador"] = despachos_de_produccion(con, u["produccion_id"])
            if u["es_subreceta"]:
                u["despachos"], u["en_congelador"] = [], 0
            for e in u["despachos"]:
                x = destinos.setdefault(e["destino"], dict(e, paquetes=0, lotes=[]))
                x["paquetes"] += e["paquetes"]
                if u["lote_final"] not in x["lotes"]:
                    x["lotes"].append(u["lote_final"])
        l["destinos"] = sorted(destinos.values(), key=lambda x: x["destino"])
        l["aperturas"] = filas(con.execute(
            "SELECT abierto_en, abierto_por, cerrado_en, cerrado_por FROM aperturas WHERE lote_id=? ORDER BY id", (l["id"],)))
        l["recepciones"] = filas(con.execute(
            """SELECT r.fecha_ingreso, r.proveedor, r.cantidad, r.caducidad, r.texto_original, a.producto
               FROM recepciones r JOIN alias a ON a.id=r.alias_id WHERE r.lote_id=? ORDER BY r.fecha_ingreso""", (l["id"],)))
        l["salidas_areas"] = filas(con.execute(
            "SELECT area, cantidad, unidad, motivo, fecha_hora, persona FROM salidas_areas WHERE lote_id=? AND anulado=0 AND area<>'BAJA' ORDER BY id",
            (l["id"],)))
        l["bajas"] = filas(con.execute(
            """SELECT id, lugar, cantidad, unidad, motivo, comentario, fecha_hora, persona FROM bajas WHERE lote_id=? AND anulado=0
               UNION ALL
               SELECT NULL, 'principal', cantidad, unidad, motivo, '', fecha_hora, persona FROM salidas_areas
               WHERE lote_id=? AND anulado=0 AND area='BAJA' ORDER BY fecha_hora""", (l["id"], l["id"])))
    return jsonify(lotes)


# ----------------------------------------------------------------------------
# 8. Administracion
# ----------------------------------------------------------------------------
@app.get("/api/avisos")
def api_avisos():
    """Avisos sin revisar de un área (bodega o produccion)."""
    area_aviso = request.args.get("area") or ""
    con = db()
    datos = filas(con.execute(
        """SELECT a.id, a.texto, a.creado_en, a.creado_por, a.lote_id, a.tipo, l.lote, l.foto, l.origen, l.caducidad, i.nombre AS ingrediente,
                  (SELECT COALESCE(SUM(sc.cantidad),0) FROM salidas_cocina sc WHERE sc.lote_id=l.id) AS tomado_cocina,
                  (SELECT al.contenido FROM alias al WHERE al.ingrediente_id=l.ingrediente_id AND al.en_catalogo=1 AND al.ignorar=0 ORDER BY al.id LIMIT 1) AS contenido,
                  (SELECT al.unidad_contenido FROM alias al WHERE al.ingrediente_id=l.ingrediente_id AND al.en_catalogo=1 AND al.ignorar=0 ORDER BY al.id LIMIT 1) AS unidad_contenido,
                  (SELECT COALESCE(SUM(t.cantidad),0) FROM traslados t WHERE t.lote_id=l.id AND t.anulado=0) AS trasladado,
                  EXISTS(SELECT 1 FROM recepciones r WHERE r.lote_id=l.id AND r.anulado=0) AS tiene_recepcion,
                  (SELECT al.producto FROM alias al WHERE al.ingrediente_id=l.ingrediente_id AND al.en_catalogo=1 AND al.ignorar=0 ORDER BY al.id LIMIT 1) AS producto,
                  (SELECT al.empaque FROM alias al WHERE al.ingrediente_id=l.ingrediente_id AND al.en_catalogo=1 AND al.ignorar=0 ORDER BY al.id LIMIT 1) AS empaque
           FROM avisos a LEFT JOIN lotes l ON l.id=a.lote_id LEFT JOIN ingredientes i ON i.id=l.ingrediente_id
           WHERE a.area=? AND a.resuelto_en IS NULL ORDER BY a.id DESC""", (area_aviso,)))
    for a in datos:
        # Lote anotado a mano en bodega secundaria que bodega principal todavia no registro: se resuelve registrando la recepcion
        a["registrar_recepcion"] = bool(area_aviso == "bodega" and a["lote_id"] and a["origen"] == "manual" and not a["tiene_recepcion"])
        # Lote que cocina anoto a mano: bodega secundaria registra cuanto entro
        a["registrar_entrada"] = bool(area_aviso == "produccion" and a["lote_id"] and a["tipo"] == "manual_cocina")
    return jsonify(datos)


@app.post("/api/avisos/<int:aviso_id>/entrada")
def api_aviso_entrada(aviso_id):
    """Bodega secundaria registra cuanto entro de un lote que cocina anoto a mano. Si bodega principal
    no tiene su recepcion, le llega el aviso para registrarla (con la pregunta de si hay mas)."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    av = con.execute("SELECT * FROM avisos WHERE id=? AND resuelto_en IS NULL", (aviso_id,)).fetchone()
    if not av or av["tipo"] != "manual_cocina" or not av["lote_id"]:
        raise ErrorApi("Este aviso ya se resolvió.")
    l = un_lote(con, av["lote_id"])
    emp = con.execute("SELECT empaque, contenido, unidad_contenido FROM alias WHERE ingrediente_id=? AND en_catalogo=1 AND ignorar=0 "
                      "AND COALESCE(empaque,'')<>'' ORDER BY id LIMIT 1", (l["ingrediente_id"],)).fetchone()
    if emp:
        cantidad, unidad = entero_paquetes(datos.get("cantidad"), emp["empaque"], "cuánto entró"), emp["empaque"]
    else:
        cantidad, unidad = numero(datos.get("cantidad")), ""
        if not cantidad or cantidad <= 0:
            raise ErrorApi("Escribe cuánto entró (solo el número).")
    caducidad = str(datos.get("caducidad") or "")[:10]
    try:
        dt.date.fromisoformat(caducidad)
    except ValueError:
        raise ErrorApi("Escribe la fecha de caducidad que dice el empaque.")
    trasladado = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM traslados WHERE lote_id=? AND anulado=0", (l["id"],)).fetchone()[0]
    tomado = con.execute("SELECT COALESCE(SUM(cantidad),0) FROM salidas_cocina WHERE lote_id=?", (l["id"],)).fetchone()[0]
    datos_emp = dict(emp) if emp else {"unidad": unidad}
    if cantidad < max(trasladado, tomado):
        raise ErrorApi("No puede ser menos de lo que cocina ya tomó (%s)." % cantidad_texto(max(trasladado, tomado), datos_emp))
    if cantidad > trasladado:
        con.execute("INSERT INTO traslados(lote_id,cantidad,unidad,fecha_hora,persona) VALUES(?,?,?,?,?)",
                    (l["id"], cantidad - trasladado, unidad, ahora(), persona))
    con.execute("UPDATE lotes SET caducidad=?, en_secundaria=? WHERE id=?", (caducidad, 1 if cantidad > tomado else 0, l["id"]))
    texto_cant = cantidad_texto(cantidad, datos_emp)
    con.execute("UPDATE avisos SET resuelto_en=?, resuelto_por=?, comentario=? WHERE id=?",
                (ahora(), persona, "Entraron %s a bodega secundaria." % texto_cant, aviso_id))
    if not con.execute("SELECT 1 FROM recepciones WHERE lote_id=? AND anulado=0", (l["id"],)).fetchone():
        ing = con.execute("SELECT nombre FROM ingredientes WHERE id=?", (l["ingrediente_id"],)).fetchone()
        con.execute("INSERT INTO avisos(area,lote_id,texto,creado_en,creado_por,tipo) VALUES(?,?,?,?,?,?)",
                    ("bodega", l["id"], "%s registró que entraron a bodega secundaria %s del lote %s de %s (caduca el %s), que cocina anotó a mano "
                     "porque no aparecía. Registra su recepción con el mismo código y la cantidad total que llegó."
                     % (persona, texto_cant, l["lote"], ing["nombre"], dt.date.fromisoformat(caducidad).strftime("%d/%m/%Y")),
                     ahora(), persona, "manual_secundaria"))
    else:        # ya tiene recepcion: solo se informa, lo que paso a secundaria ya se desconto de bodega principal
        ing = con.execute("SELECT nombre FROM ingredientes WHERE id=?", (l["ingrediente_id"],)).fetchone()
        con.execute("INSERT INTO avisos(area,lote_id,texto,creado_en,creado_por,tipo) VALUES(?,?,?,?,?,?)",
                    ("bodega", l["id"], "%s registró que pasaron a bodega secundaria %s del lote %s de %s, que cocina anotó a mano "
                     "porque no aparecía. Ya se descontó de bodega principal: revisa que coincida con lo que hay."
                     % (persona, texto_cant, l["lote"], ing["nombre"]), ahora(), persona, ""))
    anotar(con, persona, "Entrada de lote anotado por cocina", "Lote %s: entraron %s" % (l["lote"], texto_cant), lote_id=l["id"])
    con.commit()
    return jsonify(ok=True)


@app.post("/api/avisos/<int:aviso_id>/revisado")
def api_aviso_revisado(aviso_id):
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    aviso = con.execute("SELECT * FROM avisos WHERE id=?", (aviso_id,)).fetchone()
    if not aviso:
        raise ErrorApi("Ese aviso no existe.", 404)
    if aviso["resuelto_en"]:
        raise ErrorApi("Ese aviso ya fue revisado por %s." % aviso["resuelto_por"])
    comentario = str(d.get("comentario") or "").strip()
    con.execute("UPDATE avisos SET resuelto_en=?, resuelto_por=?, comentario=? WHERE id=?", (ahora(), persona, comentario, aviso_id))
    anotar(con, persona, "AVISO_REVISADO", aviso["texto"] + (" Respuesta: " + comentario if comentario else ""), aviso["lote_id"])
    con.commit()
    return jsonify(ok=True)


@app.get("/api/estado")
def api_estado():
    con = db()
    uno = lambda sql: con.execute(sql).fetchone()[0]
    principal = listar_lotes(con, "principal", limite=1000)
    return jsonify(
        abiertos=uno("SELECT COUNT(*) FROM aperturas WHERE cerrado_en IS NULL"),
        en_curso=uno("SELECT COUNT(*) FROM producciones WHERE estado='en_curso'"),
        por_completar=uno("SELECT COUNT(*) FROM producciones WHERE estado='por_completar'"),
        alias_por_asignar=uno("SELECT COUNT(*) FROM alias WHERE ingrediente_id IS NULL AND ignorar=0"),
        lotes_revisar=uno("SELECT COUNT(*) FROM lotes WHERE revisar=1 AND agotado=0"),
        lotes=uno("SELECT COUNT(*) FROM lotes WHERE origen<>'interno'"),
        recepciones_hoy=uno("SELECT COUNT(*) FROM recepciones WHERE origen='app' AND anulado=0 AND fecha_ingreso=date('now','localtime')"),
        caducados_principal=sum(1 for l in principal if l["semaforo"] == "rojo"),
        por_caducar_principal=sum(1 for l in principal if l["semaforo"] == "amarillo"),
        inventario_principal=ultimo_inventario(con, "principal"), inventario_secundaria=ultimo_inventario(con, "secundaria"),
        dias_inventario=DIAS_INVENTARIO,
        congelador=len(congelador(con)),
        hoy=dt.date.today().isoformat())


@app.get("/api/personas")
def api_personas():
    return jsonify(filas(db().execute("SELECT * FROM personas WHERE activo=1 ORDER BY nombre")))


@app.post("/api/personas")
def api_personas_guardar():
    datos = cuerpo()
    con = db()
    if datos.get("eliminar_id"):
        con.execute("UPDATE personas SET activo=0 WHERE id=?", (datos["eliminar_id"],))
    else:
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise ErrorApi("Escribe el nombre.")
        con.execute("INSERT INTO personas(nombre,rol,activo) VALUES(?,?,1) "
                    "ON CONFLICT(nombre) DO UPDATE SET activo=1, rol=excluded.rol",
                    (nombre, str(datos.get("rol") or "").strip()))
    con.commit()
    return api_personas()


def datos_empaque(d):
    empaque = str(d.get("empaque") or "").upper()
    unidad = str(d.get("unidad_contenido") or "").lower()
    contenido = numero(d.get("contenido"))
    if empaque not in EMPAQUES:
        raise ErrorApi("Elige el empaque.")
    if not contenido or contenido <= 0:
        raise ErrorApi("Escribe cuánto trae un %s (solo el número)." % empaque.lower().replace("bidon", "bidón"))
    if unidad not in UNIDADES_CONTENIDO:
        raise ErrorApi("Elige la unidad del contenido.")
    return empaque, contenido, unidad


@app.post("/api/bodega/productos/<int:alias_id>/empaque")
def api_empaque(alias_id):
    """Define (o quita) el empaque de un producto: 1 PAQUETE = 50 kg. Lo ya registrado se pasa a paquetes."""
    d = cuerpo()
    persona = persona_de(d)
    con = db()
    a = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not a:
        raise ErrorApi("Ese producto no existe.", 404)
    if d.get("quitar"):
        con.execute("UPDATE alias SET empaque='', contenido=NULL, unidad_contenido=NULL WHERE id=?", (alias_id,))
        anotar(con, persona, "EMPAQUE", "%s: sin empaque (se registra con unidad libre)" % a["producto"])
        con.commit()
        return jsonify(ok=True)
    empaque, contenido, unidad = datos_empaque(d)
    if a["empaque"] and a["empaque"] != empaque and con.execute("SELECT 1 FROM recepciones WHERE alias_id=? AND unidad=?",
                                                                (alias_id, a["empaque"])).fetchone():
        # lo registrado en el empaque anterior se cuenta como el nuevo empaque (1 a 1)
        con.execute("UPDATE recepciones SET unidad=? WHERE alias_id=? AND unidad=?", (empaque, alias_id, a["empaque"]))
    con.execute("UPDATE alias SET empaque=?, contenido=?, unidad_contenido=? WHERE id=?", (empaque, contenido, unidad, alias_id))
    convertir_a_paquetes(con, alias_id)
    anotar(con, persona, "EMPAQUE", "%s: 1 %s = %s %s" % (a["producto"], empaque.lower(), fmt(contenido), unidad))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/alias")
def api_alias():
    datos = filas(db().execute(
        """SELECT a.*, i.nombre AS ingrediente,
                  (SELECT COUNT(*) FROM recepciones r WHERE r.alias_id=a.id) AS recepciones
           FROM alias a LEFT JOIN ingredientes i ON i.id=a.ingrediente_id
           ORDER BY (a.ingrediente_id IS NULL AND a.ignorar=0) DESC, a.en_catalogo DESC, a.producto"""))
    for a in datos:
        a["foto"] = foto_producto(a["producto"], a["foto"])
    return jsonify(datos)


def lote_en_uso(con, lote_id):
    return bool(con.execute(
        """SELECT 1 FROM lotes l WHERE l.id=? AND (l.en_secundaria=1
             OR EXISTS(SELECT 1 FROM aperturas ap WHERE ap.lote_id=l.id)
             OR EXISTS(SELECT 1 FROM traslados t WHERE t.lote_id=l.id AND t.anulado=0)
             OR EXISTS(SELECT 1 FROM salidas_areas o WHERE o.lote_id=l.id AND o.anulado=0)
             OR EXISTS(SELECT 1 FROM produccion_lotes pl WHERE pl.lote_id=l.id))""", (lote_id,)).fetchone())


@app.post("/api/alias/<int:alias_id>")
def api_alias_asignar(alias_id):
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    alias = con.execute("SELECT * FROM alias WHERE id=?", (alias_id,)).fetchone()
    if not alias:
        raise ErrorApi("Ese producto no existe.", 404)
    ignorar = 1 if datos.get("ignorar") else 0
    ing_id = None
    if not ignorar:
        nuevo = normalizar(datos.get("nuevo") or "")
        if nuevo:
            con.execute("INSERT OR IGNORE INTO ingredientes(nombre,tipo,refrigerado) VALUES(?,?,?)",
                        (nuevo, "lote", 1 if datos.get("refrigerado") else 0))
            ing_id = con.execute("SELECT id FROM ingredientes WHERE nombre=?", (nuevo,)).fetchone()["id"]
        else:
            ing_id = datos.get("ingrediente_id")
            if not con.execute("SELECT 1 FROM ingredientes WHERE id=? AND tipo='lote'", (ing_id,)).fetchone():
                raise ErrorApi("Elige un ingrediente.")
    if alias["ingrediente_id"] and alias["ingrediente_id"] != ing_id:
        # Cambio de asignacion: solo si ninguno de sus lotes se ha movido todavia.
        ids = [f["lote_id"] for f in con.execute(
            "SELECT DISTINCT lote_id FROM recepciones WHERE alias_id=? AND lote_id IS NOT NULL", (alias_id,))]
        if any(lote_en_uso(con, i) for i in ids):
            raise ErrorApi("No se puede cambiar: ya hay lotes de este producto registrados en bodega secundaria, cocina o producción.")
        con.execute("UPDATE recepciones SET lote_id=NULL WHERE alias_id=?", (alias_id,))
        for i in ids:
            if not con.execute("SELECT 1 FROM recepciones WHERE lote_id=?", (i,)).fetchone():
                con.execute("DELETE FROM lotes WHERE id=?", (i,))
    con.execute("UPDATE alias SET ingrediente_id=?, ignorar=?, auto=0 WHERE id=?", (ing_id, ignorar, alias_id))
    nuevos = materializar(con)
    anotar(con, persona, "PRODUCTO_ASIGNADO", "%s → %s" % (
        alias["producto"], "ignorado" if ignorar else con.execute(
            "SELECT nombre FROM ingredientes WHERE id=?", (ing_id,)).fetchone()["nombre"]))
    con.commit()
    return jsonify(ok=True, lotes_nuevos=nuevos)


@app.post("/api/ingredientes")
def api_ingrediente_guardar():
    datos = cuerpo()
    persona_de(datos)
    con = db()
    if datos.get("id"):
        if "recipientes" in datos:
            try:
                n = int(datos.get("recipientes"))
            except (TypeError, ValueError):
                raise ErrorApi("Número de recipientes inválido.")
            if not 0 <= n <= 5:
                raise ErrorApi("Número de recipientes inválido.")
            if n and con.execute("""SELECT 1 FROM aperturas ap JOIN lotes l ON l.id=ap.lote_id
                                    WHERE l.ingrediente_id=? AND ap.recipiente>? AND ap.cerrado_en IS NULL""",
                                 (datos["id"], n)).fetchone():
                raise ErrorApi("Hay lotes abiertos en un recipiente que quitarías. Vacíalo primero en Cocina.")
            con.execute("UPDATE ingredientes SET recipientes=? WHERE id=?", (n, datos["id"]))
        else:
            con.execute("UPDATE ingredientes SET refrigerado=? WHERE id=?", (1 if datos.get("refrigerado") else 0, datos["id"]))
    else:
        nombre = normalizar(datos.get("nombre") or "")
        if not nombre:
            raise ErrorApi("Escribe el nombre del ingrediente.")
        tipo = datos.get("tipo") if datos.get("tipo") in ("lote", "interno", "no_aplica") else "lote"
        con.execute("INSERT OR IGNORE INTO ingredientes(nombre,tipo,refrigerado) VALUES(?,?,?)",
                    (nombre, tipo, 1 if datos.get("refrigerado") else 0))
    con.commit()
    return api_ingredientes()


@app.post("/api/productos")
def api_producto_guardar():
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    nombre = str(datos.get("nombre") or "").strip().upper()
    codigo = str(datos.get("codigo") or "").strip().upper()
    if not nombre or not codigo:
        raise ErrorApi("El producto necesita nombre y código de lote (por ejemplo CR).")
    try:
        meses = max(0, int(datos.get("vida_util_meses") or 1))
        dias = max(1, int(datos.get("vida_util_dias") or DIAS_SUBRECETA)) if datos.get("es_subreceta") and not datos.get("armado") else None
    except (TypeError, ValueError):
        raise ErrorApi("La vida útil debe ser un número entero.")
    produce = None
    armado = 1 if datos.get("armado") else 0
    alimenta = 1 if datos.get("se_alimenta") and datos.get("es_subreceta") and not armado else 0
    if datos.get("es_subreceta") and not armado:
        con.execute("INSERT OR IGNORE INTO ingredientes(nombre,tipo) VALUES(?,?)", (normalizar(nombre), "interno"))
        fila = con.execute("SELECT id, tipo FROM ingredientes WHERE nombre=?", (normalizar(nombre),)).fetchone()
        if fila["tipo"] != "interno":
            raise ErrorApi("Ya existe un ingrediente de proveedor con ese nombre; usa otro nombre para la subreceta.")
        produce = fila["id"]
    try:
        if datos.get("id"):
            con.execute("UPDATE productos SET nombre=?, codigo=?, vida_util_meses=?, vida_util_dias=?, produce_ingrediente_id=?, activo=?, armado=?, se_alimenta=? WHERE id=?",
                        (nombre, codigo, meses, dias, produce, 0 if datos.get("inactivo") else 1, armado, alimenta, datos["id"]))
            pid = datos["id"]
        else:
            pid = con.execute("INSERT INTO productos(nombre,codigo,vida_util_meses,vida_util_dias,produce_ingrediente_id,armado,se_alimenta) VALUES(?,?,?,?,?,?,?)",
                              (nombre, codigo, meses, dias, produce, armado, alimenta)).lastrowid
    except sqlite3.IntegrityError:
        raise ErrorApi("Ya existe un producto con ese nombre.")
    con.execute("DELETE FROM receta WHERE producto_id=?", (pid,))
    con.execute("DELETE FROM composicion WHERE producto_id=?", (pid,))
    if armado:                    # Mix Bread: panes que lo forman (en lugar de ingredientes)
        for orden, comp_id in enumerate(datos.get("componentes") or [], 1):
            comp = con.execute("SELECT id FROM productos WHERE id=? AND armado=0 AND produce_ingrediente_id IS NULL",
                               (comp_id,)).fetchone()
            if not comp or comp["id"] == pid:
                raise ErrorApi("Hay un pan de la mezcla que no es válido.")
            con.execute("INSERT INTO composicion(producto_id,orden,componente_id) VALUES(?,?,?)", (pid, orden, comp["id"]))
    for orden, li in enumerate([] if armado else (datos.get("lineas") or []), 1):
        ing = con.execute("SELECT nombre FROM ingredientes WHERE id=?", (li.get("ingrediente_id"),)).fetchone()
        if not ing:
            raise ErrorApi("Hay un renglón de la receta sin ingrediente.")
        con.execute("INSERT INTO receta(producto_id,orden,etiqueta,ingrediente_id,despues_reposo) VALUES(?,?,?,?,?)",
                    (pid, orden, (str(li.get("etiqueta") or "").strip().upper() or ing["nombre"]), li["ingrediente_id"],
                     1 if li.get("despues_reposo") else 0))
    reposo = 0 if armado or produce else 1      # todos los panes: inicio de masa, masa lista y fin de produccion
    con.execute("UPDATE productos SET lleva_reposo=? WHERE id=?", (reposo, pid))
    anotar(con, persona, "RECETA_EDITADA", nombre)
    con.commit()
    return api_productos()


@app.get("/api/pendientes")
def api_pendientes():
    con = db()
    return jsonify(
        alias=filas(con.execute(
            """SELECT a.id, a.producto, (SELECT COUNT(*) FROM recepciones r WHERE r.alias_id=a.id) AS recepciones
               FROM alias a WHERE a.ingrediente_id IS NULL AND a.ignorar=0 ORDER BY a.producto""")),
        lotes=filas(con.execute(LOTE_SQL + " WHERE l.revisar=1 AND l.agotado=0" + ORDEN_LOTES)),
        hojas=[p for p in resumen_producciones(con, "p.estado IN ('por_completar','en_curso')") if p["faltantes"]])


@app.post("/api/lotes/revisado")
def api_revisado():
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    lote = un_lote(con, datos.get("lote_id"))
    con.execute("UPDATE lotes SET revisar=0 WHERE id=?", (lote["id"],))
    anotar(con, persona, "LOTE_REVISADO", "%s lote %s" % (lote["ingrediente"], lote["lote"]), lote["id"])
    con.commit()
    return jsonify(ok=True)


@app.post("/api/lotes/fusionar")
def api_fusionar():
    """Un lote anotado a mano resulta ser uno que ya estaba en la lista: se pasa todo su historial al correcto."""
    datos = cuerpo()
    persona = persona_de(datos)
    con = db()
    malo, bueno = un_lote(con, datos.get("lote_id")), un_lote(con, datos.get("destino_id"))
    if malo["id"] == bueno["id"] or malo["ingrediente_id"] != bueno["ingrediente_id"]:
        raise ErrorApi("Los dos lotes deben ser distintos y del mismo ingrediente.")
    if malo["origen"] != "manual":
        raise ErrorApi("Solo se puede unir un lote que fue anotado a mano.")
    for tabla in ("aperturas", "produccion_lotes", "recepciones"):
        con.execute("UPDATE %s SET lote_id=? WHERE lote_id=?" % tabla, (bueno["id"], malo["id"]))
    con.execute("UPDATE movimientos SET lote_id=? WHERE lote_id=?", (bueno["id"], malo["id"]))
    con.execute("UPDATE lotes SET en_secundaria=MAX(en_secundaria,?), agotado=0 WHERE id=?",
                (malo["en_secundaria"], bueno["id"]))
    con.execute("DELETE FROM lotes WHERE id=?", (malo["id"],))
    anotar(con, persona, "LOTES_UNIDOS", "%s: «%s» (anotado a mano) era el lote «%s»" % (
        malo["ingrediente"], malo["lote"], bueno["lote"]), bueno["id"])
    con.commit()
    return jsonify(ok=True)


@app.post("/api/admin/agotar_antiguos")
def api_agotar_antiguos():
    """Puesta en marcha: marca como agotados los lotes viejos de bodega principal que ya no existen."""
    datos = cuerpo()
    persona = persona_de(datos)
    antes = str(datos.get("antes_de") or "")
    try:
        dt.date.fromisoformat(antes)
    except ValueError:
        raise ErrorApi("Elige una fecha.")
    con = db()
    cur = con.execute(
        """UPDATE lotes SET agotado=1 WHERE origen='excel' AND agotado=0 AND en_secundaria=0
           AND NOT EXISTS(SELECT 1 FROM aperturas ap WHERE ap.lote_id=lotes.id AND ap.cerrado_en IS NULL)
           AND (SELECT MAX(r.fecha_ingreso) FROM recepciones r WHERE r.lote_id=lotes.id) < ?""", (antes,))
    anotar(con, persona, "AGOTAR_ANTIGUOS", "%d lotes ingresados antes del %s" % (cur.rowcount, antes))
    con.commit()
    return jsonify(ok=True, lotes=cur.rowcount)


@app.get("/api/movimientos")
def api_movimientos():
    """Historial de cambios. Con ?pagina=n devuelve de 30 en 30 con el total; sin pagina, los ultimos (limite)."""
    con = db()
    if request.args.get("pagina"):
        por = 30
        pag = max(1, request.args.get("pagina", 1, type=int))
        total = con.execute("SELECT COUNT(*) FROM movimientos").fetchone()[0]
        datos = filas(con.execute("SELECT * FROM movimientos ORDER BY id DESC LIMIT ? OFFSET ?", (por, (pag - 1) * por)))
        return jsonify(datos=datos, total=total, pagina=pag, por_pagina=por)
    return jsonify(filas(con.execute(
        "SELECT * FROM movimientos ORDER BY id DESC LIMIT ?", (request.args.get("limite", 300, type=int),))))


@app.get("/api/respaldo")
def api_respaldo():
    destino = os.path.join(RESPALDOS, "descarga.db")
    origen, copia = sqlite3.connect(DB), sqlite3.connect(destino)
    origen.backup(copia)
    copia.close()
    origen.close()
    return send_file(destino, as_attachment=True,
                     download_name="trazabilidad_%s.db" % dt.datetime.now().strftime("%Y%m%d_%H%M"))


@app.after_request
def _sin_cache(respuesta):
    # Los celulares guardan copias viejas de la pantalla; asi siempre piden la version actual.
    if request.path == "/" or request.path.startswith("/static/"):
        respuesta.headers["Cache-Control"] = "no-cache"
    return respuesta


@app.get("/")
def inicio():
    return send_from_directory(app.static_folder, "index.html")


# ----------------------------------------------------------------------------
def direccion_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


preparar_base()

if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else PUERTO
    print("\n  TrazaPanUSFQ está funcionando.")
    print("  En esta PC:              http://localhost:%d" % puerto)
    print("  Desde celulares/tablets: http://%s:%d   (misma red wifi)" % (direccion_local(), puerto))
    print("  Para apagar: cerrar esta ventana o Ctrl+C.\n")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=puerto, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=puerto, threaded=True)
