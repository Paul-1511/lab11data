"""Pruebas de los seis controles de calidad y de la compuerta del pipeline."""

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import FAULTS, inject_fault, make_synthetic  # noqa: E402

from pipeline.config import load_config  # noqa: E402
from pipeline.dates import build_dates  # noqa: E402
from pipeline.io import apply_filters  # noqa: E402
from pipeline.prepare import prepare  # noqa: E402
from pipeline.run import execute  # noqa: E402
from pipeline import checks as C  # noqa: E402


@pytest.fixture(scope="module")
def good():
    return make_synthetic()


@pytest.fixture()
def cfg(tmp_path):
    c = load_config("config/config.yaml")
    c = copy.deepcopy(c)
    c["run"]["output_dir"] = str(tmp_path / "runs")
    return c


def run_all(df, cfg):
    a = C.check_columns_types(df, cfg)
    df, _ = apply_filters(df, cfg)                       # población de referencia, como en run.py
    d = build_dates(df, cfg)
    p = prepare(df, d, cfg)
    return {
        "a": a,
        "b": C.check_dates_coverage(df, cfg, d),
        "c": C.check_uniqueness(df, cfg, d),
        "d": C.check_missing(df, cfg),
        "e": C.check_values_denominators(df, cfg, d),
        "f": C.check_methodology(df, cfg, d, p),
    }


def test_clean_data_passes_everything(good, cfg):
    assert {k: r.status for k, r in run_all(good, cfg).items()} == {k: "PASS" for k in "abcdef"}


def test_a_missing_required_column_fails(good, cfg):
    r = C.check_columns_types(good.drop(columns=["Viajero"]), cfg)
    assert r.status == "FAIL" and "Viajero" in r.details["columnas_requeridas_faltantes"]


def test_a_incompatible_type_fails(good, cfg):
    bad = inject_fault(good, "daily_dates_in_value_text")
    assert C.check_columns_types(bad, cfg).status == "FAIL"


def test_a_missing_optional_column_warns(good, cfg):
    assert C.check_columns_types(good.drop(columns=["Vía"]), cfg).status == "WARN"


def test_b_invalid_dates_fail_over_threshold(good, cfg):
    bad = apply_filters(inject_fault(good, "invalid_date"), cfg)[0]
    r = C.check_dates_coverage(bad, cfg, build_dates(bad, cfg))
    expected = int(good.head(1000)["Tipo de Viajero"].isin(["Turista", "Excursionista"]).sum())
    assert r.status == "FAIL" and r.details["fechas_invalidas"] == expected > 0


def test_b_missing_required_month_fails_and_is_not_zero_filled(good, cfg):
    bad = apply_filters(inject_fault(good, "missing_month"), cfg)[0]
    d = build_dates(bad, cfg)
    assert C.check_dates_coverage(bad, cfg, d).status == "FAIL"
    total = prepare(bad, d, cfg)["total"].set_index("fecha")
    assert np.isnan(total.loc["2024-05-01", "valor"]) and not total.loc["2024-05-01", "cobertura"]


def test_b_missing_month_outside_required_ranges_warns(good, cfg):
    bad = good[~((good["Año"] == 2026) & (good["Mes cod"] == 2))]
    bad = apply_filters(bad, cfg)[0]
    assert C.check_dates_coverage(bad, cfg, build_dates(bad, cfg)).status == "WARN"


def test_b_allowed_gap_does_not_alert(good, cfg):
    cfg["quality"]["coverage"]["allowed_missing_months"] = ["2026-02"]
    bad = good[~((good["Año"] == 2026) & (good["Mes cod"] == 2))]
    bad = apply_filters(bad, cfg)[0]
    assert C.check_dates_coverage(bad, cfg, build_dates(bad, cfg)).status == "PASS"


def test_c_duplicates_fail(good, cfg):
    bad = apply_filters(inject_fault(good, "duplicate_key"), cfg)[0]
    r = C.check_uniqueness(bad, cfg, build_dates(bad, cfg))
    expected = 2 * int(good.head(5)["Tipo de Viajero"].isin(["Turista", "Excursionista"]).sum())
    assert r.status == "FAIL" and r.details["filas_en_claves_duplicadas"] == expected > 0


def test_d_missing_value_blocks(good, cfg):
    bad = good.copy()
    bad.loc[bad.index[3], "Viajero"] = np.nan
    assert C.check_missing(bad, cfg).status == "FAIL"


def test_d_allowed_missing_warns_then_fails_over_threshold(good, cfg):
    few = good.copy()
    few.loc[few.index[:10], "Agrupación Residencia"] = "0"      # centinela = sin dato
    assert C.check_missing(few, cfg).status == "WARN"
    many = good.copy()
    many.loc[many.index[: len(many) // 5], "Agrupación Residencia"] = np.nan
    assert C.check_missing(many, cfg).status == "FAIL"


@pytest.mark.parametrize("fault", ["negative_value", "zero_total"])
def test_e_negative_and_zero_denominator_fail(good, cfg, fault):
    bad = apply_filters(inject_fault(good, fault), cfg)[0]
    assert C.check_values_denominators(bad, cfg, build_dates(bad, cfg)).status == "FAIL"


def test_e_infinite_fails(good, cfg):
    bad = good.copy()
    bad.loc[bad.index[0], "Viajero"] = np.inf
    assert C.check_values_denominators(bad, cfg, build_dates(bad, cfg)).status == "FAIL"


def test_f_daily_source_declared_fails(good, cfg):
    cfg["source"]["granularity"] = "daily"
    r = run_all(good, cfg)["f"]
    assert r.status == "FAIL"


def test_f_daily_dates_detected_fails(cfg):
    df = pd.DataFrame({"Fecha": pd.date_range("2009-01-01", "2026-06-30", freq="D"), "Viajero": 1.0})
    cfg["columns"]["date"] = {"mode": "column", "column": "Fecha"}
    cfg["columns"]["required"] = {"Viajero": "numeric"}
    d = build_dates(df, cfg)
    assert C.check_methodology(df, cfg, d, None).status == "FAIL"


def test_f_zero_fill_is_detected(good, cfg):
    good = apply_filters(good, cfg)[0]
    d = build_dates(good, cfg)
    miss = apply_filters(inject_fault(good, "missing_month"), cfg)[0]
    p = prepare(miss, build_dates(miss, cfg), cfg)
    p["total"]["valor"] = p["total"]["valor"].fillna(0.0)          # relleno indebido
    assert C.check_methodology(good, cfg, d, p).status == "FAIL"


# ---- Compuerta del pipeline -------------------------------------------------
def _write(df, tmp_path):
    path = tmp_path / "datos.csv"
    df.to_csv(path, index=False)
    return str(path)


def test_pipeline_ok_exit_0_and_report_written(good, cfg, tmp_path):
    out = execute(cfg, data_path=_write(good, tmp_path))
    run_dir = Path(out["run_dir"])
    assert out["exit_code"] == 0
    for f in ("quality_report.json", "quality_report.md", "run_log.json",
              "serie_mensual_total.csv", "config_snapshot.yaml"):
        assert (run_dir / f).exists()


@pytest.mark.parametrize("fault", [f for f in FAULTS])
def test_pipeline_stops_on_every_fault_without_publishing(good, cfg, tmp_path, fault):
    published = Path(cfg["run"]["published_dir"])
    before = sorted(p.name for p in published.glob("**/*")) if published.exists() else []
    out = execute(cfg, data_path=_write(inject_fault(good, fault), tmp_path))
    assert out["exit_code"] == 2
    assert "bloqueada" in out["log"]["publicacion"]
    # No se preparan ni se publican salidas cuando falla un control crítico.
    assert not (Path(out["run_dir"]) / "serie_mensual_total.csv").exists()
    after = sorted(p.name for p in published.glob("**/*")) if published.exists() else []
    assert before == after


def test_pipeline_missing_source_is_error_not_publication(cfg, tmp_path):
    out = execute(cfg, data_path=str(tmp_path / "no_existe.csv"))
    assert out["exit_code"] == 1 and out["log"]["errores"]


# ---- Población de referencia y granularidad real ------------------------------
def test_population_filter_keeps_only_visitors(good, cfg):
    pop, info = apply_filters(good, cfg)
    assert set(pop["Tipo de Viajero"]) == {"Turista", "Excursionista"}
    assert info["filas_poblacion"] < info["filas_fuente"] and not info["valores_no_encontrados"]


def test_filter_matters_zero_visitor_total_is_detected_despite_non_visitor_rows(good, cfg):
    """Con 'Viajero' (no visitante) sin filtrar, el total de marzo 2025 no sería cero."""
    bad = inject_fault(good, "zero_total")
    unfiltered = bad[(bad["Año"] == 2025) & (bad["Mes cod"] == 3)]["Viajero"].sum()
    assert unfiltered > 0
    pop = apply_filters(bad, cfg)[0]
    assert C.check_values_denominators(pop, cfg, build_dates(pop, cfg)).status == "FAIL"


def test_filter_reports_values_not_found(good, cfg):
    cfg["filters"] = [{"column": "Tipo de Viajero", "values": ["Turista", "Inexistente"]}]
    assert apply_filters(good, cfg)[1]["valores_no_encontrados"] == {"Tipo de Viajero": ["Inexistente"]}


def test_missing_filter_column_fails_control_a(good, cfg):
    r = C.check_columns_types(good.drop(columns=["Tipo de Viajero"]), cfg)
    assert r.status == "FAIL" and "Tipo de Viajero" in r.details["columnas_requeridas_faltantes"]


def test_key_must_include_residence_group(good, cfg):
    """Sin la agrupación de residencia, la clave marcaría falsos duplicados (como en la fuente real)."""
    pop = apply_filters(good, cfg)[0]
    d = build_dates(pop, cfg)
    dup = pop.copy()
    dup.loc[dup.index[0], "Agrupación Residencia"] = "Otra agrupación"
    extra = dup.iloc[[0]].copy()
    extra["Agrupación Residencia"] = "Resto del Mundo"
    both = pd.concat([pop, extra], ignore_index=True)
    d2 = build_dates(both, cfg)
    assert C.check_uniqueness(both, cfg, d2).status == "PASS"
    cfg["quality"]["uniqueness"]["key"] = [k for k in cfg["quality"]["uniqueness"]["key"] if k != "Agrupación Residencia"]
    assert C.check_uniqueness(both, cfg, d2).status == "FAIL"
