"""Seis controles automáticos de calidad (Laboratorio 11, ejercicio 3).

Cada control devuelve un `CheckResult` con estado:

- PASS         : sin hallazgos.
- WARN         : hallazgo que se reporta pero NO detiene el flujo.
- FAIL         : hallazgo crítico; el pipeline se detiene antes de publicar.
- NO EVALUADO  : el control no pudo correr porque otro control crítico falló antes.

Los umbrales viven en `config/config.yaml` (sección `quality`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .dates import month_range, parse_month

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "NO EVALUADO"

# Qué verifica cada control y cuándo advierte / detiene (ejercicio 3.2).
SPECS = {
    "a_columnas_tipos": dict(
        name="a) Columnas y tipos",
        verifies="Que existan las columnas indispensables y que sus valores tengan el tipo esperado.",
        warn_when="Falta una columna opcional (dimensión de desglose).",
        stop_when="Falta una columna requerida o hay valores no convertibles al tipo esperado.",
    ),
    "b_fechas_cobertura": dict(
        name="b) Fechas y cobertura",
        verifies="Que las fechas sean válidas y que cada mes de los períodos requeridos tenga información.",
        warn_when="Hay fechas inválidas bajo el umbral, meses sin datos fuera de los períodos requeridos o fechas fuera del rango esperado.",
        stop_when="Fechas inválidas sobre el umbral, o meses sin datos dentro de los períodos de ajuste/evaluación.",
    ),
    "c_unicidad": dict(
        name="c) Unicidad",
        verifies="Que no existan filas duplicadas según la clave y granularidad definidas.",
        warn_when="Hay duplicados dentro de la fracción tolerada (si se configura mayor que 0).",
        stop_when="La fracción de filas duplicadas supera el umbral (por defecto, cualquier duplicado).",
    ),
    "d_faltantes": dict(
        name="d) Valores faltantes",
        verifies="Que los faltantes en columnas permitidas (dimensiones) se distingan de los que impiden estimar (valor).",
        warn_when="Hay faltantes en columnas permitidas bajo el umbral; se etiquetan como «Sin dato».",
        stop_when="Hay faltantes en la variable a estimar, o en columnas permitidas por encima del umbral.",
    ),
    "e_valores_denominadores": dict(
        name="e) Valores y denominadores",
        verifies="Que no haya conteos negativos, valores no finitos ni totales mensuales iguales a cero (denominador del reparto).",
        warn_when="Total mensual igual a cero, si la política se configura como «warn».",
        stop_when="Conteos negativos, valores infinitos o total mensual igual a cero (política «fail»).",
    ),
    "f_regla_metodologica": dict(
        name="f) Regla metodológica",
        verifies="Que la serie mensual provenga de la fuente mensual (no de sumar presencias diarias), cuadre con ella y no rellene con ceros los meses sin cobertura.",
        warn_when="No aplica: la regla es de cumplimiento estricto.",
        stop_when="La fuente se declara o se detecta diaria, la serie no cuadra con la suma de la fuente, o un mes sin cobertura quedó con valor.",
    ),
}


@dataclass
class CheckResult:
    id: str
    name: str
    verifies: str
    warn_when: str
    stop_when: str
    status: str
    message: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _result(check_id: str, status: str, message: str, details: dict | None = None) -> CheckResult:
    return CheckResult(id=check_id, status=status, message=message, details=details or {}, **SPECS[check_id])


def skipped(check_id: str, reason: str) -> CheckResult:
    return _result(check_id, SKIP, reason)


def _months(values) -> list[str]:
    return [pd.Timestamp(v).strftime("%Y-%m") for v in values]


def _required_months(cfg: dict) -> set[pd.Timestamp]:
    out: set[pd.Timestamp] = set()
    for name in cfg["quality"]["coverage"]["required_ranges"]:
        p = cfg["periods"][name]
        out |= set(month_range(p["start"], p["end"]))
    return out


# ---------------------------------------------------------------------------
# a) Columnas y tipos
# ---------------------------------------------------------------------------
def _unparseable(series: pd.Series, kind: str) -> int:
    """Valores no nulos que no se pueden convertir al tipo esperado."""
    nonnull = series.dropna()
    if kind == "numeric":
        return int(pd.to_numeric(nonnull, errors="coerce").isna().sum())
    if kind == "month":
        return int(parse_month(nonnull).isna().sum())
    if kind == "date":
        return int(pd.to_datetime(nonnull, errors="coerce").isna().sum())
    return 0  # text: cualquier valor es válido


def check_columns_types(df: pd.DataFrame, cfg: dict) -> CheckResult:
    cid = "a_columnas_tipos"
    cols = cfg["columns"]
    tol = cfg["quality"]["columns"]["max_unparseable_frac"]
    required = dict(cols["required"])
    optional = dict(cols.get("optional", {}))
    # La variable a estimar, las columnas de fecha y las de los filtros siempre son indispensables.
    required.setdefault(cols["value"], "numeric")
    for flt in cfg.get("filters") or []:
        required.setdefault(flt["column"], "text")
    if cols["date"]["mode"] == "column":
        required.setdefault(cols["date"]["column"], "date")
    else:
        required.setdefault(cols["date"]["year"], "numeric")
        required.setdefault(cols["date"]["month"], "month")

    missing_req = [c for c in required if c not in df.columns]
    missing_opt = [c for c in optional if c not in df.columns]
    bad_types = {}
    for col, kind in {**required, **optional}.items():
        if col in df.columns and len(df):
            n_bad = _unparseable(df[col], kind)
            if n_bad / len(df) > tol:
                bad_types[col] = {"esperado": kind, "valores_no_convertibles": n_bad}

    details = {
        "columnas_requeridas_faltantes": missing_req,
        "columnas_opcionales_faltantes": missing_opt,
        "tipos_incompatibles": bad_types,
        "columnas_en_fuente": list(map(str, df.columns)),
    }
    if missing_req or bad_types:
        parts = []
        if missing_req:
            parts.append(f"faltan columnas requeridas: {missing_req}")
        if bad_types:
            parts.append(f"tipos incompatibles en: {list(bad_types)}")
        return _result(cid, FAIL, "; ".join(parts), details)
    if missing_opt:
        return _result(cid, WARN, f"faltan columnas opcionales: {missing_opt}", details)
    return _result(cid, PASS, "columnas y tipos correctos", details)


# ---------------------------------------------------------------------------
# b) Fechas y cobertura
# ---------------------------------------------------------------------------
def check_dates_coverage(df: pd.DataFrame, cfg: dict, dates: pd.DataFrame) -> CheckResult:
    cid = "b_fechas_cobertura"
    qcfg = cfg["quality"]
    n = len(df)
    invalid = int(dates["fecha"].isna().sum())
    invalid_frac = invalid / n if n else 0.0

    expected = month_range(cfg["periods"]["expected_start"], cfg["periods"]["expected_end"])
    present = set(dates["fecha"].dropna().unique())
    allowed = {pd.Timestamp(m) for m in qcfg["coverage"].get("allowed_missing_months", [])}
    required = _required_months(cfg)

    missing = [m for m in expected if m not in present and m not in allowed]
    missing_required = [m for m in missing if m in required]
    missing_other = [m for m in missing if m not in required]
    out_of_range = sorted(m for m in present if m not in set(expected))

    details = {
        "filas": n,
        "fechas_invalidas": invalid,
        "fraccion_invalida": round(invalid_frac, 6),
        "meses_esperados": len(expected),
        "meses_sin_datos_requeridos": _months(missing_required),
        "meses_sin_datos_no_requeridos": _months(missing_other),
        "meses_fuera_del_rango_esperado": _months(out_of_range),
        "huecos_permitidos_configurados": _months(sorted(allowed)),
    }
    if invalid_frac > qcfg["dates"]["max_invalid_frac"] or missing_required:
        parts = []
        if invalid_frac > qcfg["dates"]["max_invalid_frac"]:
            parts.append(f"{invalid} fechas inválidas ({invalid_frac:.2%})")
        if missing_required:
            parts.append(f"{len(missing_required)} meses requeridos sin datos (p. ej. {_months(missing_required[:3])})")
        return _result(cid, FAIL, "; ".join(parts), details)
    if invalid or missing_other or out_of_range:
        parts = []
        if invalid:
            parts.append(f"{invalid} fechas inválidas bajo el umbral")
        if missing_other:
            parts.append(f"{len(missing_other)} meses sin datos fuera de los períodos requeridos")
        if out_of_range:
            parts.append(f"{len(out_of_range)} meses fuera del rango esperado")
        return _result(cid, WARN, "; ".join(parts), details)
    return _result(cid, PASS, "fechas válidas y cobertura completa", details)


# ---------------------------------------------------------------------------
# c) Unicidad
# ---------------------------------------------------------------------------
def check_uniqueness(df: pd.DataFrame, cfg: dict, dates: pd.DataFrame) -> CheckResult:
    cid = "c_unicidad"
    key_cols = cfg["quality"]["uniqueness"]["key"]
    max_frac = cfg["quality"]["uniqueness"]["max_duplicate_frac"]
    absent = [c for c in key_cols if c != "__fecha__" and c not in df.columns]
    if absent:
        return _result(cid, FAIL, f"la clave usa columnas inexistentes: {absent}", {"clave": key_cols})

    key = pd.DataFrame(index=df.index)
    for c in key_cols:
        key[c] = dates["fecha"] if c == "__fecha__" else df[c]
    key = key[dates["fecha"].notna()]  # filas con fecha inválida ya las reporta el control b

    dup_mask = key.duplicated(keep=False)
    n_rows = int(dup_mask.sum())
    frac = n_rows / len(key) if len(key) else 0.0
    examples = (
        key[dup_mask].drop_duplicates().head(5).astype(str).to_dict("records") if n_rows else []
    )
    details = {
        "clave": key_cols,
        "filas_en_claves_duplicadas": n_rows,
        "claves_duplicadas": int(key[dup_mask].drop_duplicates().shape[0]) if n_rows else 0,
        "fraccion": round(frac, 6),
        "ejemplos": examples,
    }
    if n_rows == 0:
        return _result(cid, PASS, "sin duplicados para la clave definida", details)
    msg = f"{n_rows} filas comparten clave ({frac:.2%})"
    return _result(cid, FAIL if frac > max_frac else WARN, msg, details)


# ---------------------------------------------------------------------------
# d) Valores faltantes
# ---------------------------------------------------------------------------
def _missing_mask(series: pd.Series, sentinels: list[str]) -> pd.Series:
    as_text = series.astype("string").str.strip()
    return series.isna() | as_text.isin(sentinels).fillna(False)


def check_missing(df: pd.DataFrame, cfg: dict) -> CheckResult:
    cid = "d_faltantes"
    mcfg = cfg["quality"]["missing"]
    sentinels = [str(s) for s in cfg["columns"].get("missing_sentinels", [])]
    n = len(df)

    blocking, allowed = {}, {}
    for col in mcfg["blocking"]:
        if col in df.columns:
            k = int(df[col].isna().sum())  # un 0 es un dato válido en la variable a estimar
            blocking[col] = {"faltantes": k, "fraccion": round(k / n, 6) if n else 0.0}
    for col in mcfg["allowed"]:
        if col in df.columns:
            k = int(_missing_mask(df[col], sentinels).sum())
            allowed[col] = {"faltantes": k, "fraccion": round(k / n, 6) if n else 0.0}

    details = {"que_impiden_estimar": blocking, "permitidos": allowed}
    bad_block = [c for c, v in blocking.items() if v["fraccion"] > mcfg["max_blocking_frac"]]
    bad_allowed = [c for c, v in allowed.items() if v["fraccion"] > mcfg["max_allowed_frac"]]
    if bad_block or bad_allowed:
        parts = []
        if bad_block:
            parts.append(f"faltantes que impiden estimar en {bad_block}")
        if bad_allowed:
            parts.append(f"faltantes permitidos sobre el umbral en {bad_allowed}")
        return _result(cid, FAIL, "; ".join(parts), details)
    if any(v["faltantes"] for v in allowed.values()) or any(v["faltantes"] for v in blocking.values()):
        return _result(cid, WARN, "hay faltantes permitidos bajo el umbral (se etiquetan «Sin dato»)", details)
    return _result(cid, PASS, "sin faltantes relevantes", details)


# ---------------------------------------------------------------------------
# e) Valores y denominadores
# ---------------------------------------------------------------------------
def check_values_denominators(df: pd.DataFrame, cfg: dict, dates: pd.DataFrame) -> CheckResult:
    cid = "e_valores_denominadores"
    value_col = cfg["columns"]["value"]
    policy = cfg["quality"]["values"]["zero_total"]
    v = pd.to_numeric(df[value_col], errors="coerce")

    negative = int((v < 0).sum())
    nonfinite = int(np.isinf(v).sum())
    finite = v.where(np.isfinite(v))
    totals = finite.groupby(dates["fecha"]).sum(min_count=1)  # denominador del reparto: total mensual
    zero_months = totals[totals == 0].index

    details = {
        "conteos_negativos": negative,
        "valores_no_finitos": nonfinite,
        "meses_con_total_cero": _months(zero_months),
        "politica_total_cero": policy,
    }
    problems = []
    if negative:
        problems.append(f"{negative} conteos negativos")
    if nonfinite:
        problems.append(f"{nonfinite} valores no finitos")
    if len(zero_months):
        problems.append(f"{len(zero_months)} meses con total igual a cero")

    hard = negative or nonfinite or (len(zero_months) and policy == "fail")
    if hard:
        return _result(cid, FAIL, "; ".join(problems), details)
    if len(zero_months):
        return _result(cid, WARN, "; ".join(problems), details)
    return _result(cid, PASS, "valores finitos, no negativos y denominadores distintos de cero", details)


# ---------------------------------------------------------------------------
# f) Regla metodológica
# ---------------------------------------------------------------------------
def check_methodology(
    df: pd.DataFrame, cfg: dict, dates: pd.DataFrame, prepared: dict | None
) -> CheckResult:
    """Regla del proyecto: la serie mensual sale de la fuente mensual.

    1. La fuente debe estar declarada con la misma granularidad que la variable (mensual).
    2. Si la fuente trae fecha completa, ninguna fecha puede tener día distinto de 1
       (señal de presencias diarias que se estarían sumando).
    3. La serie preparada debe cuadrar con la suma de la fuente mensual.
    4. Un mes sin cobertura debe quedar como NaN, nunca como 0.
    """
    cid = "f_regla_metodologica"
    problems: list[str] = []
    details: dict = {}

    src, tgt = cfg["source"]["granularity"], cfg["target"]["granularity"]
    details["granularidad_fuente"], details["granularidad_variable"] = src, tgt
    if src != tgt:
        problems.append(f"la fuente es «{src}» pero la variable es «{tgt}»")

    days = pd.to_numeric(dates["dia"], errors="coerce").dropna()
    if len(days) and (days != 1).any():
        k = int((days != 1).sum())
        details["filas_con_dia_distinto_de_1"] = k
        problems.append(
            f"{k} filas con día distinto de 1: parece una fuente diaria; no se deben sumar presencias diarias"
        )

    if prepared is None:
        details["serie_preparada"] = "no evaluada (la preparación no se ejecutó)"
    else:
        total = prepared["total"]
        bad = total[(~total["cobertura"]) & total["valor"].notna()]
        details["meses_sin_cobertura_con_valor"] = _months(bad["fecha"])
        if len(bad):
            problems.append(f"{len(bad)} meses sin cobertura tienen valor (posible relleno con 0)")

        v = pd.to_numeric(df[cfg["columns"]["value"]], errors="coerce")
        in_range = dates["fecha"].isin(total["fecha"]) & np.isfinite(v)
        source_sum, series_sum = float(v[in_range].sum()), float(total["valor"].sum())
        details["suma_fuente"], details["suma_serie"] = source_sum, series_sum
        tol = cfg["quality"]["methodology"]["reconciliation_rel_tol"]
        if not np.isclose(source_sum, series_sum, rtol=tol, atol=0.0):
            problems.append(f"la serie mensual ({series_sum:,.0f}) no cuadra con la fuente ({source_sum:,.0f})")

    if problems:
        return _result(cid, FAIL, "; ".join(problems), details)
    return _result(cid, PASS, "la serie mensual proviene de la fuente mensual y no se rellenó con ceros", details)
