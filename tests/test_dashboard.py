"""Pruebas del tablero: lectura de la versión aprobada y vigente (datos sintéticos y carpetas temporales)."""

import ast
import copy
import dataclasses
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import make_synthetic  # noqa: E402

from dashboard import published as PUB  # noqa: E402
from dashboard import theme as T  # noqa: E402
from pipeline import publish as P  # noqa: E402
from pipeline.config import load_config  # noqa: E402
from pipeline.run import execute  # noqa: E402

ID_OLD = "20261102T000001Z-dashaa"
ID_INSTITUTIONAL = "20261102T000002Z-dashii"
ID_CURRENT = "20261102T000003Z-dashbb"
MISSING_ID = "20261102T999999Z-nonexs"

SOURCES = [ROOT / "app.py", *sorted((ROOT / "dashboard").rglob("*.py"))]
ALLOWED_PIPELINE_IMPORTS = {"pipeline.config", "pipeline.contract", "pipeline.metadata", "pipeline.publish"}
FORBIDDEN_IMPORT_ROOTS = {
    "lstm_lab2", "catch22", "pycatch22", "torch", "tensorflow", "keras", "sklearn",
    "openpyxl", "subprocess", "mlflow",
}
FORBIDDEN_TEXT = (
    "lab2ds", "lstm", "catch22", "read_excel", ".xlsx", "subprocess", "os.system",
    "publish_run", "restore_version", "_write_current", "estimaciones_candidatas",
    "download_button", "to_csv",
)


# ---- Fixtures --------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def base_root(tmp_path_factory):
    """Tres versiones reales publicadas: sintética antigua, institucional y sintética vigente."""
    runs = tmp_path_factory.mktemp("corridas_tablero")
    root = tmp_path_factory.mktemp("published_tablero") / "published"
    data = tmp_path_factory.mktemp("origen_tablero") / "datos.csv"
    make_synthetic().to_csv(data, index=False)

    cfg = copy.deepcopy(load_config("config/config.yaml"))
    cfg["run"]["output_dir"] = str(runs)
    cfg["run"]["published_dir"] = str(root)
    cfg["source"]["synthetic_sha256"] = []
    for run_id, kind in ((ID_OLD, "sintetica"), (ID_INSTITUTIONAL, "institucional"), (ID_CURRENT, "sintetica")):
        out = execute(cfg, data_path=str(data), run_id=run_id, source_kind=kind)
        assert out["exit_code"] == 0
        outcome = P.publish_run(cfg, out["run_dir"])
        assert outcome.estado == P.STATE_PUBLISHED, outcome.codes
    assert P.read_current(root) == ID_CURRENT
    return root


@pytest.fixture()
def published(base_root, tmp_path):
    target = tmp_path / "published"
    shutil.copytree(base_root, target)
    return target


def set_current(root, version_id):
    (root / "CURRENT").write_text(version_id + "\n", encoding="utf-8")


def rewrite_json(version_dir, name, mutate):
    """Modifica un JSON de la versión y actualiza el manifest, como lo haría una alteración consistente."""
    path = version_dir / name
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = version_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archivos"][name] = {"sha256": P.sha256_file(path), "bytes": path.stat().st_size}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def assert_no_local_paths(result, *paths):
    text = " ".join([result.message, *result.reasons])
    for path in paths:
        assert str(path) not in text
    assert "\\" not in text and ":/" not in text


# ---- Lectura de CURRENT y versión aprobada ---------------------------------------------------
def test_reads_current_and_loads_the_approved_version(published):
    result = PUB.load_current(published)
    assert result.ok and result.status == PUB.STATUS_OK
    version = result.version
    assert version.version_id == P.read_current(published) == ID_CURRENT
    assert version.metadata["run_id"] == ID_CURRENT
    assert version.metadata["publicacion"]["version_id"] == ID_CURRENT


def test_approved_version_has_a_favorable_decision_and_published_metadata(published):
    version = PUB.load_current(published).version
    assert version.decision["aceptada"] is True
    assert version.metadata["aceptacion"]["aceptada"] is True
    assert version.metadata["publicacion"]["version_anterior"] == ID_INSTITUTIONAL
    assert PUB.format_stamp(version.published_at).endswith("UTC")
    assert version.destination and version.variable and version.unit


def test_missing_current_is_a_state_without_publication(published):
    (published / "CURRENT").unlink()
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_NONE and result.version is None
    assert result.reasons == [] and "sin_publicacion" in result.codes
    assert_no_local_paths(result, published)


def test_missing_published_folder_is_a_state_without_publication(tmp_path):
    result = PUB.load_current(tmp_path / "no_existe")
    assert result.status == PUB.STATUS_NONE and result.version is None


@pytest.mark.parametrize("content", ["../versions/x", "..\\x", "", "dos palabras", "a/b", "-inicio"])
def test_current_with_an_invalid_identifier_is_an_error(published, content):
    (published / "CURRENT").write_text(content, encoding="utf-8")
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and result.version is None
    assert result.codes == ["current_invalido"]
    assert_no_local_paths(result, published)


def test_current_pointing_to_a_nonexistent_version_is_an_error(published):
    set_current(published, MISSING_ID)
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and result.codes == ["version_inexistente"]
    assert MISSING_ID not in result.message
    assert_no_local_paths(result, published)


def test_corrupt_version_is_rejected_without_exposing_paths(published):
    path = published / "versions" / ID_CURRENT / "estimaciones.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and result.version is None
    assert "integridad_fallida" in result.codes
    assert result.reasons and all(isinstance(r, str) for r in result.reasons)
    assert_no_local_paths(result, published, path)


def test_version_with_a_missing_or_foreign_file_is_rejected(published):
    directory = published / "versions" / ID_CURRENT
    (directory / "quality_report.json").unlink()
    assert "archivo_faltante" in PUB.load_current(published).codes

    other = published / "versions" / ID_OLD
    (other / "estimaciones_candidatas.csv").write_text("x", encoding="utf-8")
    set_current(published, ID_OLD)
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and "archivos_inesperados" in result.codes


def test_version_marked_as_not_approved_is_rejected(published):
    directory = published / "versions" / ID_CURRENT
    rewrite_json(directory, "decision_aceptacion.json", lambda d: d.update(aceptada=False))
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and "no_aprobada" in result.codes


def test_incomplete_metadata_is_rejected_even_with_a_consistent_manifest(published):
    directory = published / "versions" / ID_CURRENT
    rewrite_json(directory, "metadata.json", lambda m: m["metricas"].pop("razon_mae"))
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and result.version is None
    assert result.codes == ["metadatos_incompletos"]


def test_metadata_without_the_publication_block_is_rejected(published):
    rewrite_json(published / "versions" / ID_CURRENT, "metadata.json", lambda m: m.update(publicacion=None))
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and result.codes == ["metadatos_incompletos"]


@pytest.mark.parametrize("mutate", [
    lambda m: m.pop("fuente"),
    lambda m: m["objetivo"].pop("unidad"),
    lambda m: m.update(limitaciones=[]),
])
def test_metadata_that_breaks_the_schema_never_shows_figures(published, mutate):
    rewrite_json(published / "versions" / ID_CURRENT, "metadata.json", mutate)
    result = PUB.load_current(published)
    assert result.status == PUB.STATUS_ERROR and result.version is None
    assert result.reasons
    assert_no_local_paths(result, published)


# ---- Fuente sintética ------------------------------------------------------------------------
def test_synthetic_source_is_identified(published):
    version = PUB.load_current(published).version
    assert version.source_type == "sintetica" and version.is_synthetic and not version.is_institutional
    assert any(item["id"] == "fuente_sintetica" for item in version.metadata["limitaciones"])


def test_institutional_source_is_not_labeled_synthetic(published):
    set_current(published, ID_INSTITUTIONAL)
    version = PUB.load_current(published).version
    assert version.version_id == ID_INSTITUTIONAL
    assert version.is_institutional and not version.is_synthetic


# ---- Observados y estimados ------------------------------------------------------------------
def test_observed_and_estimated_rows_are_loaded_from_the_published_csv(published):
    version = PUB.load_current(published).version
    raw = pd.read_csv(published / "versions" / ID_CURRENT / "estimaciones.csv")
    frame = version.estimates
    assert list(frame.columns) == ["periodo", "tipo", "valor"]
    assert set(frame["tipo"]) == {"observado", "estimado"}
    assert len(frame) == len(raw)
    assert frame["valor"].sum() == pytest.approx(raw["valor"].sum())
    assert pd.api.types.is_datetime64_any_dtype(frame["periodo"]) and frame["valor"].notna().all()
    assert version.observed["periodo"].max() < version.estimated["periodo"].min()

    horizon = version.metadata["periodos"]["horizonte"]
    assert version.estimated["periodo"].min() >= pd.Timestamp(horizon["inicio"])
    assert version.estimated["periodo"].max() <= pd.Timestamp(horizon["fin"])

    summary = version.summary()
    assert summary["meses_observados"] == len(version.observed)
    assert summary["meses_estimados"] == len(version.estimated)
    assert summary["ultimo_observado"] == version.observed["periodo"].max()
    assert summary["ultimo_valor"] == pytest.approx(version.observed["valor"].iloc[-1])


# ---- Solo CURRENT, solo lectura, sin pipeline ------------------------------------------------
def test_only_the_version_pointed_by_current_is_consulted(published, monkeypatch):
    consulted = []
    original = PUB.verify_directory

    def spy(directory, version_id, **kwargs):
        consulted.append(Path(directory).name)
        return original(directory, version_id, **kwargs)

    monkeypatch.setattr(PUB, "verify_directory", spy)
    broken = published / "versions" / ID_OLD / "estimaciones.csv"
    broken.write_text("alterado", encoding="utf-8")

    assert PUB.load_current(published).ok
    assert consulted == [ID_CURRENT]

    set_current(published, ID_OLD)
    assert PUB.load_current(published).status == PUB.STATUS_ERROR


def test_loading_does_not_write_anything_or_run_the_pipeline(published, monkeypatch):
    import pipeline.run as run_module

    def forbidden(*args, **kwargs):
        raise AssertionError("el tablero no debe ejecutar el pipeline")

    monkeypatch.setattr(run_module, "execute", forbidden)
    before = snapshot(published)
    names_before = {p.name for p in published.iterdir()}
    assert PUB.load_current(published).ok
    assert snapshot(published) == before
    assert {p.name for p in published.iterdir()} == names_before


def test_published_root_follows_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv(P.ENV_DIR, str(tmp_path / "otra"))
    assert PUB.published_root() == tmp_path / "otra"


# ---- Independencia de LSTM, Excel y pipeline -------------------------------------------------
def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_dashboard_sources_exist():
    names = {p.name for p in SOURCES}
    assert {"app.py", "published.py", "charts.py", "ui.py", "theme.py", "inicio.py", "estimaciones.py", "evaluacion.py"} <= names


def test_dashboard_has_no_lstm_excel_or_model_dependencies():
    for path in SOURCES:
        roots = {name.split(".")[0] for name in _imports(path)}
        assert not roots & FORBIDDEN_IMPORT_ROOTS, path.name
        text = path.read_text(encoding="utf-8").lower()
        for token in ("lab2ds", "lstm", "catch22", "read_excel", ".xlsx"):
            assert token not in text, f"{path.name} contiene {token}"


def test_dashboard_only_imports_read_only_pipeline_modules():
    for path in SOURCES:
        used = {name for name in _imports(path) if name == "pipeline" or name.startswith("pipeline.")}
        modules = {name for name in used if name.count(".") == 1 and name != "pipeline"}
        assert modules <= ALLOWED_PIPELINE_IMPORTS, (path.name, modules)
        text = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_TEXT:
            assert token not in text, f"{path.name} contiene {token}"


def test_loader_and_charts_do_not_depend_on_streamlit():
    for name in ("published.py", "charts.py", "theme.py"):
        assert "streamlit" not in _imports(ROOT / "dashboard" / name)


# ---- Figuras ---------------------------------------------------------------------------------
def test_estimates_figure_distinguishes_observed_from_estimated(published):
    pytest.importorskip("plotly")
    from dashboard import charts

    version = PUB.load_current(published).version
    frame = version.estimates
    fig = charts.estimates_figure(version, frame["periodo"].min(), frame["periodo"].max())
    names = [trace.name for trace in fig.data]
    assert names == ["Observado · sintético", "Estimado · sintético"]
    assert list(fig.data[0].y) == pytest.approx(list(version.observed["valor"]))
    assert list(fig.data[1].y) == pytest.approx(list(version.estimated["valor"]))
    assert all(version.unit in text for text in fig.data[1].hovertext)

    narrow = charts.estimates_figure(version, version.observed["periodo"].iloc[-6], version.observed["periodo"].iloc[-1])
    assert len(narrow.data[1].y) == 0 and not narrow.layout.shapes


def test_estimates_figure_does_not_label_institutional_as_synthetic(published):
    pytest.importorskip("plotly")
    from dashboard import charts

    set_current(published, ID_INSTITUTIONAL)
    version = PUB.load_current(published).version
    fig = charts.estimates_figure(version, version.estimates["periodo"].min(), version.estimates["periodo"].max())
    assert [t.name for t in fig.data] == ["Observado", "Estimado"]


def test_metrics_figure_uses_only_published_numbers(published):
    pytest.importorskip("plotly")
    from dashboard import charts

    version = PUB.load_current(published).version
    m = version.metadata["metricas"]
    fig = charts.metrics_figure(version)
    assert list(fig.data[0].y) == pytest.approx([m["mae_estimacion"], m["rmse_estimacion"]])
    assert list(fig.data[1].y) == pytest.approx([m["mae_referencia"], m["rmse_referencia"]])

    meta = copy.deepcopy(version.metadata)
    meta["metricas"]["mae_referencia"] = None
    assert charts.metrics_figure(dataclasses.replace(version, metadata=meta)) is None


# ---- Páginas Streamlit (se omiten si Streamlit no está instalado) ----------------------------
def _page_script(page):
    return f"from dashboard.views import {page}\n{page}.render()\n"


def _run_page(page, root, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv(P.ENV_DIR, str(root))
    return AppTest.from_string(_page_script(page), default_timeout=60).run()


@pytest.mark.parametrize("page", ["inicio", "estimaciones", "evaluacion"])
def test_pages_without_publication_show_a_message_and_no_figures(page, tmp_path, monkeypatch):
    at = _run_page(page, tmp_path / "vacio", monkeypatch)
    assert not at.exception
    assert any("Aún no hay resultados aprobados" in item.value for item in at.markdown)
    assert len(at.metric) == 0 and len(at.table) == 0 and len(at.select_slider) == 0


@pytest.mark.parametrize("page", ["inicio", "estimaciones", "evaluacion"])
def test_pages_with_a_corrupt_version_show_an_error_and_no_figures(page, published, monkeypatch):
    path = published / "versions" / ID_CURRENT / "metadata.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    at = _run_page(page, published, monkeypatch)
    assert not at.exception
    assert len(at.error) == 1 and str(published) not in at.error[0].value
    assert len(at.metric) == 0 and len(at.table) == 0


@pytest.mark.parametrize("page", ["inicio", "estimaciones", "evaluacion"])
def test_pages_with_an_approved_synthetic_version_warn_about_the_source(page, published, monkeypatch):
    at = _run_page(page, published, monkeypatch)
    assert not at.exception
    assert any("Datos sintéticos" in item.value for item in at.markdown)


def test_home_shows_the_published_values_only(published, monkeypatch):
    version = PUB.load_current(published).version
    at = _run_page("inicio", published, monkeypatch)
    assert not at.exception
    labels = {metric.label: metric.value for metric in at.metric}
    assert labels["Último período observado"] == T.month_label(version.summary()["ultimo_observado"])
    assert labels["Fuente"] == "Sintética"
    assert any(version.version_id in item.value for item in at.markdown)


def test_changing_the_period_filter_does_not_run_the_pipeline(published, monkeypatch):
    import pipeline.run as run_module

    at = _run_page("estimaciones", published, monkeypatch)
    assert not at.exception and len(at.select_slider) == 1

    def forbidden(*args, **kwargs):
        raise AssertionError("el tablero no debe ejecutar el pipeline")

    monkeypatch.setattr(run_module, "execute", forbidden)
    options = at.select_slider[0].options
    at.select_slider[0].set_value((options[0], options[-1])).run()
    assert not at.exception
