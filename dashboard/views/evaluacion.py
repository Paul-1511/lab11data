"""Página de evaluación: métricas publicadas, decisión de aceptación y limitaciones."""

from __future__ import annotations

import streamlit as st

from .. import charts
from .. import theme as T
from .. import ui

_BASELINES = {"last_observed": "último valor observado antes del mes evaluado"}
_CHECKS = {
    "calidad_sin_fallos_criticos": "Controles de calidad sin fallos críticos",
    "salidas_completas": "Estimaciones completas y conformes al contrato de resultados",
    "meses_evaluados_suficientes": "Meses evaluados suficientes",
    "mae_dentro_del_umbral": "Error del procedimiento dentro del umbral frente a la referencia",
}
_STATES = {"PASS": "Aprobado", "WARN": "Con advertencia", "FAIL": "Fallido", "SKIP": "No evaluado"}


def _value(number) -> str:
    return "No disponible" if number is None else T.fmt_int(number)


def render():
    ui.hero(
        "Evaluación",
        "Qué tan bien habría funcionado el procedimiento en meses ya observados y por qué la versión fue aprobada.",
    )

    version = ui.require_version()
    if version is None:
        return
    ui.source_banner(version)

    meta = version.metadata
    m = meta["metricas"]
    unit = version.unit
    if version.is_synthetic:
        st.warning(
            "Las métricas de esta página se calcularon con datos sintéticos. Sirven para comprobar que el "
            "flujo funciona; no son una evaluación institucional del procedimiento."
        )

    st.subheader("Decisión de aceptación")
    rule = meta["aceptacion"]["regla"]
    st.success(
        f"Aceptada. Se exige al menos {rule['min_evaluated_months']} meses evaluados y que el MAE del "
        f"procedimiento no supere {T.fmt_pct(rule['max_mae_ratio_to_baseline'] * 100, 0)} del MAE de la referencia."
    )
    for key, ok in meta["aceptacion"]["verificaciones"].items():
        st.markdown(f"- {'✅' if ok else '❌'} {_CHECKS.get(key, key)}")

    st.subheader("Errores del procedimiento y de la referencia")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MAE del procedimiento", _value(m["mae_estimacion"]), help=f"Unidad: {unit}")
    c2.metric("MAE de la referencia", _value(m["mae_referencia"]), help=f"Unidad: {unit}")
    c3.metric("RMSE del procedimiento", _value(m["rmse_estimacion"]), help=f"Unidad: {unit}")
    c4.metric("RMSE de la referencia", _value(m["rmse_referencia"]), help=f"Unidad: {unit}")
    st.caption(f"Errores en {unit}. La referencia (baseline) usa el {_BASELINES.get(meta['procedimiento']['baseline'], meta['procedimiento']['baseline'])}.")

    fig = charts.metrics_figure(version)
    if fig is not None:
        ui.plotly(fig)
    if m["razon_mae"] is not None:
        st.markdown(f"Razón MAE procedimiento / MAE referencia: **{T.fmt_ratio(m['razon_mae'])}**.")

    st.subheader("Período de evaluación")
    e = meta["periodos"]["evaluacion"]
    st.markdown(
        f"- **Período:** {e['inicio']} a {e['fin']}\n"
        f"- **Meses evaluados:** {m['meses_evaluados']} de {m['meses_solicitados']} solicitados"
    )
    if m["meses_no_evaluados"]:
        st.markdown(f"- **Meses sin evaluar:** {', '.join(str(x) for x in m['meses_no_evaluados'])}")

    with st.expander("¿Qué significan estas métricas?", expanded=True):
        st.markdown(
            "- **MAE** (error absoluto medio): en promedio, cuántos visitantes por mes se equivoca la estimación. "
            "Menor es mejor.\n"
            "- **RMSE** (raíz del error cuadrático medio): similar al MAE, pero penaliza más los errores "
            "grandes. Menor es mejor.\n"
            "- **Referencia (baseline):** un procedimiento muy sencillo que repite el último valor observado. "
            "El procedimiento publicado debe superarlo o, al menos, no ser mucho peor.\n"
            "- Cada mes evaluado se estima ocultando ese mes y los posteriores, de modo que no se usan datos futuros."
        )

    st.subheader("Limitaciones metodológicas")
    for item in meta["limitaciones"]:
        st.markdown(f"- {item['texto']}")
    if meta["advertencias"]:
        with st.expander("Advertencias de la ejecución"):
            for text in meta["advertencias"]:
                st.markdown(f"- {text}")

    with st.expander("Controles de calidad de la ejecución"):
        controls = meta["calidad"]["controles"]
        st.table({
            "Control": [c.get("nombre", c.get("id", "")) for c in controls],
            "Estado": [_STATES.get(c.get("estado"), str(c.get("estado"))) for c in controls],
        })
