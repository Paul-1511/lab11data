"""Monitoreo de actualización, calidad y resultados de cada ejecución.

Las alertas no cambian la semántica del pipeline: no modifican códigos de salida, la regla de
aceptación ni la publicación. Tienen dos severidades:

- `critica`: la condición ya bloquea la publicación por otra vía (control de calidad fallido o
  contrato de estimaciones inválido); el monitoreo la hace visible con nombre propio.
- `advertencia`: requiere revisión humana; por sí sola no demuestra pérdida de precisión.

Las señales de calidad se toman de los resultados de los seis controles (`CheckResult.details`),
sin recalcularlos. Las comparaciones usan como línea base la versión que señala `CURRENT` antes de
publicar la ejecución, solo si es íntegra, de la misma clase de fuente y la publicación está
habilitada. Sin línea base solo se aplican las reglas absolutas. Los meses sin cobertura nunca se
convierten en cero ni se estiman a partir de otros valores aquí.

`monitor_run` no lanza excepciones: un fallo del monitoreo se informa en su resultado.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .checks import FAIL, SKIP
from .dates import month_range
from .publish import VERSION_FILES, VERSIONS_DIR, load_settings as load_publication_settings, read_current, verify_directory
from .tracking import redact_paths

ENV_ENABLED = "LAB11_MONITOR_ENABLED"

SEVERITY_CRITICAL = "critica"
SEVERITY_WARNING = "advertencia"

CAT_FRESHNESS = "actualizacion"
CAT_QUALITY = "calidad"
CAT_RESULTS = "resultados"

STATE_CLEAN = "sin_alertas"
STATE_ALERTS = "con_alertas"
STATE_ERROR = "error"

# Valores iniciales conservadores: ver docs/MONITOREO.md para su justificación y límites.
DEFAULTS = {
    "freshness": {
        "max_months_behind": 0,           # meses de atraso del último observado frente a periods.expected_end
        "max_publication_age_days": 45,   # antigüedad máxima de la versión vigente (fuente mensual)
    },
    "quality": {
        "missing_warn_fraction": 0.02,    # fracción de faltantes que avisa antes del umbral que detiene
        "missing_max_increase": 0.005,    # aumento absoluto de la fracción de faltantes frente a la versión vigente
        "duplicates_max_increase": 0.0,   # cualquier aumento de la fracción de claves duplicadas
        "coverage_max_months_lost": 0,    # meses observados en la versión vigente que dejan de estar
    },
    "results": {
        "max_monthly_change": 0.40,       # variación mensual relativa que dispara la revisión
    },
}

_MAX_ITEMS = 12


@dataclass(frozen=True)
class Alert:
    categoria: str
    codigo: str
    severidad: str
    mensaje: str
    detalle: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Baseline:
    """Lo que se compara de la versión vigente antes de publicar la ejecución."""

    version: str
    published_at: str | None
    source_type: str
    observed: set
    last_observed: pd.Timestamp
    last_period: pd.Timestamp
    quality: dict


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def monitoring_enabled(cfg: dict) -> bool:
    raw = os.environ.get(ENV_ENABLED)
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool((cfg.get("monitoring") or {}).get("enabled", True))


def thresholds(cfg: dict) -> dict:
    """Umbrales de `monitoring` sobre los valores por defecto; valida que sean números no negativos."""
    merged = copy.deepcopy(DEFAULTS)
    section = cfg.get("monitoring") or {}
    for group, values in merged.items():
        for key in values:
            if key in (section.get(group) or {}):
                merged[group][key] = section[group][key]
    for group, values in merged.items():
        for key, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"monitoring.{group}.{key} debe ser un número mayor o igual que cero")
    return merged


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _m(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m")


def _index(ts) -> int:
    ts = pd.Timestamp(ts)
    return ts.year * 12 + ts.month


def _months_between(start, end) -> int:
    """Meses de `start` a `end` (positivo si `end` es posterior)."""
    return _index(end) - _index(start)


def _detail(results: list, check_id: str) -> tuple[str | None, dict]:
    for r in results:
        if r.id == check_id:
            return r.status, (r.details or {})
    return None, {}


def _evaluable(status: str | None) -> bool:
    return status is not None and status != SKIP


def _finite(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan)


def _observed_months(cfg: dict, results: list, candidates: pd.DataFrame | None) -> list[pd.Timestamp] | None:
    """Meses con observación. Con candidatos salen de ellos; si no, del reporte del control b."""
    if candidates is not None:
        rows = candidates[candidates["tipo"] == "observado"]
        values = _finite(rows["valor"])
        periods = pd.to_datetime(rows["periodo"])[np.isfinite(values)]
        return sorted(set(pd.Timestamp(p) for p in periods))
    status, detail = _detail(results, "b_fechas_cobertura")
    if not _evaluable(status):
        return None
    absent = {
        pd.Timestamp(m)
        for key in ("meses_sin_datos_requeridos", "meses_sin_datos_no_requeridos", "huecos_permitidos_configurados")
        for m in detail.get(key, [])
    }
    expected = month_range(cfg["periods"]["expected_start"], cfg["periods"]["expected_end"])
    return [m for m in expected if m not in absent]


# ---------------------------------------------------------------------------
# Línea base: versión vigente
# ---------------------------------------------------------------------------
def load_baseline(cfg: dict) -> tuple[Baseline | None, str]:
    """Versión vigente y su motivo de indisponibilidad; no escribe nada."""
    try:
        settings = load_publication_settings(cfg)
        if not settings.enabled:
            return None, "publicacion_deshabilitada"
        version = read_current(settings.root)
        if version is None:
            return None, "sin_version_vigente"
        directory = settings.root / VERSIONS_DIR / version
        if not directory.is_dir() or verify_directory(directory, version, allow_synthetic=True):
            return None, "version_vigente_no_valida"
        meta = json.loads((directory / VERSION_FILES["metadata"]).read_text(encoding="utf-8"))
        quality = json.loads((directory / VERSION_FILES["quality"]).read_text(encoding="utf-8"))
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        frame = pd.read_csv(directory / VERSION_FILES["estimaciones"], dtype=str)
        frame["periodo"] = pd.to_datetime(frame["periodo"])
        observed = set(pd.Timestamp(p) for p in frame.loc[frame["tipo"] == "observado", "periodo"])
        if not observed:
            return None, "version_vigente_no_valida"
        return Baseline(
            version=version,
            published_at=manifest.get("fecha_publicacion"),
            source_type=meta["fuente"]["tipo"],
            observed=observed,
            last_observed=max(observed),
            last_period=frame["periodo"].max(),
            quality={c["id"]: (c.get("details") or {}) for c in quality.get("controles", [])},
        ), "disponible"
    except Exception:
        return None, "error_de_lectura"


# ---------------------------------------------------------------------------
# Actualización
# ---------------------------------------------------------------------------
def check_freshness(
    cfg: dict, results: list, candidates: pd.DataFrame | None, baseline: Baseline | None,
    th: dict, now: datetime,
) -> list[Alert]:
    alerts: list[Alert] = []
    limits = th["freshness"]
    expected_end = pd.Timestamp(cfg["periods"]["expected_end"])
    observed = _observed_months(cfg, results, candidates)
    last = observed[-1] if observed else None

    # Fuente desactualizada: el último mes observado está atrás del último mes esperado.
    if last is not None:
        behind = _months_between(last, expected_end)
        if behind > limits["max_months_behind"]:
            alerts.append(Alert(
                CAT_FRESHNESS, "fuente_desactualizada", SEVERITY_WARNING,
                f"El último período observado ({_m(last)}) está {behind} mes(es) antes del esperado ({_m(expected_end)}).",
                {"ultimo_periodo_observado": _m(last), "ultimo_periodo_esperado": _m(expected_end),
                 "meses_de_atraso": behind, "umbral_meses": limits["max_months_behind"]},
            ))

    status, detail = _detail(results, "b_fechas_cobertura")
    if _evaluable(status):
        required = list(detail.get("meses_sin_datos_requeridos", []))
        if required:
            alerts.append(Alert(
                CAT_FRESHNESS, "meses_requeridos_ausentes", SEVERITY_CRITICAL,
                f"Faltan {len(required)} mes(es) requeridos para ajuste o evaluación; no se rellenan con cero.",
                {"meses": required[:_MAX_ITEMS], "total": len(required)},
            ))
        # Los meses finales sin dato ya los explica la fuente desactualizada; aquí solo los huecos internos.
        gaps = [m for m in detail.get("meses_sin_datos_no_requeridos", []) if last is None or pd.Timestamp(m) < last]
        if gaps:
            alerts.append(Alert(
                CAT_FRESHNESS, "huecos_en_la_serie", SEVERITY_WARNING,
                f"Hay {len(gaps)} mes(es) sin cobertura dentro de la serie observada; quedan vacíos, no en cero.",
                {"meses": gaps[:_MAX_ITEMS], "total": len(gaps)},
            ))

    # Horizonte incompleto: la salida no cubre el rango de pronóstico configurado.
    if candidates is not None:
        forecast = cfg["periods"]["forecast"]
        expected_horizon = list(month_range(forecast["start"], forecast["end"]))
        rows = candidates[candidates["tipo"] == "estimado"]
        present = set(pd.Timestamp(p) for p in pd.to_datetime(rows["periodo"])[np.isfinite(_finite(rows["valor"]))])
        missing = [m for m in expected_horizon if m not in present]
        if missing:
            alerts.append(Alert(
                CAT_FRESHNESS, "horizonte_incompleto", SEVERITY_CRITICAL,
                f"El horizonte estimado tiene {len(expected_horizon) - len(missing)} de {len(expected_horizon)} meses.",
                {"esperados": len(expected_horizon), "disponibles": len(expected_horizon) - len(missing),
                 "meses_faltantes": [_m(m) for m in missing][:_MAX_ITEMS]},
            ))

    if baseline is not None:
        behind = _months_between(baseline.last_observed, expected_end)
        if behind > limits["max_months_behind"]:
            alerts.append(Alert(
                CAT_FRESHNESS, "publicacion_desactualizada", SEVERITY_WARNING,
                f"La versión vigente ({baseline.version}) llega hasta {_m(baseline.last_observed)}, "
                f"{behind} mes(es) antes del último esperado ({_m(expected_end)}).",
                {"version": baseline.version, "ultimo_periodo_observado": _m(baseline.last_observed),
                 "ultimo_periodo_publicado": _m(baseline.last_period), "meses_de_atraso": behind},
            ))
        try:
            age = (now - datetime.fromisoformat(str(baseline.published_at))).days
        except (ValueError, TypeError):
            age = None
        if age is not None and age > limits["max_publication_age_days"]:
            alerts.append(Alert(
                CAT_FRESHNESS, "publicacion_antigua", SEVERITY_WARNING,
                f"La versión vigente se publicó hace {age} días.",
                {"version": baseline.version, "dias": age, "umbral_dias": limits["max_publication_age_days"]},
            ))
        if last is not None and last < baseline.last_observed:
            alerts.append(Alert(
                CAT_FRESHNESS, "candidato_con_menos_datos", SEVERITY_WARNING,
                f"El último período observado de la ejecución ({_m(last)}) es anterior al de la versión vigente "
                f"({_m(baseline.last_observed)}).",
                {"ultimo_observado_ejecucion": _m(last), "ultimo_observado_vigente": _m(baseline.last_observed)},
            ))
    return alerts


# ---------------------------------------------------------------------------
# Calidad (reutiliza los detalles de los controles c, d y b)
# ---------------------------------------------------------------------------
def check_quality(
    cfg: dict, results: list, candidates: pd.DataFrame | None, baseline: Baseline | None, th: dict
) -> list[Alert]:
    alerts: list[Alert] = []
    limits = th["quality"]
    rules = cfg["quality"]["missing"]

    status, detail = _detail(results, "d_faltantes")
    if _evaluable(status):
        previous = baseline.quality.get("d_faltantes", {}) if baseline else {}
        for group, limit_key in (("que_impiden_estimar", "max_blocking_frac"), ("permitidos", "max_allowed_frac")):
            for column, values in detail.get(group, {}).items():
                fraction = values.get("fraccion", 0.0)
                before = (previous.get(group, {}).get(column) or {}).get("fraccion")
                blocking = fraction > rules[limit_key]
                severity = SEVERITY_CRITICAL if blocking else SEVERITY_WARNING
                base = {"columna": column, "fraccion": fraction, "fraccion_vigente": before}
                if before is not None and fraction - before > limits["missing_max_increase"]:
                    alerts.append(Alert(
                        CAT_QUALITY, "faltantes_aumentaron", severity,
                        f"Los faltantes de «{column}» pasaron de {before:.2%} a {fraction:.2%}.",
                        {**base, "aumento": round(fraction - before, 6), "umbral_aumento": limits["missing_max_increase"]},
                    ))
                if fraction > limits["missing_warn_fraction"]:
                    alerts.append(Alert(
                        CAT_QUALITY, "faltantes_altos", severity,
                        f"«{column}» tiene {fraction:.2%} de faltantes.",
                        {**base, "umbral_fraccion": limits["missing_warn_fraction"]},
                    ))

    status, detail = _detail(results, "c_unicidad")
    if _evaluable(status):
        rows = detail.get("filas_en_claves_duplicadas", 0)
        fraction = detail.get("fraccion", 0.0)
        before = (baseline.quality.get("c_unicidad", {}).get("fraccion") if baseline else None)
        reference = 0.0 if before is None else before
        if rows > 0 and fraction - reference > limits["duplicates_max_increase"]:
            alerts.append(Alert(
                CAT_QUALITY, "duplicados_aumentaron", SEVERITY_CRITICAL if status == FAIL else SEVERITY_WARNING,
                f"{rows} fila(s) comparten clave ({fraction:.2%}); en la versión vigente: "
                f"{'sin línea base (se asume 0)' if before is None else format(before, '.2%')}.",
                {"filas_en_claves_duplicadas": rows, "fraccion": fraction, "fraccion_vigente": before,
                 "umbral_aumento": limits["duplicates_max_increase"]},
            ))

    if baseline is not None and candidates is not None:
        observed = _observed_months({}, results, candidates) or []
        lost = sorted(baseline.observed - set(observed))
        if len(lost) > limits["coverage_max_months_lost"]:
            alerts.append(Alert(
                CAT_QUALITY, "perdida_de_cobertura", SEVERITY_WARNING,
                f"{len(lost)} mes(es) observados en la versión vigente ya no tienen observación.",
                {"meses": [_m(m) for m in lost][:_MAX_ITEMS], "total": len(lost),
                 "umbral_meses": limits["coverage_max_months_lost"]},
            ))
    return alerts


# ---------------------------------------------------------------------------
# Resultados (sobre las estimaciones candidatas; no se calculan errores predictivos)
# ---------------------------------------------------------------------------
def check_results(cfg: dict, candidates: pd.DataFrame | None, th: dict) -> list[Alert]:
    if candidates is None:
        return []
    alerts: list[Alert] = []
    estimated = candidates[candidates["tipo"] == "estimado"]
    values = _finite(estimated["valor"])
    periods = pd.to_datetime(estimated["periodo"], errors="coerce")
    finite = np.isfinite(values)

    bad = periods[~finite]
    if len(bad):
        alerts.append(Alert(
            CAT_RESULTS, "valores_no_finitos", SEVERITY_CRITICAL,
            f"{len(bad)} estimación(es) no son números finitos.",
            {"total": int(len(bad)), "periodos": [_m(p) if pd.notna(p) else None for p in bad][:_MAX_ITEMS]},
        ))
    negative = finite & (values < 0)
    if negative.any():
        alerts.append(Alert(
            CAT_RESULTS, "estimaciones_negativas", SEVERITY_CRITICAL,
            f"{int(negative.sum())} estimación(es) son negativas.",
            {"total": int(negative.sum()), "periodos": [_m(p) for p in periods[negative]][:_MAX_ITEMS]},
        ))

    limit = th["results"]["max_monthly_change"]
    observed = candidates[candidates["tipo"] == "observado"]
    obs_values = _finite(observed["valor"])
    obs_periods = pd.to_datetime(observed["periodo"], errors="coerce")
    keep = np.isfinite(obs_values) & obs_periods.notna().to_numpy()
    series = pd.DataFrame({"periodo": obs_periods[keep], "valor": obs_values[keep], "tipo": "observado"})
    last_observed = series.sort_values("periodo").tail(1)
    usable = finite & periods.notna().to_numpy() & (values >= 0)
    tail = pd.DataFrame({"periodo": periods[usable], "valor": values[usable], "tipo": "estimado"})
    sequence = pd.concat([last_observed, tail], ignore_index=True).sort_values("periodo").reset_index(drop=True)

    flagged, not_computable = [], 0
    for before, after in zip(sequence.itertuples(index=False), sequence.iloc[1:].itertuples(index=False)):
        if _months_between(before.periodo, after.periodo) != 1:
            continue
        if before.valor <= 0:
            not_computable += 1
            continue
        change = after.valor / before.valor - 1
        if abs(change) > limit:
            flagged.append({"periodo": _m(after.periodo), "anterior": float(before.valor),
                            "valor": float(after.valor), "variacion": round(float(change), 4)})
    if flagged:
        alerts.append(Alert(
            CAT_RESULTS, "variacion_mensual_alta", SEVERITY_WARNING,
            f"{len(flagged)} variación(es) mensual(es) de las estimaciones superan {limit:.0%}. "
            "Es una advertencia de revisión: no demuestra por sí sola pérdida de precisión.",
            {"umbral": limit, "total": len(flagged), "transiciones": flagged[:_MAX_ITEMS],
             "pares_no_calculables": not_computable,
             "maxima_variacion_observada": _historical_max_change(cfg, series)},
        ))
    return alerts


def _historical_max_change(cfg: dict, observed: pd.DataFrame) -> dict | None:
    """Mayor variación mensual entre observaciones consecutivas desde el inicio del ajuste: contexto para revisar."""
    window = observed[observed["periodo"] >= pd.Timestamp(cfg["periods"]["fit"]["start"])].sort_values("periodo")
    best = None
    for before, after in zip(window.itertuples(index=False), window.iloc[1:].itertuples(index=False)):
        if _months_between(before.periodo, after.periodo) != 1 or before.valor <= 0:
            continue
        change = abs(after.valor / before.valor - 1)
        if best is None or change > best["variacion_absoluta"]:
            best = {"periodo": _m(after.periodo), "variacion_absoluta": round(float(change), 4)}
    return best


# ---------------------------------------------------------------------------
# Ejecución completa del monitoreo
# ---------------------------------------------------------------------------
def monitor_run(
    cfg: dict,
    results: list,
    candidates: pd.DataFrame | None,
    *,
    source_type: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Evalúa las tres familias de señales. Nunca lanza excepciones."""
    now = now or datetime.now(timezone.utc)
    try:
        th = thresholds(cfg)
        baseline, reason = load_baseline(cfg)
        if baseline is not None and source_type is not None and baseline.source_type != source_type:
            baseline, reason = None, "tipo_de_fuente_distinto"
        alerts = (
            check_freshness(cfg, results, candidates, baseline, th, now)
            + check_quality(cfg, results, candidates, baseline, th)
            + check_results(cfg, candidates, th)
        )
        critical = sum(a.severidad == SEVERITY_CRITICAL for a in alerts)
        return {
            "estado": STATE_ALERTS if alerts else STATE_CLEAN,
            "linea_base": {"estado": reason, "version": baseline.version if baseline else None},
            "resumen": {"criticas": critical, "advertencias": len(alerts) - critical},
            "alertas": [a.to_dict() for a in alerts],
            "umbrales": th,
        }
    except Exception as exc:
        return {
            "estado": STATE_ERROR, "mensaje": redact_paths(exc),
            "linea_base": {"estado": "no_evaluada", "version": None},
            "resumen": {"criticas": 0, "advertencias": 0}, "alertas": [], "umbrales": None,
        }


def write_monitoring(report: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
