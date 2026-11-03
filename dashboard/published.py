"""Lectura de la versión aprobada y vigente de `published/` para el tablero.

Solo lee: no publica, no restaura, no ejecuta el pipeline y no abre ninguna versión
distinta de la que señala `CURRENT`. Las reglas de validación no se duplican: se usan
`pipeline.publish.verify_directory` (estructura, manifest con huellas SHA-256,
decisión favorable, seis controles de calidad, contrato de estimaciones y fuente
permitida) y `pipeline.contract.read_estimates`. Aquí solo se añade lo que el tablero
necesita para mostrar cifras: que los metadatos publicados tengan los campos que se
muestran.

Los mensajes de error se componen con códigos conocidos; nunca incluyen rutas locales.
Este módulo no importa Streamlit para poder probarse sin interfaz.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.config import load_config, resolve
from pipeline.contract import TYPE_ESTIMATED, TYPE_OBSERVED, read_estimates
from pipeline.metadata import SOURCE_INSTITUTIONAL, SOURCE_SYNTHETIC
from pipeline.publish import (
    CURRENT_FILE,
    ENV_DIR,
    VERSION_FILES,
    VERSIONS_DIR,
    load_settings,
    read_current,
    verify_directory,
)

CONFIG_PATH = "config/config.yaml"

STATUS_OK = "ok"
STATUS_NONE = "sin_publicacion"
STATUS_ERROR = "error"

# Campos de metadata.json que muestran las páginas; su ausencia invalida la versión.
_REQUIRED_PATHS = (
    ("version_procedimiento",),
    ("fuente", "tipo"),
    ("objetivo", "destino"), ("objetivo", "variable"), ("objetivo", "unidad"),
    ("periodos", "evaluacion", "inicio"), ("periodos", "evaluacion", "fin"),
    ("periodos", "horizonte", "inicio"), ("periodos", "horizonte", "fin"),
    ("procedimiento", "metodo"), ("procedimiento", "periodo_estacional"), ("procedimiento", "baseline"),
    ("metricas", "mae_estimacion"), ("metricas", "rmse_estimacion"),
    ("metricas", "mae_referencia"), ("metricas", "rmse_referencia"),
    ("metricas", "razon_mae"), ("metricas", "meses_evaluados"), ("metricas", "meses_solicitados"),
    ("metricas", "meses_no_evaluados"),
    ("aceptacion", "aceptada"), ("aceptacion", "regla"), ("aceptacion", "verificaciones"),
    ("calidad", "controles"),
    ("limitaciones",), ("advertencias",),
    ("publicacion", "version_id"), ("publicacion", "fecha_publicacion"), ("publicacion", "version_anterior"),
)

_REASONS = {
    "current_invalido": "El puntero de la versión vigente no contiene un identificador válido.",
    "version_inexistente": "La versión vigente no existe entre las versiones publicadas.",
    "archivo_faltante": "Faltan archivos de la versión vigente.",
    "archivos_inesperados": "La versión vigente contiene archivos que no le pertenecen.",
    "integridad_fallida": "Los archivos de la versión vigente no coinciden con su huella de integridad.",
    "manifest_invalido": "El manifiesto de la versión vigente no tiene la estructura esperada.",
    "json_invalido": "Un archivo de metadatos de la versión vigente no se puede leer.",
    "metadatos_invalidos": "Los metadatos de la versión vigente no cumplen el esquema.",
    "metadatos_incompletos": "Los metadatos de la versión vigente están incompletos.",
    "no_aprobada": "La versión vigente no figura como aprobada.",
    "contrato_invalido": "Las estimaciones de la versión vigente no cumplen el contrato de resultados.",
    "calidad_no_aprobada": "Los controles de calidad de la versión vigente no están aprobados.",
    "id_inconsistente": "Los identificadores de la versión vigente no son consistentes.",
    "fuente_no_declarada": "La fuente de la versión vigente no está declarada.",
    "fuente_invalida": "El tipo de fuente de la versión vigente no es válido.",
    "sintetica_no_permitida": "La versión vigente es sintética y no está permitida.",
    "version_invalida": "El identificador de la versión vigente no es válido.",
    "resultados_vacios": "La versión vigente no contiene observaciones o estimaciones.",
    "version_ilegible": "La versión vigente no se pudo leer o validar.",
}
_GENERIC_REASON = "La versión vigente no superó la validación."


@dataclass(frozen=True, eq=False)
class ApprovedVersion:
    """Versión vigente, ya validada, lista para mostrar."""

    version_id: str
    metadata: dict
    decision: dict
    estimates: pd.DataFrame  # columnas: periodo (datetime), tipo, valor

    @property
    def source_type(self) -> str:
        return self.metadata["fuente"]["tipo"]

    @property
    def is_synthetic(self) -> bool:
        return self.source_type == SOURCE_SYNTHETIC

    @property
    def is_institutional(self) -> bool:
        return self.source_type == SOURCE_INSTITUTIONAL

    @property
    def destination(self) -> str:
        return self.metadata["objetivo"]["destino"]

    @property
    def variable(self) -> str:
        return self.metadata["objetivo"]["variable"]

    @property
    def unit(self) -> str:
        return self.metadata["objetivo"]["unidad"]

    @property
    def published_at(self) -> str:
        return self.metadata["publicacion"]["fecha_publicacion"]

    @property
    def previous_version(self) -> str | None:
        return self.metadata["publicacion"]["version_anterior"]

    @property
    def observed(self) -> pd.DataFrame:
        return self.estimates[self.estimates["tipo"] == TYPE_OBSERVED]

    @property
    def estimated(self) -> pd.DataFrame:
        return self.estimates[self.estimates["tipo"] == TYPE_ESTIMATED]

    def summary(self) -> dict:
        """Resumen de lo publicado; todo sale de las filas de `estimaciones.csv`."""
        observed, estimated = self.observed, self.estimated
        last = observed.iloc[-1]
        return {
            "primer_observado": observed["periodo"].iloc[0],
            "ultimo_observado": last["periodo"],
            "ultimo_valor": float(last["valor"]),
            "meses_observados": int(len(observed)),
            "primer_estimado": estimated["periodo"].iloc[0],
            "ultimo_estimado": estimated["periodo"].iloc[-1],
            "meses_estimados": int(len(estimated)),
        }


@dataclass(frozen=True, eq=False)
class LoadResult:
    status: str
    version: ApprovedVersion | None = None
    message: str = ""
    reasons: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def published_root() -> Path:
    """Carpeta de publicación según `run.published_dir`; `LAB11_PUBLISHED_DIR` tiene prioridad."""
    try:
        return load_settings(load_config(CONFIG_PATH)).root
    except Exception:
        # Sin configuración legible se usa la variable de entorno o la carpeta por defecto.
        return resolve(os.environ.get(ENV_DIR) or "published")


def format_stamp(stamp: str | None) -> str:
    """Fecha ISO de publicación como 'AAAA-MM-DD HH:MM UTC' (sin convertir de zona horaria)."""
    try:
        return datetime.fromisoformat(str(stamp)).strftime("%Y-%m-%d %H:%M") + " UTC"
    except ValueError:
        return "no disponible"


def _missing_paths(meta: dict) -> list[str]:
    missing = []
    for path in _REQUIRED_PATHS:
        node = meta
        for key in path:
            if not isinstance(node, dict) or key not in node:
                missing.append(".".join(path))
                break
            node = node[key]
    return missing


def _failure(codes: list[str]) -> LoadResult:
    reasons = [_REASONS.get(code, _GENERIC_REASON) for code in dict.fromkeys(codes)]
    return LoadResult(
        STATUS_ERROR,
        message="La versión vigente no pasó la validación, por eso no se muestran cifras.",
        reasons=reasons,
        codes=list(dict.fromkeys(codes)),
    )


def load_current(root: str | Path) -> LoadResult:
    """Valida y carga la versión que señala `CURRENT`; nunca lanza excepciones.

    Devuelve `sin_publicacion` si no hay puntero, `error` si la versión vigente no es
    íntegra o aprobada, y `ok` con la versión cargada en caso contrario.
    """
    root = Path(root)
    if not (root / CURRENT_FILE).is_file():
        return LoadResult(
            STATUS_NONE,
            message="Todavía no hay una versión aprobada publicada.",
            codes=["sin_publicacion"],
        )
    try:
        return _load(root)
    except Exception:
        return _failure(["version_ilegible"])


def _load(root: Path) -> LoadResult:
    version_id = read_current(root)
    if version_id is None:
        return _failure(["current_invalido"])
    directory = root / VERSIONS_DIR / version_id
    if not directory.is_dir():
        return _failure(["version_inexistente"])

    problems = verify_directory(directory, version_id, allow_synthetic=True)
    if problems:
        return _failure([p.code for p in problems])

    metadata = json.loads((directory / VERSION_FILES["metadata"]).read_text(encoding="utf-8"))
    decision = json.loads((directory / VERSION_FILES["decision"]).read_text(encoding="utf-8"))
    if _missing_paths(metadata) or metadata["publicacion"]["version_id"] != version_id or decision.get("aceptada") is not True:
        return _failure(["metadatos_incompletos"])

    frame = read_estimates(directory / VERSION_FILES["estimaciones"])
    estimates = pd.DataFrame({
        "periodo": pd.to_datetime(frame["periodo"]),
        "tipo": frame["tipo"].astype(str),
        "valor": pd.to_numeric(frame["valor"]),
    }).sort_values("periodo").reset_index(drop=True)
    if not (estimates["tipo"] == TYPE_OBSERVED).any() or not (estimates["tipo"] == TYPE_ESTIMATED).any():
        return _failure(["resultados_vacios"])

    return LoadResult(STATUS_OK, ApprovedVersion(version_id, metadata, decision, estimates))
