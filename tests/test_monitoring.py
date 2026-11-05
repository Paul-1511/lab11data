"""Pruebas de monitoreo, alertas y recuperación ante fallos (datos sintéticos y carpetas temporales)."""

import copy
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import inject_fault, make_synthetic  # noqa: E402

from dashboard import published as PUB  # noqa: E402
from pipeline import monitoring as M  # noqa: E402
from pipeline import publish as P  # noqa: E402
from pipeline.config import load_config  # noqa: E402
from pipeline.run import execute  # noqa: E402

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
VERSION = "20261101T000001Z-aaaaaa"


# ---- Ayudas ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def good():
    return make_synthetic()


@pytest.fixture()
def cfg():
    return copy.deepcopy(load_config("config/config.yaml"))


@pytest.fixture()
def th(cfg):
    return M.thresholds(cfg)


def months(start, end):
    return [m.strftime("%Y-%m") for m in pd.date_range(start, end, freq="MS")]


def frame(observed=None, estimated=None):
    rows = [{"periodo": pd.Timestamp(p), "tipo": "observado", "valor": v} for p, v in (observed or {}).items()]
    rows += [{"periodo": pd.Timestamp(p), "tipo": "estimado", "valor": v} for p, v in (estimated or {}).items()]
    return pd.DataFrame(rows, columns=["periodo", "tipo", "valor"])


def complete(obs_end="2026-06", observed_value=100_000.0, estimated_value=100_000.0):
    return frame(
        {m: observed_value for m in months("2025-01", obs_end)},
        {m: estimated_value for m in months("2026-07", "2026-12")},
    )


def result(check_id, status, **details):
    return SimpleNamespace(id=check_id, status=status, details=details)


def baseline(observed=None, quality=None, last="2026-06", published_at=None, source_type="sintetica"):
    observed = {pd.Timestamp(m) for m in (observed or months("2025-01", last))}
    return M.Baseline(
        version=VERSION, published_at=published_at or NOW.isoformat(timespec="seconds"), source_type=source_type,
        observed=observed, last_observed=max(observed), last_period=pd.Timestamp("2026-12-01"), quality=quality or {},
    )


def by_code(alerts):
    return {a.codigo: a for a in alerts}


def freshness(cfg, th, candidates=None, results=(), base=None):
    return by_code(M.check_freshness(cfg, list(results), candidates, base, th, NOW))


def make_cfg(tmp_path):
    out = copy.deepcopy(load_config("config/config.yaml"))
    out["run"]["output_dir"] = str(tmp_path / "runs")
    out["run"]["published_dir"] = str(tmp_path / "published")
    out["source"]["synthetic_sha256"] = []
    return out


def write(df, path):
    df.to_csv(path, index=False)
    return str(path)


def codes_of(out):
    return {a["codigo"]: a for a in out["log"]["alertas"]}


def snapshot(root):
    root = Path(root)
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture()
def publishing(monkeypatch):
    monkeypatch.setenv(P.ENV_ENABLED, "1")
    monkeypatch.delenv(P.ENV_DIR, raising=False)


# ---- Actualización -----------------------------------------------------------------------------
def test_up_to_date_source_and_complete_horizon_raise_no_freshness_alerts(cfg, th):
    assert freshness(cfg, th, complete()) == {}


def test_outdated_source_is_reported_with_the_months_behind(cfg, th):
    alerts = freshness(cfg, th, complete(obs_end="2026-04"))
    alert = alerts["fuente_desactualizada"]
    assert alert.severidad == M.SEVERITY_WARNING
    assert alert.detalle["meses_de_atraso"] == 2
    assert alert.detalle["ultimo_periodo_observado"] == "2026-04"
    assert "horizonte_incompleto" not in alerts


def test_incomplete_horizon_is_distinct_from_an_outdated_source(cfg, th):
    candidates = complete()
    candidates = candidates[~candidates["periodo"].isin(pd.to_datetime(["2026-10-01", "2026-12-01"]))]
    alerts = freshness(cfg, th, candidates)
    alert = alerts["horizonte_incompleto"]
    assert alert.severidad == M.SEVERITY_CRITICAL
    assert alert.detalle["disponibles"] == 4 and alert.detalle["meses_faltantes"] == ["2026-10", "2026-12"]
    assert "fuente_desactualizada" not in alerts


def test_a_non_finite_estimate_does_not_count_as_horizon_coverage(cfg, th):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-09-01"), "valor"] = np.nan
    assert freshness(cfg, th, candidates)["horizonte_incompleto"].detalle["meses_faltantes"] == ["2026-09"]


def test_missing_required_months_are_critical_and_are_never_filled_with_zero(cfg, th):
    b = result(
        "b_fechas_cobertura", "FAIL",
        meses_sin_datos_requeridos=["2025-03", "2025-04"], meses_sin_datos_no_requeridos=["2015-03", "2026-05", "2026-06"],
        huecos_permitidos_configurados=[],
    )
    alerts = freshness(cfg, th, None, [b])
    assert alerts["meses_requeridos_ausentes"].severidad == M.SEVERITY_CRITICAL
    assert alerts["meses_requeridos_ausentes"].detalle["meses"] == ["2025-03", "2025-04"]
    assert alerts["huecos_en_la_serie"].detalle["meses"] == ["2015-03"]      # los meses finales los explica la fuente
    assert alerts["fuente_desactualizada"].detalle["meses_de_atraso"] == 2


def test_published_version_age_and_staleness_are_reported(cfg, th):
    old = (NOW - timedelta(days=100)).isoformat(timespec="seconds")
    alerts = freshness(cfg, th, complete(), base=baseline(last="2026-03", published_at=old))
    assert alerts["publicacion_antigua"].detalle["dias"] == 100
    assert alerts["publicacion_desactualizada"].detalle["meses_de_atraso"] == 3
    assert "candidato_con_menos_datos" not in alerts


def test_a_recent_up_to_date_published_version_raises_no_alerts(cfg, th):
    assert freshness(cfg, th, complete(), base=baseline()) == {}


def test_candidate_with_less_recent_data_than_the_published_version_is_reported(cfg, th):
    alerts = freshness(cfg, th, complete(obs_end="2026-04"), base=baseline())
    assert alerts["candidato_con_menos_datos"].detalle == {
        "ultimo_observado_ejecucion": "2026-04", "ultimo_observado_vigente": "2026-06",
    }


# ---- Calidad -----------------------------------------------------------------------------------
def quality(cfg, th, results, candidates=None, base=None):
    return by_code(M.check_quality(cfg, results, candidates, base, th))


def missing_result(fraction, status="WARN"):
    return result("d_faltantes", status, que_impiden_estimar={"Viajero": {"faltantes": 0, "fraccion": 0.0}},
                  permitidos={"Región dos": {"faltantes": 3, "fraccion": fraction}})


def test_increase_in_missing_values_is_reported_against_the_published_version(cfg, th):
    base = baseline(quality={"d_faltantes": {"permitidos": {"Región dos": {"fraccion": 0.0}}}})
    alerts = quality(cfg, th, [missing_result(0.03)], base=base)
    assert alerts["faltantes_aumentaron"].detalle["aumento"] == pytest.approx(0.03)
    assert alerts["faltantes_aumentaron"].severidad == M.SEVERITY_WARNING
    assert alerts["faltantes_altos"].detalle["columna"] == "Región dos"


def test_missing_values_without_a_baseline_only_use_the_absolute_threshold(cfg, th):
    assert set(quality(cfg, th, [missing_result(0.03)])) == {"faltantes_altos"}


def test_small_missing_values_are_not_alerted(cfg, th):
    base = baseline(quality={"d_faltantes": {"permitidos": {"Región dos": {"fraccion": 0.0}}}})
    assert quality(cfg, th, [missing_result(0.001, "PASS")], base=base) == {}


def test_missing_values_above_the_blocking_limit_are_critical(cfg, th):
    alerts = quality(cfg, th, [missing_result(0.06, "FAIL")])
    assert alerts["faltantes_altos"].severidad == M.SEVERITY_CRITICAL


def test_increase_in_duplicates_is_reported_and_is_critical_when_the_control_fails(cfg, th):
    failing = result("c_unicidad", "FAIL", filas_en_claves_duplicadas=5, fraccion=0.001)
    base = baseline(quality={"c_unicidad": {"fraccion": 0.0}})
    alert = quality(cfg, th, [failing], base=base)["duplicados_aumentaron"]
    assert alert.severidad == M.SEVERITY_CRITICAL and alert.detalle["fraccion_vigente"] == 0.0
    assert quality(cfg, th, [result("c_unicidad", "WARN", filas_en_claves_duplicadas=5, fraccion=0.001)], base=base)[
        "duplicados_aumentaron"].severidad == M.SEVERITY_WARNING


def test_duplicates_that_did_not_grow_or_do_not_exist_are_not_alerted(cfg, th):
    same = quality(cfg, th, [result("c_unicidad", "WARN", filas_en_claves_duplicadas=5, fraccion=0.001)],
                   base=baseline(quality={"c_unicidad": {"fraccion": 0.001}}))
    clean = quality(cfg, th, [result("c_unicidad", "PASS", filas_en_claves_duplicadas=0, fraccion=0.0)])
    assert same == {} and clean == {}


def test_duplicates_without_a_baseline_are_compared_with_zero(cfg, th):
    alert = quality(cfg, th, [result("c_unicidad", "FAIL", filas_en_claves_duplicadas=5, fraccion=0.001)])
    assert alert["duplicados_aumentaron"].detalle["fraccion_vigente"] is None


def test_loss_of_temporal_coverage_is_reported(cfg, th):
    base = baseline(observed=months("2025-01", "2026-06"))
    candidates = complete()
    candidates = candidates[candidates["periodo"] != pd.Timestamp("2025-05-01")]
    alert = quality(cfg, th, [], candidates, base)["perdida_de_cobertura"]
    assert alert.detalle["meses"] == ["2025-05"] and alert.detalle["total"] == 1
    assert quality(cfg, th, [], complete(), base) == {}


def test_skipped_controls_do_not_produce_quality_alerts(cfg, th):
    skipped = [result("c_unicidad", "NO EVALUADO"), result("d_faltantes", "NO EVALUADO")]
    assert quality(cfg, th, skipped) == {}


# ---- Resultados --------------------------------------------------------------------------------
def outputs(cfg, th, candidates):
    return by_code(M.check_results(cfg, candidates, th))


def test_regular_estimates_raise_no_result_alerts(cfg, th):
    assert outputs(cfg, th, complete()) == {}


def test_negative_estimates_are_critical(cfg, th):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-08-01"), "valor"] = -5.0
    alerts = outputs(cfg, th, candidates)
    assert alerts["estimaciones_negativas"].severidad == M.SEVERITY_CRITICAL
    assert alerts["estimaciones_negativas"].detalle["periodos"] == ["2026-08"]
    assert "valores_no_finitos" not in alerts


def test_non_finite_estimates_are_critical_and_not_counted_as_negative(cfg, th):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-08-01"), "valor"] = np.inf
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-09-01"), "valor"] = np.nan
    alerts = outputs(cfg, th, candidates)
    assert alerts["valores_no_finitos"].detalle["total"] == 2
    assert alerts["valores_no_finitos"].severidad == M.SEVERITY_CRITICAL
    assert "estimaciones_negativas" not in alerts


def test_excessive_monthly_change_is_a_review_warning_not_a_precision_claim(cfg, th):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-07-01"), "valor"] = 160_000.0
    alert = outputs(cfg, th, candidates)["variacion_mensual_alta"]
    assert alert.severidad == M.SEVERITY_WARNING
    assert alert.detalle["transiciones"][0]["periodo"] == "2026-07"
    assert alert.detalle["transiciones"][0]["variacion"] == pytest.approx(0.6)
    assert "no demuestra" in alert.mensaje
    assert alert.detalle["maxima_variacion_observada"] is not None


def test_both_directions_of_the_monthly_change_are_detected(cfg, th):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-07-01"), "valor"] = 40_000.0
    assert outputs(cfg, th, candidates)["variacion_mensual_alta"].detalle["transiciones"][0]["variacion"] == pytest.approx(-0.6)


def test_monthly_change_threshold_is_configurable(cfg):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-07-01"), "valor"] = 160_000.0
    cfg["monitoring"]["results"]["max_monthly_change"] = 0.7
    assert outputs(cfg, M.thresholds(cfg), candidates) == {}


def test_change_from_a_zero_month_is_not_computed_and_never_divides_by_zero(cfg, th):
    candidates = complete()
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-06-01"), "valor"] = 0.0
    assert outputs(cfg, th, candidates) == {}


def test_changes_are_not_computed_across_a_gap_of_months(cfg, th):
    candidates = complete(obs_end="2026-04")
    candidates.loc[candidates["tipo"] == "estimado", "valor"] = 900_000.0
    assert "variacion_mensual_alta" not in outputs(cfg, th, candidates)


def test_without_candidates_there_are_no_result_alerts(cfg, th):
    assert M.check_results(cfg, None, th) == []


# ---- Umbrales y robustez -----------------------------------------------------------------------
def test_default_thresholds_come_from_the_committed_configuration(cfg):
    assert M.thresholds(cfg) == M.DEFAULTS


def test_invalid_thresholds_are_rejected(cfg):
    cfg["monitoring"]["results"]["max_monthly_change"] = -0.1
    with pytest.raises(ValueError):
        M.thresholds(cfg)
    cfg["monitoring"]["results"]["max_monthly_change"] = "mucho"
    with pytest.raises(ValueError):
        M.thresholds(cfg)


def test_monitor_run_never_raises(cfg):
    cfg["monitoring"]["results"]["max_monthly_change"] = -1
    report = M.monitor_run(cfg, [], complete())
    assert report["estado"] == M.STATE_ERROR and report["alertas"] == []
    broken = M.monitor_run(copy.deepcopy(load_config("config/config.yaml")), None, pd.DataFrame())
    assert broken["estado"] == M.STATE_ERROR and "mensaje" in broken


def test_monitor_run_reports_summary_and_json_serializable_alerts(cfg):
    candidates = complete(obs_end="2026-04")
    candidates.loc[candidates["periodo"] == pd.Timestamp("2026-08-01"), "valor"] = -1.0
    report = M.monitor_run(cfg, [], candidates, now=NOW)
    assert report["estado"] == M.STATE_ALERTS
    assert report["resumen"] == {"criticas": 1, "advertencias": 1}
    json.dumps(report)


def test_baseline_of_a_different_source_type_is_not_compared(publishing, good, tmp_path):
    cfg = make_cfg(tmp_path)
    first = execute(cfg, data_path=write(good, tmp_path / "bueno.csv"), source_kind="sintetica", run_id="20261103T000001Z-src001")
    assert first["log"]["version_publicada"] == first["run_id"]
    same = M.monitor_run(cfg, [], complete(), source_type="sintetica", now=NOW)
    other = M.monitor_run(cfg, [], complete(), source_type="institucional", now=NOW)
    assert same["linea_base"] == {"estado": "disponible", "version": first["run_id"]}
    assert other["linea_base"] == {"estado": "tipo_de_fuente_distinto", "version": None}


def test_invalid_published_version_is_not_used_as_baseline(publishing, good, tmp_path):
    cfg = make_cfg(tmp_path)
    first = execute(cfg, data_path=write(good, tmp_path / "bueno.csv"), source_kind="sintetica", run_id="20261103T000001Z-inv001")
    target = Path(cfg["run"]["published_dir"]) / "versions" / first["run_id"] / "estimaciones.csv"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert M.monitor_run(cfg, [], complete(), now=NOW)["linea_base"]["estado"] == "version_vigente_no_valida"


# ---- Ejecuciones completas ---------------------------------------------------------------------
def test_run_log_records_times_stages_publication_and_monitoring(good, tmp_path):
    cfg = make_cfg(tmp_path)
    out = execute(cfg, data_path=write(good, tmp_path / "datos.csv"), source_kind="sintetica")
    log = out["log"]
    assert out["exit_code"] == 0
    assert log["inicio"] and log["fin"] and log["duracion_segundos"] >= 0
    assert {"lectura", "controles_a_e", "regla_aceptacion", "monitoreo"} <= set(log["etapas_completadas"])
    assert log["resultado_publicacion"] == "deshabilitada" and log["version_publicada"] is None
    assert log["monitoreo"]["linea_base"]["estado"] == "publicacion_deshabilitada"
    assert isinstance(log["alertas"], list) and isinstance(log["advertencias"], list) and isinstance(log["errores"], list)
    saved = json.loads((Path(out["run_dir"]) / "run_log.json").read_text(encoding="utf-8"))
    assert saved["alertas"] == log["alertas"] and (Path(out["run_dir"]) / "monitoreo.json").is_file()
    codes = codes_of(out)
    assert not {"fuente_desactualizada", "horizonte_incompleto", "estimaciones_negativas"} & set(codes)
    assert log["monitoreo"]["resumen"]["criticas"] == 0


def test_outdated_source_run_reports_it_and_keeps_the_horizon_alert_separate(tmp_path):
    cfg = make_cfg(tmp_path)
    out = execute(cfg, data_path=write(make_synthetic(end="2026-04"), tmp_path / "atrasada.csv"), source_kind="sintetica")
    codes = codes_of(out)
    assert codes["fuente_desactualizada"]["detalle"]["meses_de_atraso"] == 2
    assert codes["fuente_desactualizada"]["severidad"] == "advertencia"
    assert "horizonte_incompleto" not in codes


def test_monitoring_can_be_disabled_without_changing_the_decision(good, tmp_path, monkeypatch):
    data = write(good, tmp_path / "datos.csv")
    enabled = execute(make_cfg(tmp_path / "a"), data_path=data, source_kind="sintetica")
    monkeypatch.setenv(M.ENV_ENABLED, "0")
    disabled = execute(make_cfg(tmp_path / "b"), data_path=data, source_kind="sintetica")
    assert disabled["log"]["monitoreo"] is None and disabled["log"]["alertas"] == []
    assert not (Path(disabled["run_dir"]) / "monitoreo.json").exists()
    assert any(s["etapa"] == "monitoreo" and s["estado"] == "deshabilitado" for s in disabled["log"]["etapas"])
    assert enabled["exit_code"] == disabled["exit_code"] == 0
    assert enabled["log"]["aceptacion"]["aceptada"] == disabled["log"]["aceptacion"]["aceptada"]
    assert enabled["log"]["metricas"] == disabled["log"]["metricas"]


def test_increase_in_missing_values_is_detected_against_the_published_version(publishing, good, tmp_path):
    cfg = make_cfg(tmp_path)
    first = execute(cfg, data_path=write(good, tmp_path / "bueno.csv"), source_kind="sintetica", run_id="20261103T000001Z-mon001")
    assert first["log"]["publicacion_detalle"]["estado"] == "publicada"
    gaps = good.copy()
    gaps["Región dos"] = gaps["Región dos"].astype(object)
    gaps.loc[gaps.index % 200 < 7, "Región dos"] = None
    second = execute(cfg, data_path=write(gaps, tmp_path / "faltantes.csv"), source_kind="sintetica", run_id="20261103T000002Z-mon002")
    codes = codes_of(second)
    assert second["log"]["monitoreo"]["linea_base"] == {"estado": "disponible", "version": first["run_id"]}
    assert "faltantes_aumentaron" in codes and "faltantes_altos" in codes


def test_loss_of_coverage_is_detected_against_the_published_version(publishing, good, tmp_path):
    cfg = make_cfg(tmp_path)
    execute(cfg, data_path=write(good, tmp_path / "bueno.csv"), source_kind="sintetica", run_id="20261103T000001Z-cov001")
    gap = good[~((good["Año"] == 2015) & (good["Mes cod"] == 3))]
    second = execute(cfg, data_path=write(gap, tmp_path / "hueco.csv"), source_kind="sintetica", run_id="20261103T000002Z-cov002")
    codes = codes_of(second)
    assert codes["perdida_de_cobertura"]["detalle"]["meses"] == ["2015-03"]
    assert codes["huecos_en_la_serie"]["detalle"]["meses"] == ["2015-03"]


# ---- Fallo controlado y recuperación -----------------------------------------------------------
def test_controlled_failure_keeps_current_and_the_dashboard_then_recovers_and_restores(publishing, good, tmp_path):
    cfg = make_cfg(tmp_path)
    root = Path(cfg["run"]["published_dir"])
    good_path = write(good, tmp_path / "bueno.csv")

    # 1) Publicar una versión válida.
    first = execute(cfg, data_path=good_path, source_kind="sintetica", run_id="20261103T000001Z-fal001")
    assert first["exit_code"] == 0 and first["log"]["version_publicada"] == first["run_id"]
    assert P.read_current(root) == first["run_id"]
    before = snapshot(root)

    # 2-4) Falla controlada (claves duplicadas), ejecución y detección.
    faulty = write(inject_fault(good, "duplicate_key"), tmp_path / "duplicados.csv")
    second = execute(cfg, data_path=faulty, source_kind="sintetica", run_id="20261103T000002Z-fal002")
    assert second["exit_code"] == 2
    alert = codes_of(second)["duplicados_aumentaron"]
    assert alert["severidad"] == "critica" and alert["categoria"] == "calidad"
    assert second["log"]["monitoreo"]["linea_base"]["version"] == first["run_id"]
    assert second["log"]["version_publicada"] is None and second["log"]["resultado_publicacion"] == "no_ejecutada"

    # 5) CURRENT y todo lo publicado no cambian.
    assert P.read_current(root) == first["run_id"]
    assert snapshot(root) == before

    # 6) El tablero conserva la versión anterior.
    shown = PUB.load_current(root)
    assert shown.ok and shown.version.version_id == first["run_id"]

    # 7-9) Corregir la falla, ejecutar de nuevo y publicar la nueva versión.
    third = execute(cfg, data_path=good_path, source_kind="sintetica", run_id="20261103T000003Z-fal003")
    assert third["exit_code"] == 0 and "duplicados_aumentaron" not in codes_of(third)
    assert third["log"]["publicacion_detalle"]["estado"] == "publicada"
    assert third["log"]["publicacion_detalle"]["version_anterior"] == first["run_id"]
    assert P.read_current(root) == third["run_id"]
    assert PUB.load_current(root).version.version_id == third["run_id"]

    # 10) Restaurar la versión anterior aprobada.
    restored = P.restore_version(cfg, first["run_id"], reason="prueba de recuperación")
    assert restored.estado == P.STATE_RESTORED
    assert P.read_current(root) == first["run_id"]
    assert PUB.load_current(root).version.version_id == first["run_id"]
    assert {p.name for p in (root / "versions").iterdir()} == {first["run_id"], third["run_id"]}


@pytest.mark.parametrize("fault", ["zero_total", "negative_value", "missing_month"])
def test_other_critical_faults_never_change_current(publishing, good, tmp_path, fault):
    cfg = make_cfg(tmp_path)
    root = Path(cfg["run"]["published_dir"])
    first = execute(cfg, data_path=write(good, tmp_path / "bueno.csv"), source_kind="sintetica", run_id="20261103T000001Z-fb0001")
    before = snapshot(root)
    out = execute(cfg, data_path=write(inject_fault(good, fault), tmp_path / "mala.csv"), source_kind="sintetica",
                  run_id="20261103T000002Z-fb0002")
    assert out["exit_code"] == 2 and out["log"]["monitoreo"]["estado"] != M.STATE_ERROR
    assert P.read_current(root) == first["run_id"] and snapshot(root) == before


def test_missing_required_month_fault_is_reported_as_a_critical_freshness_alert(publishing, good, tmp_path):
    cfg = make_cfg(tmp_path)
    out = execute(cfg, data_path=write(inject_fault(good, "missing_month"), tmp_path / "mala.csv"), source_kind="sintetica")
    alert = codes_of(out)["meses_requeridos_ausentes"]
    assert out["exit_code"] == 2 and alert["severidad"] == "critica" and alert["detalle"]["meses"] == ["2024-05"]


# ---- MLflow ------------------------------------------------------------------------------------
def test_monitoring_is_recorded_in_mlflow(good, tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    from pipeline.tracking import ENV_ENABLED as MLFLOW_ENABLED
    from pipeline.tracking import load_settings as mlflow_settings

    monkeypatch.setenv(MLFLOW_ENABLED, "1")
    cfg = make_cfg(tmp_path)
    cfg["mlflow"]["dir"] = str(tmp_path / "mlflow")
    out = execute(cfg, data_path=write(make_synthetic(end="2026-04"), tmp_path / "atrasada.csv"), source_kind="sintetica")
    settings = mlflow_settings(cfg)
    client = MlflowClient(tracking_uri=settings.tracking_uri)
    experiment = client.get_experiment_by_name(settings.experiment)
    run = next(r for r in client.search_runs([experiment.experiment_id])
               if r.data.tags.get("pipeline.run_id") == out["run_id"])
    monitoring = out["log"]["monitoreo"]
    assert run.data.tags["monitoreo.estado"] == monitoring["estado"] == M.STATE_ALERTS
    assert "fuente_desactualizada" in run.data.tags["monitoreo.alertas"]
    assert run.data.metrics["monitoreo.alertas_criticas"] == monitoring["resumen"]["criticas"]
    assert run.data.metrics["monitoreo.alertas_advertencia"] == monitoring["resumen"]["advertencias"]
    assert "monitoreo.json" in {item.path for item in client.list_artifacts(run.info.run_id)}
