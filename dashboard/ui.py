"""Utilidades compartidas de la interfaz: lectura cacheada de la versión vigente y componentes comunes."""

from __future__ import annotations

import html

import streamlit as st

from . import published as PUB

# La versión se vuelve a validar como máximo cada TTL segundos; si CURRENT cambia, se recarga de inmediato.
CACHE_TTL_SECONDS = 30


# ---------------------------------------------------------------------------
# Lectura de la versión aprobada (solo lectura; cambiar filtros no recarga ni calcula nada)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _load(root: str, token: str | None) -> PUB.LoadResult:
    """`token` es el contenido de CURRENT: un cambio de versión invalida la caché."""
    return PUB.load_current(root)


def current_result() -> PUB.LoadResult:
    root = PUB.published_root()
    try:
        token = (root / PUB.CURRENT_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        token = None
    return _load(str(root), token)


def require_version() -> PUB.ApprovedVersion | None:
    """Devuelve la versión vigente validada, o muestra el estado correspondiente y devuelve `None`."""
    result = current_result()
    if result.ok:
        return result.version
    if result.status == PUB.STATUS_NONE:
        empty(
            "Aún no hay resultados aprobados para mostrar. El tablero solo presenta versiones "
            "publicadas por el pipeline que superaron la validación; cuando exista una, aparecerá aquí."
        )
    else:
        st.error(result.message)
        for reason in result.reasons:
            st.markdown(f"- {reason}")
        st.caption(
            "Un responsable del pipeline puede revisar las versiones con `python -m pipeline.restore --list` "
            "y volver a señalar una versión válida."
        )
    return None


# ---------------------------------------------------------------------------
# Componentes visuales reutilizables
# ---------------------------------------------------------------------------
def hero(title: str, subtitle: str):
    st.markdown(
        f"""<div class="app-hero"><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>""",
        unsafe_allow_html=True,
    )


def note(text: str):
    st.markdown(f'<div class="note">{html.escape(text)}</div>', unsafe_allow_html=True)


def empty(text: str):
    st.markdown(f'<div class="empty">⚠️ {html.escape(text)}</div>', unsafe_allow_html=True)


def plotly(fig, **kwargs):
    """Muestra una figura Plotly con estilo y configuración coherentes."""
    config = {"displayModeBar": False, "displaylogo": False}
    return st.plotly_chart(fig, use_container_width=True, config=config, **kwargs)


def source_banner(version: PUB.ApprovedVersion):
    """Identifica el tipo de fuente en la parte superior de cada página."""
    if version.is_synthetic:
        st.markdown(
            '<div class="synthetic"><strong>Datos sintéticos.</strong> Esta versión se generó con datos de '
            "prueba: las cifras, estimaciones y métricas no son resultados institucionales y no deben "
            "presentarse como tales.</div>",
            unsafe_allow_html=True,
        )
    else:
        note("Fuente institucional: los resultados provienen de la fuente institucional declarada en la ejecución.")


def sidebar_status():
    """Resumen de la versión mostrada, sin rutas ni identificadores internos de MLflow."""
    result = current_result()
    if not result.ok:
        st.caption("Sin versión aprobada disponible.")
        return
    version = result.version
    kind = "sintética" if version.is_synthetic else "institucional"
    st.caption(f"Fuente {kind} · publicada {PUB.format_stamp(version.published_at)}")
