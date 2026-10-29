"""Punto de entrada del pipeline.

    python -m pipeline.run --config config/config.yaml [--data RUTA_DATOS]

Códigos de salida: 0 = completado (puede tener advertencias),
2 = control crítico fallido (publicación bloqueada), 1 = error inesperado.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import __version__
from .checks import (
    FAIL, SPECS, check_columns_types, check_dates_coverage, check_methodology,
    check_missing, check_uniqueness, check_values_denominators, skipped,
)
from .config import ROOT, load_config, resolve
from .dates import build_dates
from .io import apply_filters, fingerprint, read_source
from .prepare import prepare, save_prepared
from .report import overall_status, write_quality_report

# Etapas que se implementan después del avance. Se registran como pendientes:
# no se inventan métricas ni se publica nada hasta que existan.
PENDING_STAGES = ["ajuste_modelo", "evaluacion", "estimaciones", "registro_mlflow", "publicacion"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "sin-git"


class RunLog:
    """Registro por ejecución: horas, etapas, advertencias, errores y publicación."""

    def __init__(self, run_id: str, cfg: dict):
        self.data = {
            "run_id": run_id,
            "version_procedimiento": __version__,
            "commit_git": _git_commit(),
            "config": cfg["_meta"],
            "semilla": cfg["run"]["seed"],
            "inicio": _now(),
            "fin": None,
            "etapas": [],
            "advertencias": [],
            "errores": [],
            "publicacion": "no ejecutada",
        }

    @contextmanager
    def stage(self, name: str):
        entry = {"etapa": name, "inicio": _now(), "estado": "completada"}
        self.data["etapas"].append(entry)
        try:
            yield entry
        except Exception as exc:
            entry["estado"] = "error"
            self.data["errores"].append(f"{name}: {exc}")
            raise
        finally:
            entry["fin"] = _now()

    def skip(self, name: str, reason: str):
        self.data["etapas"].append({"etapa": name, "estado": reason})


def execute(cfg: dict, data_path: str | None = None, run_id: str | None = None) -> dict:
    run_id = run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    run_dir = resolve(cfg["run"]["output_dir"]) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(cfg["_meta"]["path"], run_dir / "config_snapshot.yaml")

    log = RunLog(run_id, cfg)
    results, exit_code = [], 0
    try:
        # 1) Lectura ---------------------------------------------------------
        with log.stage("lectura"):
            src = resolve(data_path or cfg["source"]["path"])
            log.data["fuente"] = fingerprint(src)
            df = read_source(src, cfg)
            log.data["fuente"]["filas"] = len(df)

        # 2) Controles a–e ---------------------------------------------------
        dates, prepared = None, None
        with log.stage("controles_a_e"):
            a = check_columns_types(df, cfg)
            results.append(a)
            if a.status == FAIL:
                for cid in list(SPECS)[1:]:
                    results.append(skipped(cid, "no evaluable: falló el control a) de columnas y tipos"))
            else:
                df, population = apply_filters(df, cfg)       # población de referencia
                log.data["poblacion"] = population
                for col, vals in population["valores_no_encontrados"].items():
                    log.data["advertencias"].append(f"filtro: valores inexistentes en {col}: {vals}")
                dates = build_dates(df, cfg)
                results += [
                    check_dates_coverage(df, cfg, dates),
                    check_uniqueness(df, cfg, dates),
                    check_missing(df, cfg),
                    check_values_denominators(df, cfg, dates),
                ]

        # 3) Preparación (solo si nada crítico falló) -------------------------
        if dates is not None and not any(r.status == FAIL for r in results):
            with log.stage("preparacion"):
                prepared = prepare(df, dates, cfg)
                log.data["salidas"] = save_prepared(prepared, run_dir)
        else:
            log.skip("preparacion", "omitida por control crítico fallido")

        # 4) Control f -------------------------------------------------------
        with log.stage("control_f"):
            if dates is None:
                results.append(skipped("f_regla_metodologica", "no evaluable: falló el control a)"))
            else:
                results.append(check_methodology(df, cfg, dates, prepared))

        # 5) Compuerta de calidad -------------------------------------------
        with log.stage("compuerta_calidad") as st:
            report = write_quality_report(results, run_dir, run_id)
            log.data["advertencias"] += [f"{r.name}: {r.message}" for r in results if r.status == "WARN"]
            if overall_status(results) == FAIL:
                st["estado"] = "detenida"
                exit_code = 2
                failed = [r.name for r in results if r.status == FAIL]
                log.data["errores"].append(f"controles críticos fallidos: {failed}")
                log.data["publicacion"] = "bloqueada por control crítico; resultados publicados sin cambios"

        # 6) Etapas posteriores al avance ---------------------------------------
        for name in PENDING_STAGES:
            reason = "omitida por compuerta de calidad" if exit_code == 2 else "pendiente (fuera del avance)"
            log.skip(name, reason)
        if exit_code == 0:
            log.data["publicacion"] = "pendiente de implementar; resultados publicados sin cambios"
    except Exception as exc:  # error inesperado: no se toca lo publicado
        exit_code = 1
        if not log.data["errores"]:
            log.data["errores"].append(str(exc))
        log.data["publicacion"] = "no ejecutada por error; resultados publicados sin cambios"
    finally:
        log.data["fin"] = _now()
        log.data["codigo_salida"] = exit_code
        (run_dir / "run_log.json").write_text(
            json.dumps(log.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    return {"run_id": run_id, "run_dir": str(run_dir), "exit_code": exit_code,
            "results": results, "log": log.data}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pipeline del Observatorio de Turismo (Lab 11)")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--data", default=None, help="ruta de la fuente (reemplaza source.path)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    out = execute(cfg, data_path=args.data)

    print(f"Ejecución {out['run_id']}  →  {out['run_dir']}")
    for r in out["results"]:
        print(f"  [{r.status:^11}] {r.name}: {r.message}")
    for e in out["log"]["errores"]:
        print(f"  ERROR: {e}", file=sys.stderr)
    print(f"Publicación: {out['log']['publicacion']}")
    return out["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
