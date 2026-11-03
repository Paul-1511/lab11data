"""Página de inicio: qué se mide, qué versión se publicó y resumen de las estimaciones."""

from __future__ import annotations

import streamlit as st

from .. import published as PUB
from .. import theme as T
from .. import ui


def render():
    ui.hero(
        "Observatorio de Turismo Sostenible · Guatemala",
        "Estimaciones mensuales de visitantes no residentes. Solo se muestran resultados "
        "publicados que superaron la validación del pipeline.",
    )

    version = ui.require_version()
    if version is None:
        return
    ui.source_banner(version)

    s = version.summary()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Último período observado", T.month_label(s["ultimo_observado"]))
    c2.metric(
        "Último valor observado", T.fmt_compact(s["ultimo_valor"]),
        help=f"{T.fmt_int(s['ultimo_valor'])} {version.unit}",
    )
    c3.metric("Fecha de publicación", PUB.format_stamp(version.published_at).replace(" UTC", ""), help="Hora UTC")
    c4.metric("Fuente", "Sintética" if version.is_synthetic else "Institucional")

    st.markdown(
        f"- **Destino:** {version.destination}\n"
        f"- **Variable:** {version.variable}\n"
        f"- **Unidad:** {version.unit}\n"
        f"- **Versión publicada:** `{version.version_id}`\n"
        f"- **Datos observados:** {T.month_label(s['primer_observado'])} a {T.month_label(s['ultimo_observado'])} "
        f"({s['meses_observados']} meses)"
    )

    st.subheader("Estimaciones disponibles")
    st.markdown(
        f"Se publicaron **{s['meses_estimados']} estimaciones mensuales**, de "
        f"{T.month_label(s['primer_estimado'])} a {T.month_label(s['ultimo_estimado'])}."
    )
    estimated = version.estimated
    st.table({
        "Mes": [T.month_label(p) for p in estimated["periodo"]],
        f"Estimación ({version.unit})": [T.fmt_int(v) for v in estimated["valor"]],
    })

    cols = st.columns(3)
    with cols[0]:
        st.markdown("#### ¿Qué puede hacer aquí?")
        st.markdown(
            "- **Estimaciones**: ver la serie observada y el horizonte estimado, y explorar períodos.\n"
            "- **Evaluación**: conocer el error del procedimiento frente a una referencia sencilla y la "
            "decisión de aceptación."
        )
    with cols[1]:
        st.markdown("#### Para quién")
        st.markdown(
            "Directivos y tomadores de decisiones del sector turístico. Cada página explica qué muestra y "
            "qué límites tiene."
        )
    with cols[2]:
        st.markdown("#### Cómo leer los colores")
        st.markdown(
            f"- <span style='color:{T.COLOR_OBSERVADO};font-weight:600'>Gris oscuro</span>: dato observado.\n"
            f"- <span style='color:{T.COLOR_ESTIMADO};font-weight:600'>Turquesa</span>: estimación publicada.\n"
            f"- <span style='color:{T.WARN};font-weight:600'>Ámbar</span>: aviso de fuente sintética.",
            unsafe_allow_html=True,
        )
