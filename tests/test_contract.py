"""Pruebas del contrato de estimaciones, la integración con la aceptación y los metadatos."""

import copy
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import make_synthetic  # noqa: E402

from pipeline.config import load_config  # noqa: E402
from pipeline.contract import (  # noqa: E402
    REQUIRED_COLUMNS, ContractViolation, validate_estimates, validate_estimates_file,
)
from pipeline.dates import month_range  # noqa: E402
from pipeline.evaluation import acceptance_decision, candidate_results  # noqa: E402
from pipeline.io import fingerprint  # noqa: E402
from pipeline.metadata import (  # noqa: E402
    MetadataError, classify_source, find_absolute_paths, validate_metadata, write_metadata,
)
from pipeline.run import execute  # noqa: E402

RUN_ID = "run-de-prueba"
VERSION = "version-de-prueba"
GOOD_METRICS = {"meses_evaluados": 12, "mae_estimacion": 1.0, "mae_referencia": 2.0}


@pytest.fixture()
def cfg(tmp_path):
    c = copy.deepcopy(load_config("config/config.yaml"))
    c["run"]["output_dir"] = str(tmp_path / "runs")
    return c


def build_total(cfg):
    idx = month_range(cfg["periods"]["expected_start"], cfg["periods"]["expected_end"])
    return pd.DataFrame({
        "fecha": idx,
        "valor": np.linspace(1000.0, 2000.0, len(idx)),
        "cobertura": True,
        "unidad": cfg["target"]["unit"],
    })


@pytest.fixture()
def candidates(cfg):
    return candidate_results(build_total(cfg), cfg, RUN_ID, VERSION)


def check(df, cfg, **kw):
    return validate_estimates(df, cfg, run_id=RUN_ID, version=VERSION, **kw)


def error(result, code):
    return next(e for e in result.errors if e.code == code)


def horizon_end(cfg):
    return pd.Timestamp(cfg["periods"]["forecast"]["end"])


# ---- Contrato -----------------------------------------------------------------
def test_valid_candidates_pass(candidates, cfg):
    result = check(candidates, cfg)
    assert result.valid, result.codes
    assert result.rows == len(candidates)


def test_valid_candidates_pass_after_csv_round_trip(candidates, cfg, tmp_path):
    path = tmp_path / "estimaciones.csv"
    candidates.to_csv(path, index=False, date_format="%Y-%m-%d")
    assert validate_estimates_file(path, cfg, run_id=RUN_ID, version=VERSION).valid


def test_columns_follow_the_documented_contract(candidates):
    assert list(candidates.columns) == list(REQUIRED_COLUMNS)


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_missing_required_column_is_reported(candidates, cfg, column):
    result = check(candidates.drop(columns=[column]), cfg)
    assert result.codes == ["columna_faltante"]
    assert error(result, "columna_faltante").detail["columnas"] == [column]


def test_duplicate_period_is_reported(candidates, cfg):
    result = check(pd.concat([candidates, candidates.iloc[[0]]], ignore_index=True), cfg)
    assert "periodo_duplicado" in result.codes


def test_repeated_estimated_month_is_reported(candidates, cfg):
    first_estimated = candidates[candidates["tipo"] == "estimado"].iloc[[0]]
    result = check(pd.concat([candidates, first_estimated], ignore_index=True), cfg)
    assert "estimado_repetido" in result.codes


def test_incomplete_horizon_is_reported(candidates, cfg):
    last = horizon_end(cfg)
    result = check(candidates[~((candidates["tipo"] == "estimado") & (candidates["periodo"] == last))], cfg)
    assert error(result, "horizonte_incompleto").detail["faltantes"] == [last.strftime("%Y-%m")]


def test_estimated_month_outside_horizon_is_reported(candidates, cfg):
    extra = candidates[candidates["tipo"] == "estimado"].iloc[[0]].copy()
    extra["periodo"] = horizon_end(cfg) + pd.DateOffset(months=1)
    result = check(pd.concat([candidates, extra], ignore_index=True), cfg)
    assert "estimado_fuera_de_horizonte" in result.codes


def test_negative_value_is_reported(candidates, cfg):
    candidates.loc[0, "valor"] = -1.0
    assert "valor_negativo" in check(candidates, cfg).codes


@pytest.mark.parametrize("bad", [np.inf, -np.inf])
def test_non_finite_value_is_reported(candidates, cfg, bad):
    candidates.loc[0, "valor"] = bad
    assert "valor_no_finito" in check(candidates, cfg).codes


def test_missing_value_is_reported_as_empty_field(candidates, cfg):
    candidates.loc[0, "valor"] = np.nan
    result = check(candidates, cfg)
    assert error(result, "campo_vacio").column == "valor"


def test_non_numeric_value_is_reported(candidates, cfg):
    candidates["valor"] = candidates["valor"].astype(object)
    candidates.loc[0, "valor"] = "abc"
    assert "valor_no_numerico" in check(candidates, cfg).codes


def test_blank_text_field_is_reported_as_empty(candidates, cfg):
    candidates.loc[0, "destino"] = "   "
    assert error(check(candidates, cfg), "campo_vacio").column == "destino"


def test_invalid_date_is_reported(candidates, cfg):
    candidates["periodo"] = candidates["periodo"].dt.strftime("%Y-%m-%d")
    candidates.loc[0, "periodo"] = "no-es-fecha"
    assert "fecha_invalida" in check(candidates, cfg).codes


def test_period_that_is_not_month_start_is_reported(candidates, cfg):
    candidates.loc[0, "periodo"] = pd.Timestamp("2010-01-15")
    assert "periodo_no_mensual" in check(candidates, cfg).codes


def test_inconsistent_run_id_is_reported(candidates, cfg):
    candidates.loc[0, "run_id"] = "otra-ejecucion"
    assert "run_id_inconsistente" in check(candidates, cfg).codes


def test_run_id_must_match_the_expected_one(candidates, cfg):
    result = validate_estimates(candidates, cfg, run_id="otra-ejecucion", version=VERSION)
    assert error(result, "run_id_inconsistente").detail["esperado"] == "otra-ejecucion"


def test_inconsistent_unit_is_reported(candidates, cfg):
    candidates["unidad"] = "personas"
    assert "unidad_inconsistente" in check(candidates, cfg).codes


def test_inconsistent_destination_is_reported(candidates, cfg):
    candidates["destino"] = "Otro destino"
    assert "destino_inconsistente" in check(candidates, cfg).codes


def test_inconsistent_version_is_reported(candidates, cfg):
    candidates.loc[0, "version_procedimiento"] = "otra-version"
    assert "version_inconsistente" in check(candidates, cfg).codes


def test_generation_date_must_be_single_and_valid(candidates, cfg):
    different = candidates.copy()
    different.loc[0, "fecha_generacion"] = "2020-01-01T00:00:00+00:00"
    assert "fecha_generacion_inconsistente" in check(different, cfg).codes
    candidates["fecha_generacion"] = "no-es-fecha"
    assert "fecha_generacion_invalida" in check(candidates, cfg).codes


def test_type_and_method_must_be_allowed(candidates, cfg):
    candidates.loc[0, "tipo"] = "proyectado"
    candidates["metodo"] = "modelo_desconocido"
    codes = check(candidates, cfg).codes
    assert "tipo_no_permitido" in codes and "metodo_no_permitido" in codes


def test_observed_and_estimated_must_be_separated_in_time(candidates, cfg):
    candidates.loc[candidates.index[0], "periodo"] = horizon_end(cfg)
    assert "observados_y_estimados_mezclados" in check(candidates, cfg).codes


def test_empty_and_non_table_inputs_are_reported(candidates, cfg):
    assert check(candidates.iloc[0:0], cfg).codes == ["sin_filas"]
    assert check(None, cfg).codes == ["tipo_invalido"]


def test_validator_reports_without_correcting(candidates, cfg):
    candidates.loc[0, "valor"] = -5.0
    before = candidates.copy(deep=True)
    check(candidates, cfg)
    pd.testing.assert_frame_equal(candidates, before)


def test_result_is_structured_and_can_raise(candidates, cfg):
    candidates["unidad"] = "personas"
    result = check(candidates, cfg)
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["valido"] is False and payload["errores"][0]["codigo"]
    with pytest.raises(ContractViolation):
        result.raise_if_invalid()


# ---- Aceptación ---------------------------------------------------------------
def test_acceptance_accepts_a_valid_contract(candidates, cfg):
    decision = acceptance_decision("PASS", GOOD_METRICS, candidates, cfg, run_id=RUN_ID, version=VERSION)
    assert decision["aceptada"] and decision["contrato"]["valido"]


def test_acceptance_keeps_the_previous_call_signature(candidates, cfg):
    assert acceptance_decision("PASS", GOOD_METRICS, candidates, cfg)["aceptada"]


def test_acceptance_rejects_candidates_that_break_the_contract(candidates, cfg):
    broken = candidates[candidates["periodo"] != horizon_end(cfg)]
    decision = acceptance_decision("PASS", GOOD_METRICS, broken, cfg, run_id=RUN_ID, version=VERSION)
    assert not decision["aceptada"]
    assert decision["motivos_rechazo"] == ["salidas_completas"]
    assert "horizonte_incompleto" in [e["codigo"] for e in decision["contrato"]["errores"]]


def test_acceptance_rejects_a_run_id_that_does_not_match(candidates, cfg):
    decision = acceptance_decision("PASS", GOOD_METRICS, candidates, cfg, run_id="otra-ejecucion", version=VERSION)
    assert not decision["aceptada"]


def test_acceptance_thresholds_and_metrics_are_unchanged(candidates, cfg):
    decision = acceptance_decision("PASS", GOOD_METRICS, candidates, cfg)
    assert decision["regla"] == {"min_evaluated_months": 6, "max_mae_ratio_to_baseline": 1.1}
    assert decision["metricas"] == GOOD_METRICS
    worse = {**GOOD_METRICS, "mae_estimacion": 2.3, "mae_referencia": 2.0}
    assert acceptance_decision("PASS", worse, candidates, cfg)["motivos_rechazo"] == ["mae_dentro_del_umbral"]


# ---- Fuente -------------------------------------------------------------------
def test_known_synthetic_hash_wins_over_an_institutional_declaration(cfg):
    cfg["source"]["synthetic_sha256"] = ["ABC123"]
    out = classify_source(cfg, "abc123", declared="institucional")
    assert out["tipo"] == "sintetica" and out["conflicto_con_declaracion"]
    assert out["criterio"] == "huella_sintetica_registrada"


def test_config_declaration_applies_only_to_the_configured_path(cfg):
    assert classify_source(cfg, "0" * 64, path_overridden=False)["tipo"] == "institucional"
    assert classify_source(cfg, "0" * 64, path_overridden=True)["tipo"] == "no_declarada"


def test_explicit_declaration_is_used_and_validated(cfg):
    assert classify_source(cfg, "0" * 64, declared="sintetica", path_overridden=True)["tipo"] == "sintetica"
    with pytest.raises(ValueError):
        classify_source(cfg, "0" * 64, declared="oficial")


# ---- Ejecuciones con datos sintéticos construidos en las pruebas ------------------
@pytest.fixture(scope="module")
def synthetic_csv(tmp_path_factory):
    path = tmp_path_factory.mktemp("origen_prueba") / "datos.csv"
    make_synthetic().to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def declared_run(tmp_path_factory, synthetic_csv):
    c = copy.deepcopy(load_config("config/config.yaml"))
    c["source"]["synthetic_sha256"] = []
    c["run"]["output_dir"] = str(tmp_path_factory.mktemp("corridas_prueba"))
    out = execute(c, data_path=str(synthetic_csv), source_kind="sintetica")
    meta = json.loads((Path(out["run_dir"]) / "metadata.json").read_text(encoding="utf-8"))
    return out, meta


def test_pipeline_remains_compatible_and_writes_metadata(declared_run):
    out, _ = declared_run
    run_dir = Path(out["run_dir"])
    assert out["exit_code"] == 0 and out["log"]["aceptacion"]["aceptada"]
    for name in ("quality_report.json", "quality_report.md", "run_log.json", "serie_mensual_total.csv",
                 "config_snapshot.yaml", "evaluacion_temporal.csv", "estimaciones_candidatas.csv",
                 "decision_aceptacion.json", "metadata.json"):
        assert (run_dir / name).exists()
    assert {"path", "sha256", "bytes", "filas"}.issubset(out["log"]["fuente"])
    assert out["log"]["aceptacion"]["contrato"]["valido"]


def test_candidates_written_by_the_pipeline_meet_the_contract(declared_run, cfg):
    out, _ = declared_run
    path = Path(out["run_dir"]) / "estimaciones_candidatas.csv"
    assert validate_estimates_file(path, cfg, run_id=out["run_id"], version=out["log"]["version_procedimiento"]).valid


def test_metadata_is_valid_and_consistent_with_the_run(declared_run):
    out, meta = declared_run
    assert validate_metadata(meta).valid
    assert meta["esquema_version"] == "1.0" and meta["run_id"] == out["run_id"]
    assert meta["version_procedimiento"] == out["log"]["version_procedimiento"]
    assert meta["configuracion"]["sha256"] == out["log"]["config"]["sha256"]
    assert meta["fuente"]["sha256"] == out["log"]["fuente"]["sha256"]
    assert meta["aceptacion"]["aceptada"] == out["log"]["aceptacion"]["aceptada"]
    assert meta["metricas"]["mae_estimacion"] == out["log"]["metricas"]["mae_estimacion"]
    assert meta["metricas"]["meses_evaluados"] == out["log"]["metricas"]["meses_evaluados"]
    assert [c["id"] for c in meta["calidad"]["controles"]] == [r.id for r in out["results"]]
    assert meta["calidad"]["estado_global"] == "PASS"
    assert meta["procedimiento"]["baseline"] == "last_observed"


def test_metadata_leaves_later_stages_empty(declared_run):
    _, meta = declared_run
    assert meta["mlflow"] is None and meta["publicacion"] is None


def test_metadata_hashes_the_candidates_file(declared_run):
    out, meta = declared_run
    path = Path(out["run_dir"]) / "estimaciones_candidatas.csv"
    assert meta["integridad"]["estimaciones_candidatas_sha256"] == fingerprint(path)["sha256"]


def test_metadata_has_no_absolute_paths(declared_run, synthetic_csv):
    out, meta = declared_run
    raw = (Path(out["run_dir"]) / "metadata.json").read_text(encoding="utf-8")
    assert find_absolute_paths(meta) == []
    assert synthetic_csv.parent.name not in raw and Path(out["run_dir"]).parent.name not in raw
    assert meta["fuente"]["nombre_archivo"] == "datos.csv"


def test_declared_synthetic_source_is_identified_and_limited(declared_run):
    _, meta = declared_run
    assert meta["fuente"]["tipo"] == "sintetica"
    assert meta["fuente"]["criterio"] == "declaracion_de_la_ejecucion"
    ids = [item["id"] for item in meta["limitaciones"]]
    assert "fuente_sintetica" in ids and "indicador_nacional_sin_reparto" in ids
    assert "tramo_homogeneo_desde_2023" not in ids
    assert all(item["evidencia"] for item in meta["limitaciones"])


def test_undeclared_source_is_not_classified_by_its_file_name(tmp_path, synthetic_csv, cfg):
    named = tmp_path / "base_sintetica.csv"
    shutil.copy(synthetic_csv, named)
    cfg["source"]["synthetic_sha256"] = []
    out = execute(cfg, data_path=str(named))
    assert out["log"]["fuente"]["tipo"] == "no_declarada"
    meta = json.loads((Path(out["run_dir"]) / "metadata.json").read_text(encoding="utf-8"))
    assert meta["fuente"]["tipo"] == "no_declarada"
    assert "fuente_no_declarada" in [item["id"] for item in meta["limitaciones"]]


def test_registered_hash_identifies_a_synthetic_source(synthetic_csv, cfg):
    cfg["source"]["synthetic_sha256"] = [fingerprint(synthetic_csv)["sha256"]]
    out = execute(cfg, data_path=str(synthetic_csv))
    assert out["log"]["fuente"]["tipo"] == "sintetica"
    assert out["log"]["fuente"]["criterio"] == "huella_sintetica_registrada"


# ---- Metadatos: validación ------------------------------------------------------
@pytest.mark.parametrize("path", [
    r"C:\Users\alguien\datos.csv", "C:/Users/alguien/datos.csv", r"\\servidor\recurso\datos.csv",
    "/home/alguien/datos.csv", "ruta usada: /var/datos/archivo.csv",
])
def test_absolute_paths_are_detected(path):
    assert find_absolute_paths({"a": [path]}) == ["$.a[0]"]


@pytest.mark.parametrize("text", [
    "2026-07", "visitantes por mes", "pipeline/", "docs/ALCANCE.md sección 2.2", "2026-11-02T04:29:14+00:00",
])
def test_ordinary_text_is_not_flagged_as_a_path(text):
    assert find_absolute_paths({"a": text}) == []


def test_metadata_with_an_absolute_path_is_rejected_and_not_written(declared_run, tmp_path):
    _, meta = declared_run
    bad = copy.deepcopy(meta)
    bad["fuente"]["nombre_archivo"] = r"C:\datos\base.csv"
    assert "ruta_absoluta" in validate_metadata(bad).codes
    target = tmp_path / "metadata.json"
    with pytest.raises(MetadataError):
        write_metadata(bad, target)
    assert not target.exists()


def test_metadata_missing_keys_and_invalid_source_type_are_rejected(declared_run):
    _, meta = declared_run
    missing = {k: v for k, v in meta.items() if k != "aceptacion"}
    assert validate_metadata(missing).codes == ["clave_faltante"]
    bad = copy.deepcopy(meta)
    bad["fuente"]["tipo"] = "oficial"
    assert "tipo_de_fuente_invalido" in validate_metadata(bad).codes


# ---- Factor de reparto --------------------------------------------------------
def test_allocation_factor_is_not_read_by_the_pipeline():
    sources = [p for p in (ROOT / "pipeline").glob("*.py")]
    assert sources and not any("allocation_factor" in p.read_text(encoding="utf-8") for p in sources)
