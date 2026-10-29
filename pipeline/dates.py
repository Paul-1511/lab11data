"""Construcción de fechas mensuales a partir de las columnas de la fuente."""

from __future__ import annotations

import pandas as pd

SPANISH_MONTHS = {
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
    "julio": 7, "jul": 7, "agosto": 8, "ago": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "set": 9,
    "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12,
}


def parse_month(series: pd.Series) -> pd.Series:
    """Mes como número 1-12 (acepta números o nombres en español); NaN si no se puede."""
    num = pd.to_numeric(series, errors="coerce")
    names = series.astype("string").str.strip().str.lower().map(SPANISH_MONTHS)
    out = num.where(num.notna(), pd.to_numeric(names, errors="coerce"))
    return out.where(out.between(1, 12))


def month_range(start: str, end: str) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="MS")


def build_dates(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Devuelve un DataFrame con `fecha` (inicio de mes, NaT si es inválida) y `dia`.

    `dia` solo existe cuando la fuente trae una fecha completa (mode: column);
    se usa para detectar si la fuente es diaria (control f).
    """
    dcfg = cfg["columns"]["date"]
    if dcfg["mode"] == "column":
        raw = pd.to_datetime(df[dcfg["column"]], errors="coerce")
        return pd.DataFrame(
            {"fecha": raw.dt.to_period("M").dt.to_timestamp(), "dia": raw.dt.day},
            index=df.index,
        )
    year = pd.to_numeric(df[dcfg["year"]], errors="coerce")
    year = year.where(year.between(1900, 2100) & (year % 1 == 0))
    month = parse_month(df[dcfg["month"]])
    text = (
        year.astype("Int64").astype("string")
        + "-"
        + month.astype("Int64").astype("string").str.zfill(2)
        + "-01"
    )
    fecha = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    return pd.DataFrame({"fecha": fecha, "dia": pd.NA}, index=df.index)
