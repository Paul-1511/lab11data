"""Estimación temporal, evaluación sin fuga de información y aceptación.

El procedimiento candidato usa una referencia estacional ingenua: para estimar
un mes toma el valor del mismo mes del año anterior. La referencia de
comparación es el último valor observado antes del mes evaluado.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .dates import month_range


def _history_before(series: pd.Series, period: pd.Timestamp) -> pd.Series:
    """Solo devuelve observaciones disponibles estrictamente antes de `period`."""
    return series.loc[(series.index < period) & series.notna()].sort_index()


def _last_observed(history: pd.Series) -> float | None:
    return None if history.empty else float(history.iloc[-1])


def _historical_mean(history: pd.Series) -> float | None:
    return None if history.empty else float(history.mean())


def _seasonal_naive(history: pd.Series, period: pd.Timestamp, seasonal_period: int) -> float | None:
    reference_period = period - pd.DateOffset(months=seasonal_period)
    value = history.get(reference_period)
    return None if value is None or pd.isna(value) else float(value)


def _baseline(history: pd.Series, method: str) -> float | None:
    if method == "last_observed":
        return _last_observed(history)
    if method == "historical_mean":
        return _historical_mean(history)
    raise ValueError(f"baseline no reconocido: {method}")


def _as_series(total: pd.DataFrame) -> pd.Series:
    return total.set_index("fecha")["valor"].astype(float).sort_index()


def evaluate_temporally(total: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Evalúa cada mes con un origen móvil, sin usar la observación objetivo ni futuras."""
    series = _as_series(total)
    p = cfg["periods"]["eval"]
    params = cfg["parameters"]
    rows = []
    for period in month_range(p["start"], p["end"]):
        observed = series.get(period)
        history = _history_before(series, period)
        estimate = _seasonal_naive(history, period, params["seasonal_period"])
        reference = _baseline(history, params["baseline"])
        if pd.isna(observed) or estimate is None or reference is None:
            rows.append({
                "periodo": period, "observado": observed, "estimacion": estimate,
                "referencia": reference, "estado": "NO EVALUADO",
                "motivo": "no hay historia u observación comparable suficiente",
            })
            continue
        rows.append({
            "periodo": period, "observado": float(observed), "estimacion": estimate,
            "referencia": reference, "estado": "EVALUADO", "motivo": "",
        })

    detail = pd.DataFrame(rows)
    evaluated = detail[detail["estado"] == "EVALUADO"].copy()
    if not evaluated.empty:
        evaluated["error_absoluto_estimacion"] = (evaluated["estimacion"] - evaluated["observado"]).abs()
        evaluated["error_absoluto_referencia"] = (evaluated["referencia"] - evaluated["observado"]).abs()
        evaluated["error_cuadrado_estimacion"] = (evaluated["estimacion"] - evaluated["observado"]) ** 2
        evaluated["error_cuadrado_referencia"] = (evaluated["referencia"] - evaluated["observado"]) ** 2
        detail = detail.merge(
            evaluated[["periodo", "error_absoluto_estimacion", "error_absoluto_referencia",
                       "error_cuadrado_estimacion", "error_cuadrado_referencia"]],
            on="periodo", how="left",
        )
    else:
        for col in ("error_absoluto_estimacion", "error_absoluto_referencia",
                    "error_cuadrado_estimacion", "error_cuadrado_referencia"):
            detail[col] = np.nan

    metrics = {
        "metodo": "seasonal_naive",
        "referencia": params["baseline"],
        "periodo_evaluacion": {"inicio": p["start"], "fin": p["end"]},
        "meses_solicitados": len(detail),
        "meses_evaluados": len(evaluated),
        "meses_no_evaluados": detail.loc[detail["estado"] != "EVALUADO", "periodo"].dt.strftime("%Y-%m").tolist(),
        "mae_estimacion": None if evaluated.empty else float(evaluated["error_absoluto_estimacion"].mean()),
        "rmse_estimacion": None if evaluated.empty else float(np.sqrt(evaluated["error_cuadrado_estimacion"].mean())),
        "mae_referencia": None if evaluated.empty else float(evaluated["error_absoluto_referencia"].mean()),
        "rmse_referencia": None if evaluated.empty else float(np.sqrt(evaluated["error_cuadrado_referencia"].mean())),
    }
    return detail, metrics


def forecast_future(total: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Proyecta el horizonte configurado con la misma regla estacional reproducible."""
    series = _as_series(total).copy()
    horizon = cfg["periods"]["forecast"]
    rows = []
    for period in month_range(horizon["start"], horizon["end"]):
        history = _history_before(series, period)
        value = _seasonal_naive(history, period, cfg["parameters"]["seasonal_period"])
        if value is None:
            raise ValueError(f"no hay observación comparable para estimar {period:%Y-%m}")
        series.loc[period] = value
        rows.append({"periodo": period, "valor": value})
    return pd.DataFrame(rows)


def candidate_results(total: pd.DataFrame, cfg: dict, run_id: str, version: str) -> pd.DataFrame:
    """Combina observaciones disponibles y proyecciones candidatas para el tablero."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    observed = total.loc[total["cobertura"] & total["valor"].notna(), ["fecha", "valor"]].copy()
    observed.columns = ["periodo", "valor"]
    observed["tipo"] = "observado"
    forecast = forecast_future(total, cfg)
    forecast["tipo"] = "estimado"
    result = pd.concat([observed, forecast], ignore_index=True)
    result.insert(0, "destino", cfg["target"]["destination"])
    result["unidad"] = cfg["target"]["unit"]
    result["run_id"] = run_id
    result["fecha_generacion"] = now
    result["version_procedimiento"] = version
    result["metodo"] = "seasonal_naive"
    return result[["destino", "periodo", "tipo", "valor", "unidad", "run_id", "fecha_generacion",
                   "version_procedimiento", "metodo"]]


def acceptance_decision(quality_status: str, metrics: dict, candidates: pd.DataFrame, cfg: dict) -> dict:
    """Regla fija de aceptación; no cambia según el resultado de una ejecución."""
    rule = cfg["acceptance"]
    required = {"destino", "periodo", "tipo", "valor", "unidad", "run_id", "fecha_generacion", "version_procedimiento"}
    outputs_complete = bool(
        required.issubset(candidates.columns) and not candidates.empty and candidates["valor"].notna().all()
    )
    enough_months = bool(metrics["meses_evaluados"] >= rule["min_evaluated_months"])
    candidate_mae, baseline_mae = metrics["mae_estimacion"], metrics["mae_referencia"]
    performance_ok = bool(
        candidate_mae is not None and baseline_mae is not None
        and candidate_mae <= baseline_mae * rule["max_mae_ratio_to_baseline"]
    )
    checks = {
        "calidad_sin_fallos_criticos": quality_status != "FAIL",
        "salidas_completas": outputs_complete,
        "meses_evaluados_suficientes": enough_months,
        "mae_dentro_del_umbral": performance_ok,
    }
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "aceptada": not failed,
        "regla": {
            "min_evaluated_months": rule["min_evaluated_months"],
            "max_mae_ratio_to_baseline": rule["max_mae_ratio_to_baseline"],
        },
        "verificaciones": checks,
        "motivos_rechazo": failed,
        "metricas": metrics,
        "nota": "La publicación la implementa el siguiente módulo; esta decisión solo autoriza o bloquea el candidato.",
    }
