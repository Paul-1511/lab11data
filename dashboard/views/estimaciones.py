"""Página de estimaciones: serie observada y horizonte estimado de la versión vigente."""

from __future__ import annotations

import streamlit as st

from .. import charts
from .. import published as PUB
from .. import theme as T
from .. import ui

DEFAULT_MONTHS = 36


def render():
    ui.hero(
        "Estimaciones",
        "Serie mensual observada y estimaciones publicadas. El tablero no calcula estimaciones: "
        "solo muestra las de la versión vigente.",
    )

    version = ui.require_version()
    if version is None:
        return
    ui.source_banner(version)

    periods = list(version.estimates["periodo"].drop_duplicates())
    labels = [T.month_label(p) for p in periods]
    by_label = dict(zip(labels, periods))
    last_observed = version.summary()["ultimo_observado"]
    last_index = periods.index(last_observed)
    first_label = labels[max(0, last_index - DEFAULT_MONTHS + 1)]

    start_label, end_label = st.select_slider(
        "Período a explorar", options=labels, value=(first_label, labels[-1]), key="estimaciones_periodo",
        help="Solo acota lo que se dibuja; no recalcula nada.",
    )
    fig = charts.estimates_figure(version, by_label[start_label], by_label[end_label])
    ui.plotly(fig)

    s = version.summary()
    ui.note(
        f"Gris: dato observado hasta {T.month_label(s['ultimo_observado'])}. Turquesa discontinuo: estimación, "
        f"con la zona sombreada como horizonte estimado ({T.month_label(s['primer_estimado'])} a "
        f"{T.month_label(s['ultimo_estimado'])}). Unidad: {version.unit}."
    )

    st.subheader("Valores estimados en el período seleccionado")
    chosen = version.estimated[
        (version.estimated["periodo"] >= by_label[start_label]) & (version.estimated["periodo"] <= by_label[end_label])
    ]
    if chosen.empty:
        st.markdown("El período seleccionado no incluye meses estimados.")
    else:
        st.table({
            "Mes": [T.month_label(p) for p in chosen["periodo"]],
            f"Estimación ({version.unit})": [T.fmt_int(v) for v in chosen["valor"]],
        })

    method = version.metadata["procedimiento"]
    st.caption(
        f"Versión {version.version_id} · publicada {PUB.format_stamp(version.published_at)} · procedimiento "
        f"{method['metodo']} (mismo mes {method['periodo_estacional']} meses antes)."
    )
