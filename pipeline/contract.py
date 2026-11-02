"""Contrato de las estimaciones candidatas y validador reutilizable.

El contrato describe el archivo que produce `evaluation.candidate_results` y que
consumirán la publicación, la restauración, el monitoreo y el tablero. El
validador solo informa: nunca corrige, completa ni descarta filas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .dates import month_range

REQUIRED_COLUMNS = (
    "destino", "periodo", "tipo", "valor", "unidad", "run_id",
    "fecha_generacion", "version_procedimiento", "metodo",
)
TYPE_OBSERVED, TYPE_ESTIMATED = "observado", "estimado"
ALLOWED_TYPES = (TYPE_OBSERVED, TYPE_ESTIMATED)
ALLOWED_METHODS = ("seasonal_naive",)

# Columnas que deben tener un único valor en todo el archivo.
CONSTANT_COLUMNS = ("destino", "unidad", "run_id", "fecha_generacion", "version_procedimiento", "metodo")

_EXAMPLES = 10


class ContractViolation(Exception):
    """El archivo de estimaciones no cumple el contrato."""


@dataclass(frozen=True)
class ContractError:
    code: str
    message: str
    column: str | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"codigo": self.code, "mensaje": self.message, "columna": self.column, "detalle": self.detail}


@dataclass
class ContractResult:
    rows: int
    errors: list[ContractError] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def codes(self) -> list[str]:
        return [e.code for e in self.errors]

    def to_dict(self) -> dict:
        return {"valido": self.valid, "filas": self.rows, "errores": [e.to_dict() for e in self.errors]}

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ContractViolation("; ".join(f"{e.code}: {e.message}" for e in self.errors))


def read_estimates(path: str | Path) -> pd.DataFrame:
    """Lee un CSV de estimaciones sin interpretar fechas ni convertir texto a otro tipo."""
    return pd.read_csv(path, dtype={c: "string" for c in REQUIRED_COLUMNS if c != "valor"})


def validate_estimates_file(
    path: str | Path, cfg: dict, *, run_id: str | None = None, version: str | None = None
) -> ContractResult:
    return validate_estimates(read_estimates(path), cfg, run_id=run_id, version=version)


def _empty_mask(series: pd.Series) -> np.ndarray:
    text = series.astype("string").str.strip()
    return (series.isna() | text.eq("").fillna(False)).to_numpy(dtype=bool)


def _parse_periods(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    text = series.astype("string").str.strip().str.slice(0, 10)
    return pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")


def _months(values) -> list[str]:
    return sorted(pd.Timestamp(v).strftime("%Y-%m") for v in values)


def _examples(mask: np.ndarray) -> list[int]:
    return [int(i) for i in np.flatnonzero(mask)[:_EXAMPLES]]


def validate_estimates(
    candidates: pd.DataFrame, cfg: dict, *, run_id: str | None = None, version: str | None = None
) -> ContractResult:
    """Valida las estimaciones contra el contrato.

    `run_id` y `version` son opcionales: cuando se indican, el archivo debe
    coincidir con ellos; cuando no, solo se exige que cada columna constante
    tenga un único valor.
    """
    if not isinstance(candidates, pd.DataFrame):
        result = ContractResult(rows=0)
        result.errors.append(ContractError("tipo_invalido", "las estimaciones deben ser una tabla (DataFrame)"))
        return result

    df = candidates
    result = ContractResult(rows=len(df))
    errors = result.errors

    def add(code: str, message: str, column: str | None = None, **detail) -> None:
        errors.append(ContractError(code, message, column, detail))

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        add("columna_faltante", f"faltan columnas obligatorias: {missing}", columnas=missing)
        return result
    if df.empty:
        add("sin_filas", "el archivo de estimaciones no tiene filas")
        return result

    empty = {col: _empty_mask(df[col]) for col in REQUIRED_COLUMNS}
    for col, mask in empty.items():
        if mask.any():
            add("campo_vacio", f"{int(mask.sum())} valores vacíos en «{col}»", col,
                cantidad=int(mask.sum()), filas=_examples(mask))

    periods = _parse_periods(df["periodo"])
    invalid_date = periods.isna().to_numpy() & ~empty["periodo"]
    if invalid_date.any():
        add("fecha_invalida", f"{int(invalid_date.sum())} períodos no son fechas AAAA-MM-DD", "periodo",
            cantidad=int(invalid_date.sum()), filas=_examples(invalid_date))
    not_month_start = ((periods.dt.day != 1) & periods.notna()).to_numpy(dtype=bool)
    if not_month_start.any():
        add("periodo_no_mensual", "hay períodos que no corresponden al primer día de un mes", "periodo",
            cantidad=int(not_month_start.sum()), filas=_examples(not_month_start))

    valid_period = periods.notna().to_numpy()
    key = pd.DataFrame({"destino": df["destino"].astype("string").to_numpy(), "periodo": periods.to_numpy()})
    duplicated = key.duplicated(keep=False).to_numpy(dtype=bool) & valid_period
    if duplicated.any():
        add("periodo_duplicado", "hay períodos repetidos para un mismo destino", "periodo",
            cantidad=int(duplicated.sum()), periodos=_months(periods[duplicated].unique()))

    values = pd.to_numeric(df["valor"], errors="coerce")
    non_numeric = values.isna().to_numpy() & ~empty["valor"]
    if non_numeric.any():
        add("valor_no_numerico", f"{int(non_numeric.sum())} valores no son numéricos", "valor",
            cantidad=int(non_numeric.sum()), filas=_examples(non_numeric))
    numeric = values.to_numpy(dtype=float, na_value=np.nan)
    non_finite = np.isinf(numeric)
    if non_finite.any():
        add("valor_no_finito", f"{int(non_finite.sum())} valores no son finitos", "valor",
            cantidad=int(non_finite.sum()), filas=_examples(non_finite))
    negative = np.where(np.isnan(numeric), False, numeric < 0)
    if negative.any():
        add("valor_negativo", f"{int(negative.sum())} valores son negativos", "valor",
            cantidad=int(negative.sum()), filas=_examples(negative))

    expected_constants = {
        "destino": cfg["target"]["destination"],
        "unidad": cfg["target"]["unit"],
        "run_id": run_id,
        "version_procedimiento": version,
    }
    for col in CONSTANT_COLUMNS:
        present = df.loc[~empty[col], col].astype("string").str.strip()
        found = sorted(present.unique().tolist())
        expected = expected_constants.get(col)
        if len(found) > 1:
            add(f"{_constant_code(col)}_inconsistente", f"«{col}» tiene más de un valor", col, valores=found[:_EXAMPLES])
        elif expected is not None and found and found != [expected]:
            add(f"{_constant_code(col)}_inconsistente", f"«{col}» no coincide con el valor esperado", col,
                esperado=expected, encontrado=found)

    generated = df.loc[~empty["fecha_generacion"], "fecha_generacion"].astype("string").str.strip()
    parsed = pd.to_datetime(generated, format="ISO8601", utc=True, errors="coerce")
    if parsed.isna().any():
        add("fecha_generacion_invalida", "«fecha_generacion» no es una fecha y hora ISO 8601", "fecha_generacion",
            cantidad=int(parsed.isna().sum()))

    kind = df["tipo"].astype("string").str.strip()
    bad_type = (~kind.isin(ALLOWED_TYPES)).fillna(True).to_numpy(dtype=bool) & ~empty["tipo"]
    if bad_type.any():
        add("tipo_no_permitido", f"«tipo» solo admite {list(ALLOWED_TYPES)}", "tipo",
            cantidad=int(bad_type.sum()), encontrados=sorted(kind[bad_type].dropna().unique().tolist())[:_EXAMPLES])
    method = df["metodo"].astype("string").str.strip()
    bad_method = (~method.isin(ALLOWED_METHODS)).fillna(True).to_numpy(dtype=bool) & ~empty["metodo"]
    if bad_method.any():
        add("metodo_no_permitido", f"«metodo» solo admite {list(ALLOWED_METHODS)}", "metodo",
            cantidad=int(bad_method.sum()), encontrados=sorted(method[bad_method].dropna().unique().tolist())[:_EXAMPLES])

    is_observed = (kind == TYPE_OBSERVED).fillna(False).to_numpy(dtype=bool) & valid_period
    is_estimated = (kind == TYPE_ESTIMATED).fillna(False).to_numpy(dtype=bool) & valid_period
    observed_periods, estimated_periods = periods[is_observed], periods[is_estimated]

    repeated = estimated_periods.duplicated(keep=False)
    if repeated.any():
        add("estimado_repetido", "hay meses estimados repetidos", "periodo", periodos=_months(estimated_periods[repeated].unique()))

    if len(observed_periods) and len(estimated_periods) and observed_periods.max() >= estimated_periods.min():
        add("observados_y_estimados_mezclados",
            "los períodos observados deben ser anteriores a todos los estimados", "periodo",
            ultimo_observado=_months([observed_periods.max()])[0], primer_estimado=_months([estimated_periods.min()])[0])

    horizon = cfg["periods"]["forecast"]
    expected_months = set(month_range(horizon["start"], horizon["end"]))
    estimated_set = set(estimated_periods.dropna())
    absent = expected_months - estimated_set
    if absent:
        add("horizonte_incompleto", f"faltan {len(absent)} meses del horizonte configurado", "periodo",
            faltantes=_months(absent), horizonte={"inicio": horizon["start"], "fin": horizon["end"]})
    outside = estimated_set - expected_months
    if outside:
        add("estimado_fuera_de_horizonte", f"hay {len(outside)} meses estimados fuera del horizonte configurado", "periodo",
            sobrantes=_months(outside), horizonte={"inicio": horizon["start"], "fin": horizon["end"]})

    return result


def _constant_code(column: str) -> str:
    return {"version_procedimiento": "version"}.get(column, column)
