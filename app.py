"""Observatorio de Turismo Sostenible — Guatemala.

Tablero del Laboratorio 11 (MLOps). Muestra únicamente la versión aprobada y vigente
que publica el pipeline (`published/CURRENT`); no ejecuta el pipeline ni estimaciones.

Ejecutar con:
    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from dashboard import theme as T
from dashboard import ui
from dashboard.views import estimaciones, evaluacion, inicio

st.set_page_config(
    page_title="Observatorio de Turismo · Guatemala",
    page_icon="🌋",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(T.APP_CSS, unsafe_allow_html=True)

pages = [
    st.Page(inicio.render, title="Inicio", icon="🏠", url_path="inicio", default=True),
    st.Page(estimaciones.render, title="Estimaciones", icon="📈", url_path="estimaciones"),
    st.Page(evaluacion.render, title="Evaluación", icon="✅", url_path="evaluacion"),
]

with st.sidebar:
    st.markdown("### 🌋 Observatorio de Turismo")
    st.caption("Guatemala · visitantes no residentes")
    ui.sidebar_status()

nav = st.navigation(pages)
nav.run()

with st.sidebar:
    st.divider()
    st.caption(
        "Laboratorio 11 · CC3066 Data Science · UVG. "
        "Muestra solo versiones aprobadas y publicadas por el pipeline."
    )
