"""Registro de las ejecuciones del pipeline en MLflow Tracking.

Backend SQLite y artefactos en carpetas locales configurables (`mlflow` en
`config/config.yaml`; `LAB11_MLFLOW_DIR` reemplaza la carpeta base, por ejemplo
con un volumen montado). El `run_id` del pipeline y el de MLflow son distintos:
se relacionan con la etiqueta `pipeline.run_id`.

Ninguna operación de este módulo lanza excepciones hacia el pipeline. Si MLflow
falla, el error se guarda en el resumen y la trazabilidad queda `incompleta` o
`no_disponible`.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import resolve
from .report import overall_status

ENV_ENABLED = "LAB11_MLFLOW_ENABLED"
ENV_DIR = "LAB11_MLFLOW_DIR"
DEFAULT_EXPERIMENT = "observatorio_turismo_guatemala"

# Solo se registran estos archivos de la carpeta de ejecución, y únicamente si existen.
# La fuente original, las series mensuales y run_log.json (que guarda rutas locales) no se registran.
ARTIFACT_NAMES = (
    "quality_report.json", "quality_report.md", "evaluacion_temporal.csv",
    "estimaciones_candidatas.csv", "decision_aceptacion.json", "metadata.json", "config_snapshot.yaml",
)

TRACE_COMPLETE = "completa"
TRACE_INCOMPLETE = "incompleta"
TRACE_UNAVAILABLE = "no_disponible"
TRACE_DISABLED = "deshabilitada"

OUTCOME_ACCEPTED = "aceptada"
OUTCOME_REJECTED = "rechazada"
OUTCOME_QUALITY_BLOCKED = "bloqueada_por_calidad"
OUTCOME_ERROR = "error_inesperado"
OUTCOME_INTERRUPTED = "interrumpida"

_FALSE_VALUES = {"0", "false", "no", "off"}
_MAX_TEXT = 4000
_METRIC_KEYS = (
    "mae_estimacion", "rmse_estimacion", "mae_referencia", "rmse_referencia",
    "meses_evaluados", "meses_solicitados",
)
# Una ruta absoluta (unidad, UNC o POSIX) que no forme parte de otra palabra.
_PATH = re.compile(r"""(?<![\w.])(?:[A-Za-z]:[\\/]|\\\\|/(?=[\w.-]+[\\/]))[^\s'"]*""")


def redact_paths(text) -> str:
    """Sustituye cada ruta absoluta del texto por su último componente."""
    def _name(match: re.Match) -> str:
        parts = [p for p in re.split(r"[\\/]+", match.group(0)) if p]
        return parts[-1] if parts else "ruta"

    return _PATH.sub(_name, str(text))


@dataclass(frozen=True)
class TrackingSettings:
    enabled: bool
    experiment: str
    db_path: Path
    artifact_dir: Path

    @property
    def tracking_uri(self) -> str:
        return "sqlite:///" + self.db_path.as_posix()


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def load_settings(cfg: dict) -> TrackingSettings:
    """Lee la sección `mlflow`; las variables de entorno reemplazan `enabled` y `dir`."""
    section = cfg.get("mlflow") or {}
    base = resolve(os.environ.get(ENV_DIR) or section.get("dir", "mlflow"))
    return TrackingSettings(
        enabled=_as_bool(os.environ.get(ENV_ENABLED), bool(section.get("enabled", True))),
        experiment=section.get("experiment", DEFAULT_EXPERIMENT),
        db_path=base / section.get("db_file", "mlflow.db"),
        artifact_dir=base / section.get("artifacts_dir", "artifacts"),
    )


def _ms(moment: datetime | None = None) -> int:
    return int((moment or datetime.now(timezone.utc)).timestamp() * 1000)


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 3] + "..."


def _text(value) -> str | None:
    if value is None or value == "":
        return None
    return _clip(redact_paths(value))


def config_params(cfg: dict) -> dict[str, str]:
    """Parámetros de la configuración que definen el procedimiento."""
    periods, params, rule = cfg["periods"], cfg["parameters"], cfg.get("acceptance", {})
    values = {
        "destino": cfg["target"]["destination"],
        "variable_objetivo": cfg["target"]["name"],
        "unidad": cfg["target"]["unit"],
        "granularidad": cfg["target"]["granularity"],
        "periodo_esperado_inicio": periods["expected_start"],
        "periodo_esperado_fin": periods["expected_end"],
        "periodo_ajuste_inicio": periods["fit"]["start"],
        "periodo_ajuste_fin": periods["fit"]["end"],
        "periodo_evaluacion_inicio": periods["eval"]["start"],
        "periodo_evaluacion_fin": periods["eval"]["end"],
        "horizonte_inicio": periods["forecast"]["start"],
        "horizonte_fin": periods["forecast"]["end"],
        "metodo": params["estimator"],
        "periodo_estacional": params["seasonal_period"],
        "baseline": params["baseline"],
        "semilla": cfg["run"]["seed"],
        "umbral_min_meses_evaluados": rule.get("min_evaluated_months"),
        "umbral_max_razon_mae": rule.get("max_mae_ratio_to_baseline"),
    }
    return {k: str(v) for k, v in values.items() if v is not None}


class Tracker:
    """Una ejecución de MLflow asociada a una ejecución del pipeline."""

    def __init__(self, settings: TrackingSettings):
        self.settings = settings
        self.client = None
        self.run_id: str | None = None
        self.experiment_id: str | None = None
        self.status: str | None = None
        self.errors: list[str] = []

    @classmethod
    def start(
        cls, cfg: dict, pipeline_run_id: str, *, version: str, commit: str | None, tree_modified: bool | None
    ) -> "Tracker":
        tracker = cls(load_settings(cfg))
        if not tracker.settings.enabled:
            return tracker
        try:
            tracker._open(cfg, pipeline_run_id, version, commit, tree_modified)
        except Exception as exc:
            tracker._record("inicio", exc)
        return tracker

    def _record(self, step: str, exc: Exception) -> None:
        self.errors.append(f"{step}: {redact_paths(exc)}")

    def _open(self, cfg: dict, pipeline_run_id: str, version: str, commit, tree_modified) -> None:
        from mlflow.entities import Param
        from mlflow.tracking import MlflowClient

        settings = self.settings
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        client = MlflowClient(tracking_uri=settings.tracking_uri)
        experiment = client.get_experiment_by_name(settings.experiment)
        experiment_id = (
            experiment.experiment_id if experiment is not None
            else client.create_experiment(settings.experiment, artifact_location=settings.artifact_dir.as_uri())
        )
        tags = {
            "pipeline.run_id": pipeline_run_id,
            "pipeline.version": version,
            "git.commit": commit if commit not in (None, "sin-git") else None,
            "git.tree_modified": None if tree_modified is None else str(tree_modified).lower(),
            "config.sha256": cfg["_meta"]["sha256"],
        }
        run = client.create_run(
            experiment_id, start_time=_ms(), run_name=pipeline_run_id,
            tags={k: v for k, v in tags.items() if v is not None},
        )
        self.client, self.experiment_id, self.run_id = client, experiment_id, run.info.run_id
        try:
            client.log_batch(self.run_id, params=[Param(k, v) for k, v in config_params(cfg).items()])
        except Exception as exc:
            self._record("parametros", exc)

    def ids(self) -> dict | None:
        """Identificadores de MLflow para los metadatos; `None` si no hay ejecución registrada."""
        if self.run_id is None:
            return None
        return {"run_id": self.run_id, "experiment_id": self.experiment_id, "experimento": self.settings.experiment}

    def trace_state(self) -> str:
        if not self.settings.enabled:
            return TRACE_DISABLED
        if self.run_id is None:
            return TRACE_UNAVAILABLE
        return TRACE_INCOMPLETE if self.errors else TRACE_COMPLETE

    def summary(self) -> dict:
        return {
            "habilitado": self.settings.enabled,
            "trazabilidad": self.trace_state(),
            "experimento": self.settings.experiment if self.settings.enabled else None,
            "experimento_id": self.experiment_id,
            "run_id": self.run_id,
            "estado": self.status,
            "errores": list(self.errors),
        }

    def finalize(self, log: dict, results: list, run_dir: Path, *, exit_code: int, interrupted: bool = False) -> dict:
        """Registra etiquetas, métricas y artefactos, y cierra la ejecución de MLflow."""
        if self.run_id is None:
            return self.summary()
        outcome = _outcome(log, exit_code, interrupted)
        self._log_tags(log, results, exit_code, outcome)
        self._log_metrics(log, results)
        self._log_artifacts(Path(run_dir))
        self._terminate(exit_code, interrupted)
        return self.summary()

    def _log_tags(self, log: dict, results: list, exit_code: int, outcome: str) -> None:
        try:
            from mlflow.entities import RunTag

            self.client.log_batch(
                self.run_id, tags=[RunTag(k, v) for k, v in _result_tags(log, results, exit_code, outcome).items()]
            )
        except Exception as exc:
            self._record("etiquetas", exc)

    def _log_metrics(self, log: dict, results: list) -> None:
        try:
            from mlflow.entities import Metric

            now = _ms()
            metrics = _result_metrics(log, results)
            if metrics:
                self.client.log_batch(self.run_id, metrics=[Metric(k, v, now, 0) for k, v in metrics.items()])
        except Exception as exc:
            self._record("metricas", exc)

    def _log_artifacts(self, run_dir: Path) -> None:
        for name in ARTIFACT_NAMES:
            path = run_dir / name
            if not path.is_file():
                continue
            try:
                self.client.log_artifact(self.run_id, str(path))
            except Exception as exc:
                self._record(f"artefacto {name}", exc)

    def _terminate(self, exit_code: int, interrupted: bool) -> None:
        status = "KILLED" if interrupted else "FAILED" if exit_code == 1 else "FINISHED"
        try:
            self.client.set_tag(self.run_id, "trazabilidad", self.trace_state())
        except Exception as exc:
            self._record("etiqueta de trazabilidad", exc)
        try:
            self.client.set_terminated(self.run_id, status=status, end_time=_ms())
            self.status = status
        except Exception as exc:
            self._record("cierre", exc)
            try:
                self.client.set_tag(self.run_id, "trazabilidad", TRACE_INCOMPLETE)
            except Exception:
                pass


def _outcome(log: dict, exit_code: int, interrupted: bool) -> str:
    if interrupted:
        return OUTCOME_INTERRUPTED
    if exit_code == 1:
        return OUTCOME_ERROR
    if exit_code == 2:
        return OUTCOME_QUALITY_BLOCKED
    decision = log.get("aceptacion")
    return OUTCOME_ACCEPTED if decision and decision["aceptada"] else OUTCOME_REJECTED


def _result_tags(log: dict, results: list, exit_code: int, outcome: str) -> dict[str, str]:
    source = log.get("fuente") or {}
    decision = log.get("aceptacion")
    critical = [f"{r.name}: {r.message}" for r in results if r.status == "FAIL"]
    tags = {
        "pipeline.resultado": outcome,
        "pipeline.exit_code": str(exit_code),
        "inicio_utc": log.get("inicio"),
        "fin_utc": log.get("fin"),
        "fuente.tipo": source.get("tipo"),
        "fuente.criterio": source.get("criterio"),
        "fuente.conflicto_con_declaracion": (
            None if "conflicto_con_declaracion" not in source else str(source["conflicto_con_declaracion"]).lower()
        ),
        "fuente.nombre_archivo": source.get("nombre_archivo"),
        "fuente.sha256": source.get("sha256"),
        "fuente.bytes": source.get("bytes"),
        "fuente.filas": source.get("filas"),
        "aceptacion.aceptada": "no_evaluada" if decision is None else str(decision["aceptada"]).lower(),
        "aceptacion.motivos": None if decision is None else ", ".join(decision["motivos_rechazo"]),
        "calidad.estado_global": overall_status(results) if results else None,
        "calidad.advertencias": "; ".join(log.get("advertencias") or []),
        "calidad.errores_criticos": "; ".join(critical),
        "pipeline.error": "; ".join(log.get("errores") or []) if exit_code == 1 else None,
    }
    for result in results:
        tags[f"calidad.{result.id}"] = result.status
    return {k: text for k, v in tags.items() if (text := _text(v)) is not None}


def _result_metrics(log: dict, results: list) -> dict[str, float]:
    """Solo métricas que el pipeline calculó; no se completa ningún valor ausente."""
    source = log.get("metricas") or {}
    out: dict[str, float] = {}
    for key in _METRIC_KEYS:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[key] = float(value)
    estimate, reference = out.get("mae_estimacion"), out.get("mae_referencia")
    if estimate is not None and reference:
        out["razon_mae"] = estimate / reference
    for status in ("PASS", "WARN", "FAIL", "NO EVALUADO"):
        if results:
            out["controles_" + status.lower().replace(" ", "_")] = float(sum(r.status == status for r in results))
    return out
