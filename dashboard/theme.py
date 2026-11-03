"""Paleta de colores, tipografía y plantilla de gráficos del tablero.

Adaptado del tablero del Laboratorio 10 (misma marca turquesa, neutros, tipografía,
formatos numéricos y CSS de tarjetas). Se omitieron las paletas de vías, regiones,
modelos y clústeres porque pertenecen a visualizaciones que este tablero no tiene.

Cada concepto conserva un color fijo en todas las páginas:

- gris oscuro: dato observado;
- turquesa de marca: dato estimado;
- ámbar: aviso, usado para identificar resultados de fuente sintética.
"""

from __future__ import annotations

# --- Marca y neutros -------------------------------------------------------
BRAND = "#0F766E"          # turquesa profundo (color principal)
BRAND_SOFT = "#14B8A6"     # turquesa claro (acentos / hover)
INK = "#1F2A33"            # texto principal
MUTED = "#5B6B76"          # texto secundario
GRID = "#E3E9ED"           # líneas de rejilla
SURFACE = "#FFFFFF"        # fondo de tarjetas
CANVAS = "#F6F8F9"         # fondo de la app

OK = "#2E7D5B"             # positivo / buen desempeño
WARN = "#C2672B"           # atención
BAD = "#B23A48"            # alto error / negativo

# --- Significado fijo de las series ----------------------------------------
COLOR_OBSERVADO = "#2F3B44"   # gris oscuro, serie real
COLOR_ESTIMADO = BRAND        # turquesa, estimación publicada
COLOR_REFERENCIA = "#8A99A5"  # gris azulado, referencia de comparación

FONT_FAMILY = (
    "Inter, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
)

_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def base_layout(height: int = 360, legend: bool = True) -> dict:
    """Diccionario de layout común para todas las figuras Plotly."""
    return dict(
        template="plotly_white",
        height=height,
        font=dict(family=FONT_FAMILY, size=13, color=INK),
        title=dict(font=dict(size=16, color=INK)),
        margin=dict(l=55, r=20, t=50, b=45),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=12),
        )
        if legend
        else dict(visible=False),
        hoverlabel=dict(font=dict(family=FONT_FAMILY, size=12)),
    )


def fmt_int(value: float) -> str:
    """Formatea un entero con separador de miles (estilo es-GT: punto)."""
    try:
        return f"{int(round(value)):,}".replace(",", ".")
    except (ValueError, TypeError, OverflowError):
        return "—"


def fmt_compact(value: float) -> str:
    """Formato compacto para tarjetas: 3,33 M / 845,2 k."""
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "—"
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f} M".replace(".", ",")
    if abs(v) >= 1_000:
        return f"{v/1_000:.1f} k".replace(".", ",")
    return f"{int(round(v))}"


def fmt_pct(value: float, decimals: int = 1) -> str:
    try:
        return f"{value:.{decimals}f}%".replace(".", ",")
    except (ValueError, TypeError):
        return "—"


def fmt_ratio(value: float, decimals: int = 2) -> str:
    try:
        return f"{value:.{decimals}f}".replace(".", ",")
    except (ValueError, TypeError):
        return "—"


def month_label(period) -> str:
    """Mes abreviado en español ('mar 2026'); no depende de la configuración regional del sistema."""
    return f"{_MESES[period.month - 1]} {period.year}"


# CSS global de la aplicación (tipografía, tarjetas, encabezados).
APP_CSS = f"""
<style>
    html, body, [class*="css"] {{ font-family: {FONT_FAMILY}; }}
    .main .block-container {{ padding-top: 1.4rem; max-width: 1200px; }}
    h1, h2, h3 {{ color: {INK}; font-weight: 700; letter-spacing: -0.01em; }}
    .app-hero {{
        background: linear-gradient(100deg, {BRAND} 0%, {BRAND_SOFT} 100%);
        color: white; padding: 1.1rem 1.4rem; border-radius: 14px;
        margin-bottom: 1.0rem;
    }}
    .app-hero h1 {{ color: white; margin: 0; font-size: 1.5rem; }}
    .app-hero p {{ color: rgba(255,255,255,0.92); margin: 0.25rem 0 0 0; font-size: 0.95rem; }}
    .note {{
        background: #EAF4F3; border-left: 4px solid {BRAND};
        padding: 0.7rem 0.95rem; border-radius: 8px; color: {INK};
        font-size: 0.9rem; margin: 0.4rem 0 0.9rem 0;
    }}
    .empty {{
        background: #FFF4E6; border-left: 4px solid {WARN};
        padding: 0.7rem 0.95rem; border-radius: 8px; color: {INK};
        font-size: 0.9rem;
    }}
    .synthetic {{
        background: #FFF4E6; border: 1px solid {WARN}; border-left: 6px solid {WARN};
        padding: 0.7rem 0.95rem; border-radius: 8px; color: {INK};
        font-size: 0.92rem; margin: 0.2rem 0 0.9rem 0;
    }}
    div[data-testid="stMetric"] {{
        background: {SURFACE}; border: 1px solid {GRID};
        border-radius: 12px; padding: 0.7rem 0.9rem;
    }}
    div[data-testid="stMetricLabel"] p {{ color: {MUTED}; font-size: 0.8rem; }}
    div[data-testid="stMetricValue"] {{ font-size: 1.5rem; }}
</style>
"""
