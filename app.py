# app.py — SOLO CARGA DE GASTOS (Mongo, obra manual, formato ARS)

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import streamlit as st
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

st.set_page_config(page_title="Cargar Gastos", page_icon="💼", layout="centered")

# ---- Config DB ----
MONGO_URI = st.secrets.get("MONGO_URI", os.getenv("MONGO_URI", ""))
MONGO_DB_NAME = st.secrets.get("MONGO_DB", os.getenv("MONGO_DB", "tus_gastos_db"))
GASTOS_COLL_NAME = st.secrets.get("MONGO_GASTOS_COLL", os.getenv("MONGO_GASTOS_COLL", "gastos"))

@st.cache_resource(show_spinner=False)
def get_coll():
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI no configurado en .streamlit/secrets.toml o variables de entorno.")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    coll = client[MONGO_DB_NAME][GASTOS_COLL_NAME]
    # índices útiles para tus otras pantallas
    try:
        coll.create_index([("estado", ASCENDING), ("fecha", DESCENDING)], name="idx_estado_fecha")
        coll.create_index([("obra", ASCENDING)], name="idx_obra")
        coll.create_index([("comprobante", ASCENDING)], name="idx_comprobante")
    except Exception:
        pass
    return coll

def _to_datetime(fecha_date: date) -> datetime:
    return datetime(fecha_date.year, fecha_date.month, fecha_date.day)

def format_monto(monto):
    """Mostrar como $ 1.234,56 (solo en UI)."""
    try:
        val = float(monto)
        return f"$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"$ {monto}"

def insertar_gasto(doc: dict) -> bool:
    """Inserta el gasto en Mongo. Devuelve True/False."""
    try:
        coll = get_coll()
        coll.insert_one(doc)
        return True
    except PyMongoError as e:
        st.error(f"❌ Error insertando en MongoDB: {e}")
        return False

# ---- UI: SOLO carga ----
st.title("💼 Carga de gastos")

with st.form("form_gasto", clear_on_submit=False):
    col_a, col_b = st.columns(2)
    with col_a:
        fecha = st.date_input("📅 Fecha", value=date.today())
        concepto = st.text_input("📝 Concepto")
        monto_input = st.number_input("💵 Monto", min_value=0.0, step=0.01, format="%.2f",
                                      help="Monto en pesos argentinos")
    with col_b:
        proveedor = st.text_input("🏪 Proveedor")
        persona = st.text_input("👤 Persona que realizó el gasto")
        metodo_pago = st.selectbox("💳 Método de pago",
                                   ["— Seleccionar —", "Efectivo", "Transferencia",
                                    "Tarjeta de debito", "Tarjeta de credito",
                                    "Cuenta corriente", "Otro"])

    st.markdown("---")
    comprobante = st.text_input("📄 Nº de comprobante *", help="Ej: A-0001-00001234 (obligatorio)")
    obra = st.text_input("🏗️ Obra *", help="Ingresá el nombre de la obra (texto)")

    submit = st.form_submit_button("Continuar ➡️")

# Validaciones mínimas y confirmación
if submit:
    try:
        monto_dec = Decimal(str(monto_input)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        monto_dec = Decimal("0.00")

    errores = []
    if monto_dec <= Decimal("0.00"):
        errores.append("El monto debe ser mayor a 0.")
    if not (obra or "").strip():
        errores.append("Ingresá el nombre de la obra.")
    if not (comprobante or "").strip():
        errores.append("Ingresá el número de comprobante.")
    if not (concepto or "").strip():
        errores.append("Ingresá un concepto.")

    if errores:
        for e in errores:
            st.error(f"⚠️ {e}")
    else:
        # Confirmación (usa dialog si está disponible)
        def render_confirm():
            st.write("Revisá y confirmá los datos del gasto:")
            st.info(f"**📅 Fecha:** {fecha.strftime('%d/%m/%Y')}")
            st.info(f"**📝 Concepto:** {concepto.strip()}")
            st.info(f"**💵 Monto:** {format_monto(monto_dec)}")
            st.info(f"**🏗️ Obra:** {obra.strip()}")
            st.info(f"**📄 Comprobante Nº:** {comprobante.strip()}")
            st.info(f"**🏪 Proveedor:** {(proveedor or '').strip()}")
            st.info(f"**👤 Persona:** {(persona or '').strip()}")
            st.info(f"**💳 Método de pago:** {(metodo_pago or '').strip()}")

            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬅️ Editar", use_container_width=True):
                    st.rerun()
            with c2:
                if st.button("✅ Confirmar y guardar", type="primary", use_container_width=True):
                    doc = {
                        "fecha": _to_datetime(fecha),
                        "concepto": concepto.strip(),
                        "monto": float(monto_dec),          # número en DB
                        "comprobante": comprobante.strip(),
                        "obra": obra.strip(),               # obra como texto
                        "persona": (persona or "").strip(),
                        "proveedor": (proveedor or "").strip(),
                        "metodo_pago": (metodo_pago or "").strip(),
                        "estado": "pendiente",              # para tu vista de aprobación
                    }
                    if insertar_gasto(doc):
                        st.success("✅ ¡Gasto guardado con éxito!")
                        st.balloons()

        if hasattr(st, "dialog"):
            @st.dialog("Confirmar carga")
            def _dlg():
                render_confirm()
            _dlg()
        else:
            st.subheader("Confirmar carga")
            render_confirm()
