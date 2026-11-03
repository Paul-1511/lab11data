"""Figuras Plotly del tablero. Solo grafican lo publicado; no calculan estimaciones ni métricas."""

from __future__ import annotations

import html

import pandas as pd
import plotly.graph_objects as go

from . import theme as T
from .published import ApprovedVersion


def _hover(frame: pd.DataFrame, unit: str) -> list[str]:
    unit = html.escape(unit)
    return [f"{T.month_label(p)}<br>{T.fmt_int(v)} {unit}" for p, v in zip(frame["periodo"], frame["valor"])]


def estimates_figure(version: ApprovedVersion, start: pd.Timestamp, end: pd.Timestamp) -> go.Figure:
    """Serie observada y estimada entre `start` y `end`, con el horizonte estimado sombreado."""
    frame = version.estimates
    window = frame[(frame["periodo"] >= start) & (frame["periodo"] <= end)]
    observed = window[window["tipo"] == "observado"]
    estimated = window[window["tipo"] == "estimado"]
    suffix = " · sintético" if version.is_synthetic else ""
    unit = version.unit

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=observed["periodo"], y=observed["valor"], mode="lines",
        name=f"Observado{suffix}", line=dict(color=T.COLOR_OBSERVADO, width=2),
        hovertext=_hover(observed, unit), hoverinfo="text",
    ))
    fig.add_trace(go.Scatter(
        x=estimated["periodo"], y=estimated["valor"], mode="lines+markers",
        name=f"Estimado{suffix}", line=dict(color=T.COLOR_ESTIMADO, width=2.5, dash="dash"),
        marker=dict(size=7, color=T.COLOR_ESTIMADO),
        hovertext=_hover(estimated, unit), hoverinfo="text",
    ))

    horizon = version.estimated["periodo"]
    visible = horizon[(horizon >= start) & (horizon <= end)]
    if len(visible):
        fig.add_vrect(
            x0=visible.min() - pd.Timedelta(days=15), x1=visible.max() + pd.Timedelta(days=15),
            fillcolor="rgba(15,118,110,0.07)", line_width=0,
            annotation_text="Horizonte estimado", annotation_position="top left", annotation_font_size=11,
        )

    layout = T.base_layout(height=420)
    layout["yaxis"] = dict(
        gridcolor=T.GRID, zerolinecolor=T.GRID, rangemode="tozero",
        title=dict(text=f"{version.variable} ({unit})", font=dict(size=12)),
    )
    fig.update_layout(**layout)
    return fig


def metrics_figure(version: ApprovedVersion) -> go.Figure | None:
    """Barras MAE y RMSE del procedimiento frente al baseline; `None` si falta alguna cifra publicada."""
    m = version.metadata["metricas"]
    values = [m["mae_estimacion"], m["mae_referencia"], m["rmse_estimacion"], m["rmse_referencia"]]
    if any(v is None for v in values):
        return None
    unit = html.escape(version.unit)
    fig = go.Figure()
    for name, color, keys in (
        ("Procedimiento", T.COLOR_ESTIMADO, ("mae_estimacion", "rmse_estimacion")),
        ("Baseline", T.COLOR_REFERENCIA, ("mae_referencia", "rmse_referencia")),
    ):
        ys = [m[k] for k in keys]
        fig.add_trace(go.Bar(
            x=["MAE", "RMSE"], y=ys, name=name, marker_color=color,
            hovertext=[f"{name} · {label}<br>{T.fmt_int(y)} {unit}" for label, y in zip(("MAE", "RMSE"), ys)],
            hoverinfo="text",
        ))
    layout = T.base_layout(height=340)
    layout["barmode"] = "group"
    layout["yaxis"] = dict(
        gridcolor=T.GRID, zerolinecolor=T.GRID, title=dict(text=f"Error ({version.unit})", font=dict(size=12)),
    )
    fig.update_layout(**layout)
    return fig
