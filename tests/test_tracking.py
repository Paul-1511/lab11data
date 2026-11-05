"""Pruebas del registro de ejecuciones en MLflow Tracking (SQLite y artefactos temporales)."""

import copy
import json
import sys
from pathlib import Path

import pytest

mlflow = pytest.importorskip("mlflow")
from mlflow.tracking import MlflowClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import inject_fault, make_synthetic  # noqa: E402

from pipeline.config import load_config  # noqa: E402
from pipeline.metadata import find_absolute_paths  # noqa: E402
from pipeline.run import execute  # noqa: E402
from pipeline.tracking import (  # noqa: E402
    ARTIFACT_NAMES, ENV_DIR, ENV_ENABLED, Tracker, load_settings, redact_paths,
)

EXPECTED_METRICS = {"mae_estimacion", "rmse_estimacion", "mae_referencia", "rmse_referencia",
                    "meses_evaluados", "meses_solicitados", "razon_mae"}


def make_cfg(store, runs):
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    cfg["mlflow"]["dir"] = str(store)
    cfg["run"]["output_dir"] = str(runs)
    return cfg


def find_run(cfg, pipeline_run_id):
    settings = load_settings(cfg)
    client = MlflowClient(tracking_uri=settings.tracking_uri)
    experiment = client.get_experiment_by_name(settings.experiment)
    runs = [r for r in client.search_runs([experiment.experiment_id])
            if r.data.tags.get("pipeline.run_id") == pipeline_run_id]
    assert len(runs) == 1
    return client, runs[0]


def artifact_names(client, run):
    return {item.path for item in client.list_artifacts(run.info.run_id)}


@pytest.fixture(scope="module")
def tracking_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(ENV_ENABLED, "1")
        mp.delenv(ENV_DIR, raising=False)
        yield


@pytest.fixture(scope="module")
def good_df():
    return make_synthetic()


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return tmp_path_factory.mktemp("almacen_mlflow")


@pytest.fixture(scope="module")
def runs_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("ejecuciones_mlflow")


def write_csv(tmp_path_factory, df, name="datos.csv"):
    path = tmp_path_factory.mktemp("datos_mlflow") / name
    df.to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def accepted(tracking_env, store, runs_dir, tmp_path_factory, good_df):
    cfg = make_cfg(store, runs_dir)
    cfg["source"]["synthetic_sha256"] = []
    csv = write_csv(tmp_path_factory, good_df)
    return {"cfg": cfg, "csv": csv, "out": execute(cfg, data_path=str(csv), source_kind="sintetica")}


@pytest.fixture(scope="module")
def rejected(tracking_env, store, runs_dir, tmp_path_factory, good_df):
    cfg = make_cfg(store, runs_dir)
    cfg["acceptance"]["min_evaluated_months"] = 99
    csv = write_csv(tmp_path_factory, good_df)
    return {"cfg": cfg, "out": execute(cfg, data_path=str(csv), source_kind="sintetica")}


@pytest.fixture(scope="module")
def blocked(tracking_env, store, runs_dir, tmp_path_factory, good_df):
    cfg = make_cfg(store, runs_dir)
    csv = write_csv(tmp_path_factory, inject_fault(good_df, "zero_total"))
    return {"cfg": cfg, "out": execute(cfg, data_path=str(csv), source_kind="sintetica")}


@pytest.fixture(scope="module")
def errored(tracking_env, store, runs_dir, tmp_path_factory):
    cfg = make_cfg(store, runs_dir)
    missing = tmp_path_factory.mktemp("datos_ausentes") / "no_existe.csv"
    return {"cfg": cfg, "missing": missing, "out": execute(cfg, data_path=str(missing))}


# ---- Estados técnicos y decisión de aceptación -------------------------------------
def test_accepted_run_is_finished_and_accepted(accepted):
    client, run = find_run(accepted["cfg"], accepted["out"]["run_id"])
    assert accepted["out"]["exit_code"] == 0
    assert run.info.status == "FINISHED"
    assert run.data.tags["pipeline.resultado"] == "aceptada"
    assert run.data.tags["aceptacion.aceptada"] == "true"
    assert run.data.tags["trazabilidad"] == "completa"
    assert run.data.tags["calidad.estado_global"] == "PASS"


def test_rejected_run_is_finished_technically_but_rejected_by_the_rule(rejected):
    _, run = find_run(rejected["cfg"], rejected["out"]["run_id"])
    assert rejected["out"]["exit_code"] == 0
    assert run.info.status == "FINISHED"
    assert run.data.tags["pipeline.resultado"] == "rechazada"
    assert run.data.tags["aceptacion.aceptada"] == "false"
    assert "meses_evaluados_suficientes" in run.data.tags["aceptacion.motivos"]
    assert "mae_estimacion" in run.data.metrics


def test_critical_quality_failure_is_recorded(blocked):
    _, run = find_run(blocked["cfg"], blocked["out"]["run_id"])
    assert blocked["out"]["exit_code"] == 2
    assert run.info.status == "FINISHED"
    assert run.data.tags["pipeline.resultado"] == "bloqueada_por_calidad"
    assert run.data.tags["pipeline.exit_code"] == "2"
    assert run.data.tags["calidad.estado_global"] == "FAIL"
    assert run.data.tags["calidad.e_valores_denominadores"] == "FAIL"
    assert run.data.tags["calidad.errores_criticos"]
    assert run.data.metrics["controles_fail"] >= 1.0


def test_unexpected_error_is_recorded_as_failed(errored):
    _, run = find_run(errored["cfg"], errored["out"]["run_id"])
    assert errored["out"]["exit_code"] == 1
    assert run.info.status == "FAILED"
    assert run.data.tags["pipeline.resultado"] == "error_inesperado"
    assert run.data.tags["pipeline.exit_code"] == "1"
    error = run.data.tags["pipeline.error"]
    assert "no_existe.csv" in error
    assert find_absolute_paths({"error": error}) == []
    assert errored["missing"].parent.name not in error


def test_no_metrics_are_recorded_when_they_were_not_computed(blocked, errored):
    for case in (blocked, errored):
        _, run = find_run(case["cfg"], case["out"]["run_id"])
        assert not EXPECTED_METRICS & set(run.data.metrics)
        assert run.data.tags["aceptacion.aceptada"] == "no_evaluada"
        assert "aceptacion.motivos" not in run.data.tags


def test_metrics_match_the_pipeline_metrics(accepted):
    _, run = find_run(accepted["cfg"], accepted["out"]["run_id"])
    computed = accepted["out"]["log"]["metricas"]
    for key in ("mae_estimacion", "rmse_estimacion", "mae_referencia", "rmse_referencia", "meses_evaluados"):
        assert run.data.metrics[key] == pytest.approx(computed[key])
    assert run.data.metrics["razon_mae"] == pytest.approx(computed["mae_estimacion"] / computed["mae_referencia"])


# ---- Parámetros, etiquetas y artefactos ---------------------------------------------
def test_parameters_and_tags_are_recorded(accepted):
    out = accepted["out"]
    _, run = find_run(accepted["cfg"], out["run_id"])
    params, tags = run.data.params, run.data.tags
    assert params["destino"] == "Guatemala"
    assert params["unidad"] == "visitantes por mes"
    assert params["periodo_ajuste_inicio"] == "2023-01" and params["periodo_ajuste_fin"] == "2024-12"
    assert params["periodo_evaluacion_inicio"] == "2025-01" and params["periodo_evaluacion_fin"] == "2025-12"
    assert params["horizonte_inicio"] == "2026-07" and params["horizonte_fin"] == "2026-12"
    assert params["metodo"] == "seasonal_naive" and params["baseline"] == "last_observed"
    assert params["semilla"] == "42" and params["periodo_estacional"] == "12"
    assert params["umbral_min_meses_evaluados"] == "6" and params["umbral_max_razon_mae"] == "1.1"
    assert tags["fuente.tipo"] == "sintetica"
    assert tags["fuente.nombre_archivo"] == "datos.csv"
    assert tags["fuente.sha256"] == out["log"]["fuente"]["sha256"]
    assert tags["fuente.filas"] == str(out["log"]["fuente"]["filas"])
    assert tags["config.sha256"] == out["log"]["config"]["sha256"]
    assert tags["pipeline.version"] == out["log"]["version_procedimiento"]
    assert tags["inicio_utc"] == out["log"]["inicio"] and tags["fin_utc"] == out["log"]["fin"]
    assert all(tags[f"calidad.{r.id}"] == r.status for r in out["results"])
    if out["log"]["commit_git"] != "sin-git":
        assert tags["git.commit"] == out["log"]["commit_git"]


def test_nothing_recorded_contains_an_absolute_path(accepted, blocked, errored):
    for case in (accepted, blocked, errored):
        _, run = find_run(case["cfg"], case["out"]["run_id"])
        assert find_absolute_paths(dict(run.data.tags)) == []
        assert find_absolute_paths(dict(run.data.params)) == []


def test_only_existing_permitted_artifacts_are_recorded(accepted, blocked, errored):
    client, run = find_run(accepted["cfg"], accepted["out"]["run_id"])
    assert artifact_names(client, run) == set(ARTIFACT_NAMES)
    client, run = find_run(blocked["cfg"], blocked["out"]["run_id"])
    # El monitoreo se ejecuta también cuando la compuerta de calidad detiene el pipeline.
    assert artifact_names(client, run) == {
        "quality_report.json", "quality_report.md", "config_snapshot.yaml", "monitoreo.json",
    }
    assert blocked["out"]["log"]["monitoreo"]["estado"] != "error"
    client, run = find_run(errored["cfg"], errored["out"]["run_id"])
    assert artifact_names(client, run) == {"config_snapshot.yaml"}


def test_source_data_and_run_log_are_never_recorded(accepted):
    client, run = find_run(accepted["cfg"], accepted["out"]["run_id"])
    names = artifact_names(client, run)
    assert not names & {"datos.csv", "run_log.json", "serie_mensual_total.csv", "serie_mensual_por_dimension.csv"}


# ---- Relación entre identificadores y persistencia ---------------------------------------
def test_pipeline_and_mlflow_ids_are_related_by_tag_not_equal(accepted):
    out = accepted["out"]
    _, run = find_run(accepted["cfg"], out["run_id"])
    assert run.data.tags["pipeline.run_id"] == out["run_id"]
    assert run.info.run_id != out["run_id"]
    assert out["log"]["mlflow"]["run_id"] == run.info.run_id
    meta = json.loads((Path(out["run_dir"]) / "metadata.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == out["run_id"]
    assert meta["mlflow"] == {
        "run_id": run.info.run_id, "experiment_id": run.info.experiment_id,
        "experimento": "observatorio_turismo_guatemala",
    }


def test_run_log_records_the_tracking_summary(accepted):
    log = json.loads((Path(accepted["out"]["run_dir"]) / "run_log.json").read_text(encoding="utf-8"))
    assert log["mlflow"]["trazabilidad"] == "completa" and log["mlflow"]["estado"] == "FINISHED"
    assert log["mlflow"]["errores"] == [] and find_absolute_paths(log["mlflow"]) == []
    assert any(s["etapa"] == "registro_mlflow" and s["estado"] == "completa" for s in log["etapas"])


def test_experiment_and_runs_persist_and_a_second_run_is_reproducible(accepted, store, tmp_path_factory):
    cfg = accepted["cfg"]
    first = accepted["out"]
    second = execute(cfg, data_path=str(accepted["csv"]), source_kind="sintetica")
    assert (store / "mlflow.db").is_file()

    client = MlflowClient(tracking_uri=load_settings(cfg).tracking_uri)
    experiment = client.get_experiment_by_name("observatorio_turismo_guatemala")
    assert experiment is not None
    _, run_a = find_run(cfg, first["run_id"])
    _, run_b = find_run(cfg, second["run_id"])
    assert run_a.info.experiment_id == run_b.info.experiment_id == experiment.experiment_id
    assert run_a.info.run_id != run_b.info.run_id
    for tag in ("fuente.sha256", "config.sha256", "pipeline.resultado"):
        assert run_a.data.tags[tag] == run_b.data.tags[tag]
    for key in ("mae_estimacion", "rmse_estimacion", "mae_referencia", "rmse_referencia", "meses_evaluados"):
        assert run_a.data.metrics[key] == run_b.data.metrics[key]
    assert run_a.data.params == run_b.data.params


# ---- Fallos de MLflow ----------------------------------------------------------------------
def test_mlflow_unavailable_does_not_change_the_pipeline_and_is_reported(tracking_env, tmp_path, good_df):
    blocker = tmp_path / "no_es_carpeta"
    blocker.write_text("archivo", encoding="utf-8")
    cfg = make_cfg(blocker, tmp_path / "ejecuciones")
    csv = tmp_path / "datos.csv"
    good_df.to_csv(csv, index=False)
    out = execute(cfg, data_path=str(csv), source_kind="sintetica")
    tracking = out["log"]["mlflow"]
    assert out["exit_code"] == 0 and out["log"]["aceptacion"]["aceptada"]
    assert tracking["trazabilidad"] == "no_disponible" and tracking["run_id"] is None
    assert tracking["errores"] and find_absolute_paths(tracking) == []
    assert any(w.startswith("mlflow:") for w in out["log"]["advertencias"])
    meta = json.loads((Path(out["run_dir"]) / "metadata.json").read_text(encoding="utf-8"))
    assert meta["mlflow"] is None


def test_artifact_failure_marks_the_run_as_incomplete(tracking_env, tmp_path, good_df, monkeypatch):
    def broken(self, run_id, local_path, artifact_path=None):
        raise RuntimeError(r"fallo simulado al copiar C:\temporal\archivo.csv")

    monkeypatch.setattr(MlflowClient, "log_artifact", broken)
    cfg = make_cfg(tmp_path / "almacen", tmp_path / "ejecuciones")
    csv = tmp_path / "datos.csv"
    good_df.to_csv(csv, index=False)
    out = execute(cfg, data_path=str(csv), source_kind="sintetica")
    tracking = out["log"]["mlflow"]
    assert out["exit_code"] == 0
    assert tracking["trazabilidad"] == "incompleta" and tracking["estado"] == "FINISHED"
    assert any(e.startswith("artefacto ") for e in tracking["errores"])
    assert find_absolute_paths(tracking) == []
    _, run = find_run(cfg, out["run_id"])
    assert run.data.tags["trazabilidad"] == "incompleta"


def test_interrupted_run_is_closed_as_killed(tracking_env, tmp_path):
    cfg = make_cfg(tmp_path / "almacen", tmp_path / "ejecuciones")
    tracker = Tracker.start(cfg, "ejecucion-interrumpida", version="version-de-prueba", commit=None, tree_modified=None)
    summary = tracker.finalize({}, [], tmp_path, exit_code=0, interrupted=True)
    assert summary["estado"] == "KILLED"
    _, run = find_run(cfg, "ejecucion-interrumpida")
    assert run.info.status == "KILLED" and run.data.tags["pipeline.resultado"] == "interrumpida"


# ---- Desactivación y compatibilidad ----------------------------------------------------------
def test_pipeline_runs_unchanged_when_tracking_is_disabled_by_environment(tmp_path, good_df, monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "0")
    cfg = make_cfg(tmp_path / "almacen", tmp_path / "ejecuciones")
    csv = tmp_path / "datos.csv"
    good_df.to_csv(csv, index=False)
    out = execute(cfg, data_path=str(csv), source_kind="sintetica")
    assert out["exit_code"] == 0 and out["log"]["aceptacion"]["aceptada"]
    assert out["log"]["mlflow"]["trazabilidad"] == "deshabilitada" and out["log"]["mlflow"]["run_id"] is None
    assert not (tmp_path / "almacen").exists()


def test_configuration_flag_disables_tracking_when_the_environment_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_ENABLED, raising=False)
    cfg = make_cfg(tmp_path / "almacen", tmp_path / "ejecuciones")
    assert load_settings(cfg).enabled
    cfg["mlflow"]["enabled"] = False
    assert not load_settings(cfg).enabled
    monkeypatch.setenv(ENV_ENABLED, "1")
    assert load_settings(cfg).enabled
    monkeypatch.setenv(ENV_ENABLED, "false")
    assert not load_settings(cfg).enabled


# ---- Configuración de rutas ------------------------------------------------------------------
def test_default_paths_are_relative_to_the_repository_and_use_sqlite():
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv(ENV_DIR, raising=False)
        settings = load_settings(cfg)
    assert settings.experiment == "observatorio_turismo_guatemala"
    assert settings.db_path == ROOT / "mlflow" / "mlflow.db"
    assert settings.artifact_dir == ROOT / "mlflow" / "artifacts"
    assert settings.tracking_uri.startswith("sqlite:///") and "\\" not in settings.tracking_uri
    assert settings.tracking_uri.endswith("/mlflow/mlflow.db")


def test_environment_variable_moves_the_store_to_a_mounted_volume(tmp_path, monkeypatch):
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    monkeypatch.setenv(ENV_DIR, str(tmp_path / "volumen"))
    settings = load_settings(cfg)
    assert settings.db_path == tmp_path / "volumen" / "mlflow.db"
    assert settings.artifact_dir == tmp_path / "volumen" / "artifacts"


@pytest.mark.parametrize("text, expected", [
    (r"No such file: 'C:\Users\alguien\datos\base.csv'", "No such file: 'base.csv'"),
    ("falta C:/Users/alguien/base.csv ahora", "falta base.csv ahora"),
    ("ruta /home/alguien/base.csv listo", "ruta base.csv listo"),
    ("visitantes/mes en 2026-07 y pipeline/", "visitantes/mes en 2026-07 y pipeline/"),
])
def test_redact_paths_keeps_only_file_names(text, expected):
    assert redact_paths(text) == expected
