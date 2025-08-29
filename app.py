
import os
from datetime import date
from decimal import Decimal, InvalidOperation
import contextlib
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import streamlit as st
import psycopg2
from psycopg2 import pool

# -----------------------------
# Configuración de la página
# -----------------------------
st.set_page_config(page_title="Cargar Gastos", page_icon="💼", layout="centered")

# -----------------------------
# Utilidades varios
# -----------------------------
def add_keepalives_to_dsn(dsn: str) -> str:
    """
    Si DATABASE_URL es formato URI (postgresql://...), añadimos parámetros de keepalive
    para evitar caídas por inactividad. Si no es URI, devolvemos dsn sin cambios.
    """
    if not dsn or "://" not in dsn:
        return dsn
    parsed = urlparse(dsn)
    q = parse_qs(parsed.query, keep_blank_values=True)
    # Valores razonables para hosting común (supabase, render, neon, fly, etc.)
    q.setdefault("keepalives", ["1"])
    q.setdefault("keepalives_idle", ["30"])
    q.setdefault("keepalives_interval", ["10"])
    q.setdefault("keepalives_count", ["5"])
    q.setdefault("connect_timeout", ["5"])
    new_query = urlencode({k: v[-1] for k, v in q.items()})
    return urlunparse(parsed._replace(query=new_query))

# -----------------------------
# Configuración DB
# -----------------------------
DATABASE_URL = st.secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))
ENRICHED_DSN = add_keepalives_to_dsn(DATABASE_URL)

@st.cache_resource(show_spinner=False)
def get_pool():
    if not ENRICHED_DSN:
        raise RuntimeError("DATABASE_URL no configurada en secrets o variables de entorno.")
    # Pool 1..8 conexiones, con DSN enriquecido (keepalives) y timeout de conexión
    return pool.SimpleConnectionPool(1, 8, dsn=ENRICHED_DSN)

def _ping(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False

@contextlib.contextmanager
def pooled_conn():
    """
    Obtiene una conexión del pool verificando que esté viva (pre-ping).
    Si falla, cierra esa conexión y toma otra limpia del pool.
    """
    p = get_pool()
    conn = p.getconn()
    try:
        if conn.closed or not _ping(conn):
            # descartamos y pedimos otra
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
            conn = p.getconn()
            # último intento: si la segunda tampoco responde, que dispare excepción
            if conn.closed or not _ping(conn):
                raise RuntimeError("No se pudo obtener una conexión válida del pool.")
        yield conn
    finally:
        try:
            p.putconn(conn)
        except Exception:
            # Si algo salió mal, cerramos la conexión para que el pool se recupere
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass

# -----------------------------
# Utilidades DB
# -----------------------------
@st.cache_data(ttl=60)
def cargar_obras():
    """Devuelve lista de nombres de obras (solo nombre)."""
    try:
        with pooled_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT nombre FROM obras ORDER BY nombre")
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        st.session_state.db_error = f"Error cargando obras: {e}"
        st.session_state.db_connected = False
        return []

def insertar_gasto(fecha, concepto, monto_decimal, comprobante_numero,
                   obra_nombre, proveedor, persona, metodo_pago):
    """Inserta gasto guardando el NOMBRE de la obra en la columna gastos.obra."""
    try:
        comprobante_final = (comprobante_numero or "").strip()
        with pooled_conn() as conn, conn.cursor() as cur:
            cur.execute(
                '''
                INSERT INTO gastos (fecha, concepto, monto, comprobante, obra, persona, proveedor, metodo_pago)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''',
                (fecha, concepto, monto_decimal, comprobante_final, obra_nombre, persona, proveedor, metodo_pago),
            )
            conn.commit()
            return True
    except Exception as e:
        st.session_state.db_error = f"Error insertando gasto: {e}"
        st.session_state.db_connected = False
        return False

def test_connection():
    """Verifica que podamos tomar y devolver una conexión del pool."""
    try:
        p = get_pool()
        conn = p.getconn()
        ok = _ping(conn)
        p.putconn(conn)
        st.session_state.db_connected = bool(ok)
        st.session_state.db_error = None if ok else "Ping a la base falló."
        return bool(ok)
    except Exception as e:
        st.session_state.db_connected = False
        st.session_state.db_error = f"Error conectando a la base de datos: {e}"
        return False

# -----------------------------
# Estado de sesión
# -----------------------------
if "db_connected" not in st.session_state:
    st.session_state.db_connected = False
if "db_error" not in st.session_state:
    st.session_state.db_error = None
if "db_checked_once" not in st.session_state:
    st.session_state.db_checked_once = False
if "enviando" not in st.session_state:
    st.session_state.enviando = False
if "datos_gasto" not in st.session_state:
    st.session_state.datos_gasto = {}
if "mostrar_confirmacion" not in st.session_state:
    st.session_state.mostrar_confirmacion = False  # usado solo en fallback sin dialog
# Flash de éxito
if "flash_ok" not in st.session_state:
    st.session_state.flash_ok = None

# Chequeo automático de conexión (solo 1 vez por sesión)
if not st.session_state.db_checked_once:
    test_connection()
    st.session_state.db_checked_once = True

# Si alguna operación falló previamente, reintentar ping automático en cada rerun
if not st.session_state.db_connected:
    test_connection()

# -----------------------------
# Sidebar: estado de conexión (sin botón)
# -----------------------------
# -----------------------------
# Sidebar: estado de conexión + botón de reintento
# -----------------------------
st.sidebar.header("Base de datos")

if st.sidebar.button("🔄 Reintentar conexión"):
    with st.spinner("Reintentando conexión..."):
        test_connection()
    st.rerun()

if st.session_state.get("db_connected"):
    st.sidebar.success("Conectado ✅")
else:
    st.sidebar.error("Sin conexión ❌")
    if not DATABASE_URL:
        st.sidebar.info("Configura DATABASE_URL en .streamlit/secrets.toml o variable de entorno.")

if st.session_state.get("db_error"):
    st.sidebar.caption(f"Detalle: {st.session_state['db_error']}")

last_check = st.session_state.get("last_db_check")
if last_check:
    st.sidebar.caption("Último chequeo: " + last_check.strftime("%d/%m/%Y %H:%M:%S"))

# Cargar obras SIEMPRE; la propia función gestiona errores y estado
lista_obras = cargar_obras()  # ['Obra A', 'Obra B', ...]

# -----------------------------
# UI
# -----------------------------
st.title("💼 Carga de gastos")

# Mostrar alerta/avisito si hay mensaje pendiente (toast + banner)
if st.session_state.flash_ok:
    if hasattr(st, "toast"):
        st.toast(st.session_state.flash_ok, icon="✅")
    st.success(st.session_state.flash_ok)
    st.session_state.flash_ok = None

if not lista_obras:
    st.info("No hay obras para seleccionar. Verifica la conexión y que existan registros en la tabla `obras`.")

with st.form("form_gasto", clear_on_submit=False):
    col_a, col_b = st.columns(2)
    with col_a:
        fecha = st.date_input("📅 Fecha", value=date.today())
        concepto = st.text_input("📝 Concepto")
        monto_float = st.number_input("💵 Monto", min_value=0.0, step=0.01, format="%.2f",
                                      help="Monto en pesos argentinos")
    with col_b:
        proveedor = st.text_input("🏪 Proveedor")
        persona = st.text_input("👤 Persona que realizó el gasto")
        metodo_pago = st.selectbox("💳 Método de pago", ["— Seleccionar —", "Efectivo", "Transferencia", "Tarjeta de debito", "Tarjeta de credito", "Cuenta corriente", "Otro"])

    st.markdown("---")
    # Campo obligatorio de comprobante (sin select/radio)
    comprobante_numero = st.text_input("📄 Nº de comprobante *", help="Obligatorio. Ej: A-0001-00001234")

    obra_nombre = st.selectbox("🏗️ Obra", ["— Seleccionar —"] + lista_obras, help="Selecciona la obra")
    submit = st.form_submit_button("Continuar ➡️")

# Validaciones y lanzamiento del diálogo
if submit:
    # Convertir a Decimal usando texto para no heredar errores de float
    try:
        monto_decimal = Decimal(str(monto_float)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        monto_decimal = Decimal("0.00")

    campos_validos = True

    concepto = (concepto or "").strip()
    proveedor = (proveedor or "").strip()
    persona = (persona or "").strip()
    comprobante_numero = (comprobante_numero or "").strip()

    if monto_decimal <= Decimal("0.00"):
        st.error("⚠️ El monto debe ser mayor a 0.")
        campos_validos = False

    if obra_nombre == "— Seleccionar —":
        st.error("⚠️ Debe seleccionar una obra.")
        campos_validos = False

    if not comprobante_numero:
        st.error("⚠️ Ingresá el número de comprobante (obligatorio).")
        campos_validos = False

    if not concepto:
        st.error("⚠️ Ingresá un concepto.")
        campos_validos = False

    if not st.session_state.db_connected:
        st.error("⚠️ No hay conexión a la base de datos.")
        campos_validos = False

    if campos_validos:
        st.session_state.datos_gasto = {
            "fecha": fecha,
            "concepto": concepto,
            "monto": monto_decimal,
            "persona": persona,
            "comprobante_numero": comprobante_numero,
            "obra_nombre": obra_nombre,
            "proveedor": proveedor,
            "metodo_pago": metodo_pago,
        }
        # Si existe st.dialog, mostrar overlay modal; si no, usar fallback de pantalla completa
        if hasattr(st, "dialog"):
            @st.dialog("Confirmar carga")
            def confirmar_dialog():
                datos = st.session_state.datos_gasto
                st.write("Revisá y confirmá los datos del gasto:")
                st.info(f"**📅 Fecha:** {datos['fecha'].strftime('%d/%m/%Y')}")
                st.info(f"**📝 Concepto:** {datos['concepto']}")
                st.info(f"**💵 Monto:** ${datos['monto']:.2f}")
                st.info(f"**🏗️ Obra:** {datos['obra_nombre']}")
                st.info(f"**📄 Comprobante Nº:** {datos['comprobante_numero']}")
                st.info(f"**🏪 Proveedor:** {datos['proveedor']}")
                st.info(f"**👤 Persona:** {datos['persona']}")
                st.info(f"**💳 Método de pago:** {datos['metodo_pago']}")

                col_ok, col_cancel = st.columns(2)
                with col_cancel:
                    if st.button("Cancelar", use_container_width=True):
                        # cerrar el diálogo re-ejecutando sin datos
                        st.session_state.datos_gasto = {}
                        st.rerun()
                with col_ok:
                    if st.button("✅ Confirmar y guardar", type="primary", use_container_width=True,
                                 disabled=st.session_state.get("enviando", False)):
                        if not st.session_state.get("enviando", False):
                            st.session_state.enviando = True
                            with st.spinner("Guardando..."):
                                ok = insertar_gasto(
                                    datos["fecha"],
                                    datos["concepto"],
                                    datos["monto"],
                                    datos["comprobante_numero"],
                                    datos["obra_nombre"],  # guarda nombre en columna "obra"
                                    datos["proveedor"],
                                    datos["persona"],
                                    datos["metodo_pago"],
                                )
                            st.session_state.enviando = False
                            if ok:
                                st.session_state.flash_ok = "¡Gasto guardado con éxito!"
                                st.session_state.datos_gasto = {}
                                st.rerun()
                            else:
                                st.error("❌ Error al cargar el gasto. Revisá el mensaje en el sidebar.")
            # abrir el diálogo inmediatamente
            confirmar_dialog()
        else:
            # Fallback sin modal (pantalla de confirmación)
            st.session_state.mostrar_confirmacion = True
            st.rerun()

# -----------------------------
# Fallback: confirmación a pantalla completa (si no hay st.dialog)
# -----------------------------
if st.session_state.mostrar_confirmacion and not hasattr(st, "dialog"):
    st.subheader("✅ Confirmá los datos")
    datos = st.session_state.datos_gasto

    col1, col2 = st.columns(2)
    with col1:
        st.info(f"**📅 Fecha:** {datos['fecha'].strftime('%d/%m/%Y')}")
        st.info(f"**📝 Concepto:** {datos['concepto']}")
        st.info(f"**💵 Monto:** ${datos['monto']:.2f}")
        st.info(f"**🏗️ Obra:** {datos['obra_nombre']}")
    with col2:
        st.info(f"**📄 Comprobante Nº:** {datos['comprobante_numero']}")
        st.info(f"**🏪 Proveedor:** {datos['proveedor']}")
        st.info(f"**👤 Persona:** {datos['persona']}")
        st.info(f"**💳 Método de pago:** {datos['metodo_pago']}")

    col_ok, col_cancel = st.columns(2)
    with col_cancel:
        if st.button("⬅️ Volver y editar", use_container_width=True):
            st.session_state.mostrar_confirmacion = False
            st.rerun()

    with col_ok:
        if st.button("✅ CONFIRMAR Y GUARDAR", type="primary", use_container_width=True,
                     disabled=st.session_state.get("enviando", False)):
            if not st.session_state.get("enviando", False):
                st.session_state.enviando = True  # evita doble click
                with st.spinner("Guardando..."):
                    ok = insertar_gasto(
                        datos["fecha"],
                        datos["concepto"],
                        datos["monto"],
                        datos["comprobante_numero"],
                        datos["obra_nombre"],  # guarda nombre en columna "obra"
                        datos["proveedor"],
                        datos["persona"],
                        datos["metodo_pago"],
                    )
                st.session_state.enviando = False
                if ok:
                    st.session_state.flash_ok = "¡Gasto guardado con éxito!"
                    st.session_state.mostrar_confirmacion = False
                    st.session_state.datos_gasto = {}
                    st.rerun()
                else:
                    st.error("❌ Error al cargar el gasto. Revisá el mensaje en el sidebar.")

# -----------------------------
# Notas
# -----------------------------
st.caption("Conexión automática con pool + keepalives. Guardando nombre de obra en gastos.obra. Sugerido: gastos.monto NUMERIC(12,2).")
