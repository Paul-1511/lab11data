"""Metadatos de una ejecución: construcción, validación y clasificación de la fuente.

`metadata.json` describe un resultado candidato con información verificable y sin
rutas locales. Los campos que dependen de etapas posteriores (`mlflow`,
`publicacion`) quedan en `null` hasta que esas etapas existan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath

import pandas as pd

from .contract import ContractError, ContractResult, TYPE_ESTIMATED, TYPE_OBSERVED

SCHEMA_VERSION = "1.0"

SOURCE_SYNTHETIC = "sintetica"
SOURCE_INSTITUTIONAL = "institucional"
SOURCE_UNDECLARED = "no_declarada"
SOURCE_KINDS = (SOURCE_SYNTHETIC, SOURCE_INSTITUTIONAL)
SOURCE_TYPES = SOURCE_KINDS + (SOURCE_UNDECLARED,)

REQUIRED_KEYS = (
    "esquema_version", "run_id", "fecha_generacion", "version_procedimiento", "commit_git",
    "configuracion", "fuente", "objetivo", "periodos", "cobertura", "procedimiento", "metricas",
    "calidad", "aceptacion", "contrato", "advertencias", "limitaciones", "mlflow", "publicacion",
    "integridad",
)

# Letra de unidad (C:\ o C:/), ruta UNC o ruta POSIX que empieza en un directorio.
_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'(=])(?:[A-Za-z]:[\\/]|\\\\|/[A-Za-z0-9_.-]+/)")


class MetadataError(Exception):
    """Los metadatos no cumplen el esquema."""


def classify_source(
    cfg: dict, sha256: str, *, declared: str | None = None, path_overridden: bool = False
) -> dict:
    """Clasifica la fuente como sintética, institucional o no declarada.

    No se usa el nombre del archivo. El orden de decisión es:
    1. Una huella listada en `source.synthetic_sha256` es sintética, aunque se
       declare otra cosa; el conflicto queda registrado.
    2. La declaración de la ejecución (`declared`).
    3. `source.kind` de la configuración, solo si la ruta no fue reemplazada con
       `--data`: la declaración de la configuración describe `source.path`, no
       otro archivo.
    4. Sin declaración, la fuente es `no_declarada`.
    """
    source_cfg = cfg.get("source", {})
    if declared is not None and declared not in SOURCE_KINDS:
        raise ValueError(f"tipo de fuente no válido: {declared}. Opciones: {list(SOURCE_KINDS)}")
    config_kind = source_cfg.get("kind")
    if config_kind is not None and config_kind not in SOURCE_KINDS:
        raise ValueError(f"source.kind no válido: {config_kind}. Opciones: {list(SOURCE_KINDS)}")

    known = {str(h).lower() for h in (source_cfg.get("synthetic_sha256") or [])}
    if declared is not None:
        declaration, criterion = declared, "declaracion_de_la_ejecucion"
    elif config_kind is not None and not path_overridden:
        declaration, criterion = config_kind, "declaracion_de_la_configuracion"
    else:
        declaration, criterion = None, "sin_declaracion"

    if sha256.lower() in known:
        return {
            "tipo": SOURCE_SYNTHETIC,
            "criterio": "huella_sintetica_registrada",
            "conflicto_con_declaracion": declaration == SOURCE_INSTITUTIONAL,
        }
    if declaration is not None:
        return {"tipo": declaration, "criterio": criterion, "conflicto_con_declaracion": False}
    return {"tipo": SOURCE_UNDECLARED, "criterio": criterion, "conflicto_con_declaracion": False}


def find_absolute_paths(value, location: str = "$") -> list[str]:
    """Ubicaciones dentro de una estructura JSON cuyo texto parece una ruta absoluta."""
    found: list[str] = []
    if isinstance(value, str):
        if _ABSOLUTE_PATH.search(value):
            found.append(location)
    elif isinstance(value, dict):
        for key, item in value.items():
            found += find_absolute_paths(item, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            found += find_absolute_paths(item, f"{location}[{i}]")
    return found


def _unique(candidates: pd.DataFrame, column: str):
    if column not in candidates.columns or candidates.empty:
        return None
    values = candidates[column].dropna().astype(str).unique()
    return str(values[0]) if len(values) == 1 else None


def _coverage(candidates: pd.DataFrame) -> dict:
    empty = {"primer_mes_observado": None, "ultimo_mes_observado": None,
             "meses_observados": 0, "meses_estimados": 0}
    if candidates.empty or not {"periodo", "tipo"}.issubset(candidates.columns):
        return empty
    periods = pd.to_datetime(candidates["periodo"], errors="coerce")
    observed = periods[candidates["tipo"] == TYPE_OBSERVED].dropna()
    estimated = periods[candidates["tipo"] == TYPE_ESTIMATED].dropna()
    return {
        "primer_mes_observado": None if observed.empty else observed.min().strftime("%Y-%m"),
        "ultimo_mes_observado": None if observed.empty else observed.max().strftime("%Y-%m"),
        "meses_observados": int(observed.nunique()),
        "meses_estimados": int(estimated.nunique()),
    }


def _ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def build_limitations(cfg: dict, log: dict, source_type: str) -> list[dict]:
    """Limitaciones que se derivan de la configuración y de lo que hizo la ejecución.

    Cada una nombra la evidencia de la que sale; no se incluyen limitaciones que
    solo existan como texto libre.
    """
    params, periods = cfg["parameters"], cfg["periods"]
    metrics = log.get("metricas") or {}
    out: list[dict] = []

    if source_type == SOURCE_SYNTHETIC:
        out.append({
            "id": "fuente_sintetica",
            "texto": "Los resultados provienen de datos sintéticos y no son cifras institucionales.",
            "evidencia": "fuente.tipo",
        })
    elif source_type == SOURCE_UNDECLARED:
        out.append({
            "id": "fuente_no_declarada",
            "texto": "El origen de la fuente no fue declarado; los resultados no deben presentarse como cifras institucionales.",
            "evidencia": "fuente.tipo",
        })

    if params.get("estimator") == "seasonal_naive":
        out.append({
            "id": "estimador_estacional_ingenuo",
            "texto": (
                f"Cada mes se estima con el valor observado del mismo mes {params['seasonal_period']} meses antes; "
                "el procedimiento no modela tendencia ni incertidumbre."
            ),
            "evidencia": "parameters.estimator; parameters.seasonal_period; pipeline.evaluation._seasonal_naive",
        })

    out.append({
        "id": "ventana_de_ajuste_sin_parametros",
        "texto": (
            f"La ventana de ajuste configurada ({periods['fit']['start']} a {periods['fit']['end']}) solo define la "
            "cobertura exigida por los controles; el estimador no ajusta parámetros con ella."
        ),
        "evidencia": "periods.fit; pipeline.checks._required_months; pipeline.evaluation.evaluate_temporally",
    })

    if metrics.get("meses_solicitados") is not None:
        texto = (
            f"La evaluación cubre {metrics['meses_evaluados']} de {metrics['meses_solicitados']} meses solicitados "
            f"entre {periods['eval']['start']} y {periods['eval']['end']}, en una sola ventana con origen móvil."
        )
        if metrics.get("meses_no_evaluados"):
            texto += f" Meses sin evaluar: {metrics['meses_no_evaluados']}."
        out.append({"id": "evaluacion_ventana_unica", "texto": texto, "evidencia": "metricas"})

    filters = (log.get("poblacion") or {}).get("filtros") or []
    if filters:
        detail = "; ".join(f"{f['columna']} en {f['valores']}" for f in filters)
        out.append({
            "id": "poblacion_de_referencia",
            "texto": f"El indicador cuenta solo la población de referencia: {detail}.",
            "evidencia": "filters; poblacion.filtros",
        })

    out.append({
        "id": "indicador_nacional_sin_reparto",
        "texto": (
            "El indicador es la suma mensual de las filas de la población de referencia para el destino "
            "configurado; el procedimiento no distribuye visitantes entre destinos."
        ),
        "evidencia": "pipeline.prepare.prepare (suma directa de las filas de la población de referencia)",
    })

    if source_type == SOURCE_INSTITUTIONAL and str(periods["fit"]["start"]) >= "2023-01":
        out.append({
            "id": "tramo_homogeneo_desde_2023",
            "texto": (
                f"El ajuste y la evaluación comienzan en {periods['fit']['start']}: la hoja Notas de la fuente "
                "indica un cambio metodológico entre 2022 y 2023."
            ),
            "evidencia": "periods.fit; docs/ALCANCE.md sección 2.2",
        })
    return out


def build_metadata(
    cfg: dict,
    log: dict,
    results: list,
    candidates: pd.DataFrame,
    decision: dict,
    *,
    candidates_sha256: str | None = None,
) -> dict:
    """Construye `metadata.json` a partir de los resultados de una ejecución.

    `log` es `RunLog.data`. Las rutas locales de `log` no se copian: la fuente se
    identifica por nombre de archivo, huella y tamaño.
    """
    source = log.get("fuente") or {}
    population = log.get("poblacion") or {}
    metrics = log.get("metricas") or decision.get("metricas") or {}
    commit = log.get("commit_git")
    name = source.get("nombre_archivo") or (PureWindowsPath(source["path"]).name if source.get("path") else None)
    source_type = source.get("tipo", SOURCE_UNDECLARED)

    return {
        "esquema_version": SCHEMA_VERSION,
        "run_id": log["run_id"],
        "fecha_generacion": _unique(candidates, "fecha_generacion"),
        "version_procedimiento": log["version_procedimiento"],
        "commit_git": None if commit in (None, "sin-git") else commit,
        "arbol_git_modificado": log.get("arbol_git_modificado"),
        "commit_git_origen": log.get("commit_git_origen") or ("no_disponible" if commit in (None, "sin-git") else "git"),
        "configuracion": {"sha256": log["config"]["sha256"], "semilla": log["semilla"]},
        "fuente": {
            "tipo": source_type,
            "criterio": source.get("criterio"),
            "conflicto_con_declaracion": source.get("conflicto_con_declaracion", False),
            "nombre_archivo": name,
            "sha256": source.get("sha256"),
            "bytes": source.get("bytes"),
            "filas": source.get("filas"),
            "filas_poblacion": population.get("filas_poblacion"),
            "filtros": population.get("filtros", []),
        },
        "objetivo": {
            "destino": cfg["target"]["destination"],
            "variable": cfg["target"]["name"],
            "unidad": cfg["target"]["unit"],
            "granularidad": cfg["target"]["granularity"],
        },
        "periodos": {
            "esperado": dict(zip(("inicio", "fin"), (cfg["periods"]["expected_start"], cfg["periods"]["expected_end"]))),
            "ajuste": {"inicio": cfg["periods"]["fit"]["start"], "fin": cfg["periods"]["fit"]["end"]},
            "evaluacion": {"inicio": cfg["periods"]["eval"]["start"], "fin": cfg["periods"]["eval"]["end"]},
            "horizonte": {"inicio": cfg["periods"]["forecast"]["start"], "fin": cfg["periods"]["forecast"]["end"]},
        },
        "cobertura": _coverage(candidates),
        "procedimiento": {
            "metodo": metrics.get("metodo", cfg["parameters"]["estimator"]),
            "periodo_estacional": cfg["parameters"]["seasonal_period"],
            "baseline": cfg["parameters"]["baseline"],
        },
        "metricas": {
            "mae_estimacion": metrics.get("mae_estimacion"),
            "rmse_estimacion": metrics.get("rmse_estimacion"),
            "mae_referencia": metrics.get("mae_referencia"),
            "rmse_referencia": metrics.get("rmse_referencia"),
            "razon_mae": _ratio(metrics.get("mae_estimacion"), metrics.get("mae_referencia")),
            "meses_evaluados": metrics.get("meses_evaluados"),
            "meses_solicitados": metrics.get("meses_solicitados"),
            "meses_no_evaluados": metrics.get("meses_no_evaluados", []),
        },
        "calidad": {
            "estado_global": _overall(results),
            "controles": [
                {"id": r.id, "nombre": r.name, "estado": r.status, "mensaje": r.message} for r in results
            ],
        },
        "aceptacion": {
            "aceptada": decision["aceptada"],
            "regla": decision["regla"],
            "verificaciones": decision["verificaciones"],
            "motivos_rechazo": decision["motivos_rechazo"],
        },
        "contrato": decision["contrato"],
        "advertencias": list(log.get("advertencias", [])),
        "limitaciones": build_limitations(cfg, log, source_type),
        "mlflow": None,
        "publicacion": None,
        "integridad": {"estimaciones_candidatas_sha256": candidates_sha256},
    }


def _overall(results: list) -> str:
    statuses = {r.status for r in results}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


def validate_metadata(meta: dict) -> ContractResult:
    """Comprueba claves obligatorias, tipo de fuente y ausencia de rutas absolutas."""
    result = ContractResult(rows=0)

    def add(code: str, message: str, **detail) -> None:
        result.errors.append(ContractError(code, message, None, detail))

    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        add("clave_faltante", f"faltan claves obligatorias: {missing}", claves=missing)
        return result
    if meta["esquema_version"] != SCHEMA_VERSION:
        add("esquema_no_soportado", f"esquema_version debe ser {SCHEMA_VERSION}", encontrado=meta["esquema_version"])
    if not meta["run_id"]:
        add("run_id_vacio", "run_id no puede estar vacío")
    if meta["fuente"].get("tipo") not in SOURCE_TYPES:
        add("tipo_de_fuente_invalido", f"fuente.tipo debe ser uno de {list(SOURCE_TYPES)}", encontrado=meta["fuente"].get("tipo"))
    if not isinstance(meta["aceptacion"].get("aceptada"), bool):
        add("aceptacion_invalida", "aceptacion.aceptada debe ser booleano")
    if not meta["limitaciones"] or any(not item.get("evidencia") for item in meta["limitaciones"]):
        add("limitaciones_sin_evidencia", "cada limitación debe indicar su evidencia y la lista no puede estar vacía")
    paths = find_absolute_paths(meta)
    if paths:
        add("ruta_absoluta", "los metadatos no pueden contener rutas absolutas", ubicaciones=paths)
    return result


def write_metadata(meta: dict, path: Path) -> None:
    """Escribe `metadata.json`; no escribe si el esquema no es válido."""
    result = validate_metadata(meta)
    if not result.valid:
        raise MetadataError("; ".join(f"{e.code}: {e.message}" for e in result.errors))
    Path(path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
