"""Pruebas de publicación controlada, versionado y restauración (datos sintéticos y carpetas temporales)."""

import copy
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import make_synthetic  # noqa: E402

from pipeline import publish as P  # noqa: E402
from pipeline.config import load_config  # noqa: E402
from pipeline.restore import main as restore_main  # noqa: E402
from pipeline.run import execute  # noqa: E402

IDS = {
    "a": "20261101T000001Z-aaaaaa",
    "b": "20261101T000002Z-bbbbbb",
    "c": "20261101T000003Z-cccccc",
    "d": "20261101T000004Z-dddddd",
    "u": "20261101T000005Z-uuuuuu",
    "r": "20261101T000006Z-rrrrrr",
}


def make_cfg(runs, published):
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    cfg["run"]["output_dir"] = str(runs)
    cfg["run"]["published_dir"] = str(published)
    cfg["source"]["synthetic_sha256"] = []
    return cfg


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def good_csv(tmp_path_factory):
    path = tmp_path_factory.mktemp("origen_publicacion") / "datos.csv"
    make_synthetic().to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def runs_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("corridas_publicacion")


@pytest.fixture(scope="module")
def runs(runs_dir, good_csv, tmp_path_factory):
    """Ejecuciones reales sobre datos sintéticos, con la publicación del pipeline desactivada."""
    cfg = make_cfg(runs_dir, tmp_path_factory.mktemp("sin_uso"))
    rejected_cfg = copy.deepcopy(cfg)
    rejected_cfg["acceptance"]["min_evaluated_months"] = 99
    data = str(good_csv)
    out = {
        "a": execute(cfg, data_path=data, run_id=IDS["a"], source_kind="sintetica"),
        "b": execute(cfg, data_path=data, run_id=IDS["b"], source_kind="sintetica"),
        "c": execute(cfg, data_path=data, run_id=IDS["c"], source_kind="sintetica"),
        "d": execute(cfg, data_path=data, run_id=IDS["d"], source_kind="institucional"),
        "u": execute(cfg, data_path=data, run_id=IDS["u"]),
        "r": execute(rejected_cfg, data_path=data, run_id=IDS["r"], source_kind="sintetica"),
    }
    return {name: Path(result["run_dir"]) for name, result in out.items()}


@pytest.fixture()
def cfg(runs_dir, tmp_path):
    return make_cfg(runs_dir, tmp_path / "published")


def root_of(cfg):
    return Path(cfg["run"]["published_dir"])


def boom(source, target):
    raise OSError("fallo simulado al reemplazar el puntero")


# ---- Publicación ---------------------------------------------------------------------------
def test_first_approved_publication(cfg, runs):
    outcome = P.publish_run(cfg, runs["a"])
    root = root_of(cfg)
    version = root / "versions" / IDS["a"]
    assert outcome.estado == "publicada" and outcome.version_anterior is None
    assert sorted(p.name for p in version.iterdir()) == sorted(
        ["estimaciones.csv", "metadata.json", "decision_aceptacion.json", "quality_report.json", "manifest.json"]
    )
    assert P.read_current(root) == IDS["a"]
    assert (version / "estimaciones.csv").read_bytes() == (runs["a"] / "estimaciones_candidatas.csv").read_bytes()
    assert P.verify_directory(version, IDS["a"]) == []
    meta = read_json(version / "metadata.json")
    assert meta["publicacion"] == {
        "version_id": IDS["a"], "fecha_publicacion": outcome.fecha, "version_anterior": None,
    }
    events = P.read_events(root)
    assert [e["evento"] for e in events] == ["publicacion"] and events[0]["anterior"] is None
    assert not list(root.glob("CURRENT.tmp-*")) and not list((root / ".staging").iterdir())


def test_second_approved_publication_moves_current_and_keeps_the_first(cfg, runs):
    P.publish_run(cfg, runs["a"])
    root = root_of(cfg)
    first_manifest = (root / "versions" / IDS["a"] / "manifest.json").read_bytes()
    outcome = P.publish_run(cfg, runs["b"])
    assert outcome.estado == "publicada" and outcome.version_anterior == IDS["a"]
    assert P.read_current(root) == IDS["b"]
    assert (root / "versions" / IDS["a"] / "manifest.json").read_bytes() == first_manifest
    assert P.verify_directory(root / "versions" / IDS["a"], IDS["a"]) == []
    assert read_json(root / "versions" / IDS["b"] / "metadata.json")["publicacion"]["version_anterior"] == IDS["a"]
    assert [e["anterior"] for e in P.read_events(root)] == [None, IDS["a"]]


def test_a_published_version_is_never_overwritten(cfg, runs):
    P.publish_run(cfg, runs["a"])
    root = root_of(cfg)
    before = (root / "versions" / IDS["a"] / "manifest.json").read_bytes()
    outcome = P.publish_run(cfg, runs["a"])
    assert outcome.estado == "bloqueada" and "version_existente" in outcome.codes
    assert (root / "versions" / IDS["a"] / "manifest.json").read_bytes() == before
    assert P.read_current(root) == IDS["a"]


def test_invalid_candidate_is_rejected_without_touching_current(cfg, runs, tmp_path):
    P.publish_run(cfg, runs["a"])
    copy_dir = tmp_path / "copia" / IDS["b"]
    shutil.copytree(runs["b"], copy_dir)
    lines = (copy_dir / "estimaciones_candidatas.csv").read_text(encoding="utf-8").splitlines()
    (copy_dir / "estimaciones_candidatas.csv").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    outcome = P.publish_run(cfg, copy_dir)
    root = root_of(cfg)
    assert outcome.estado == "bloqueada"
    assert {"contrato_invalido", "integridad_fallida"} <= set(outcome.codes)
    assert P.read_current(root) == IDS["a"]
    assert not (root / "versions" / IDS["b"]).exists()
    assert P.read_events(root)[-1]["evento"] == "publicacion_bloqueada"


def test_candidate_missing_a_required_artifact_is_rejected(cfg, runs, tmp_path):
    copy_dir = tmp_path / "copia" / IDS["b"]
    shutil.copytree(runs["b"], copy_dir)
    (copy_dir / "quality_report.json").unlink()
    outcome = P.publish_run(cfg, copy_dir)
    assert outcome.estado == "bloqueada" and outcome.codes == ["archivo_faltante"]
    assert P.read_current(root_of(cfg)) is None


def test_inconsistent_identifier_is_rejected(cfg, runs, tmp_path):
    copy_dir = tmp_path / "copia" / "otra-ejecucion"
    shutil.copytree(runs["b"], copy_dir)
    outcome = P.publish_run(cfg, copy_dir)
    assert outcome.estado == "bloqueada" and "id_inconsistente" in outcome.codes


def test_undeclared_source_is_not_published(cfg, runs):
    outcome = P.publish_run(cfg, runs["u"])
    assert outcome.estado == "bloqueada" and outcome.codes == ["fuente_no_declarada"]
    assert P.read_current(root_of(cfg)) is None and not (root_of(cfg) / "versions" / IDS["u"]).exists()


def test_candidate_rejected_by_the_rule_is_not_published(cfg, runs):
    outcome = P.publish_run(cfg, runs["r"])
    assert outcome.estado == "bloqueada" and "no_aprobada" in outcome.codes
    assert P.read_current(root_of(cfg)) is None


def test_synthetic_publication_is_clearly_identified(cfg, runs):
    P.publish_run(cfg, runs["a"])
    root = root_of(cfg)
    version = root / "versions" / IDS["a"]
    meta = read_json(version / "metadata.json")
    assert meta["fuente"]["tipo"] == "sintetica"
    assert "fuente_sintetica" in [item["id"] for item in meta["limitaciones"]]
    assert read_json(version / "manifest.json")["fuente_tipo"] == "sintetica"
    assert P.read_events(root)[0]["fuente_tipo"] == "sintetica"
    assert P.list_versions(cfg)[0]["fuente_tipo"] == "sintetica"


def test_synthetic_publication_can_be_disallowed_by_configuration(cfg, runs):
    cfg["publication"]["allow_synthetic"] = False
    blocked = P.publish_run(cfg, runs["a"])
    assert blocked.estado == "bloqueada" and blocked.codes == ["sintetica_no_permitida"]
    published = P.publish_run(cfg, runs["d"])
    assert published.estado == "publicada"
    assert read_json(root_of(cfg) / "versions" / IDS["d"] / "metadata.json")["fuente"]["tipo"] == "institucional"


# ---- Restauración --------------------------------------------------------------------------
def test_restore_previous_version(cfg, runs):
    P.publish_run(cfg, runs["a"])
    P.publish_run(cfg, runs["b"])
    root = root_of(cfg)
    outcome = P.restore_version(cfg, IDS["a"], reason="regreso a la versión anterior")
    assert outcome.estado == "restaurada" and outcome.version_anterior == IDS["b"]
    assert P.read_current(root) == IDS["a"]
    assert {p.name for p in (root / "versions").iterdir()} == {IDS["a"], IDS["b"]}
    event = P.read_events(root)[-1]
    assert event["evento"] == "restauracion" and event["anterior"] == IDS["b"]
    assert event["motivo"] == "regreso a la versión anterior"
    assert P.restore_version(cfg, IDS["a"]).estado == "sin_cambios"


@pytest.mark.parametrize("version, code", [("no-existe", "version_inexistente"), ("../versions", "version_invalida")])
def test_restore_rejects_missing_or_invalid_identifiers(cfg, runs, version, code):
    P.publish_run(cfg, runs["a"])
    outcome = P.restore_version(cfg, version)
    assert outcome.estado == "rechazada" and outcome.codes == [code]
    assert P.read_current(root_of(cfg)) == IDS["a"]
    assert P.read_events(root_of(cfg))[-1]["evento"] == "restauracion_rechazada"


def corrupt_estimates(version):
    with open(version / "estimaciones.csv", "a", encoding="utf-8") as handle:
        handle.write("alteracion\n")


def tamper_metadata(version):
    meta = read_json(version / "metadata.json")
    meta["destino_alterado"] = True
    (version / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")


def remove_quality(version):
    (version / "quality_report.json").unlink()


def add_extra_file(version):
    (version / "extra.txt").write_text("no pertenece a la versión", encoding="utf-8")


@pytest.mark.parametrize("damage, code", [
    (corrupt_estimates, "integridad_fallida"),
    (tamper_metadata, "integridad_fallida"),
    (remove_quality, "archivo_faltante"),
    (add_extra_file, "archivos_inesperados"),
])
def test_restore_rejects_a_corrupt_version_and_keeps_current(cfg, runs, damage, code):
    P.publish_run(cfg, runs["a"])
    P.publish_run(cfg, runs["b"])
    root = root_of(cfg)
    damage(root / "versions" / IDS["a"])
    outcome = P.restore_version(cfg, IDS["a"])
    assert outcome.estado == "rechazada" and code in outcome.codes
    assert P.read_current(root) == IDS["b"]
    listing = {row["version"]: row for row in P.list_versions(cfg)}
    assert not listing[IDS["a"]]["valida"] and listing[IDS["b"]]["valida"] and listing[IDS["b"]]["vigente"]


def test_restore_rejects_a_version_that_was_not_approved(cfg, runs):
    P.publish_run(cfg, runs["a"])
    root = root_of(cfg)
    forged = root / "versions" / IDS["r"]
    forged.mkdir(parents=True)
    rejected_files = {key: runs["r"] / name for key, name in P.RUN_FILES.items()}
    P._stage(rejected_files, forged, IDS["r"], "2026-11-01T00:00:00+00:00", None)
    assert P.verify_directory(forged, IDS["r"]) != []
    outcome = P.restore_version(cfg, IDS["r"])
    assert outcome.estado == "rechazada" and "no_aprobada" in outcome.codes
    assert P.read_current(root) == IDS["a"]


# ---- Fallos y CURRENT ------------------------------------------------------------------------
def test_failure_updating_current_keeps_the_current_version(cfg, runs, monkeypatch):
    P.publish_run(cfg, runs["a"])
    root = root_of(cfg)
    monkeypatch.setattr(P, "_replace_pointer", boom)
    outcome = P.publish_run(cfg, runs["b"])
    assert outcome.estado == "fallida" and outcome.codes == ["actualizacion_de_current_fallida"]
    assert P.read_current(root) == IDS["a"]
    assert [p.name for p in (root / "versions").iterdir()] == [IDS["a"]]
    assert not list(root.glob("CURRENT.tmp-*")) and not list((root / ".staging").iterdir())
    assert P.read_events(root)[-1]["evento"] == "publicacion_fallida"
    monkeypatch.undo()
    assert P.publish_run(cfg, runs["b"]).estado == "publicada"


def test_failure_on_first_publication_leaves_no_current(cfg, runs, monkeypatch):
    monkeypatch.setattr(P, "_replace_pointer", boom)
    outcome = P.publish_run(cfg, runs["a"])
    assert outcome.estado == "fallida"
    assert P.read_current(root_of(cfg)) is None and not (root_of(cfg) / "CURRENT").exists()


def test_failure_updating_current_during_restore_keeps_the_current_version(cfg, runs, monkeypatch):
    P.publish_run(cfg, runs["a"])
    P.publish_run(cfg, runs["b"])
    monkeypatch.setattr(P, "_replace_pointer", boom)
    outcome = P.restore_version(cfg, IDS["a"])
    assert outcome.estado == "fallida"
    assert P.read_current(root_of(cfg)) == IDS["b"]


def test_failure_while_copying_leaves_no_partial_version(cfg, runs, monkeypatch):
    P.publish_run(cfg, runs["a"])
    original = shutil.copyfile
    calls = {"n": 0}

    def flaky(source, target, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disco lleno simulado")
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(P.shutil, "copyfile", flaky)
    outcome = P.publish_run(cfg, runs["b"])
    root = root_of(cfg)
    assert outcome.estado == "fallida" and outcome.codes == ["error_de_escritura"]
    assert P.read_current(root) == IDS["a"]
    assert [p.name for p in (root / "versions").iterdir()] == [IDS["a"]]
    assert not list((root / ".staging").iterdir())


def test_invalid_pointer_content_is_treated_as_no_current(cfg):
    root = root_of(cfg)
    root.mkdir(parents=True)
    (root / "CURRENT").write_text("../fuera\n", encoding="utf-8")
    assert P.read_current(root) is None


# ---- Concurrencia y persistencia ---------------------------------------------------------------
def test_simultaneous_publications_are_serialized(cfg, runs):
    names = ["a", "b", "c", "d"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda name: P.publish_run(cfg, runs[name]), names))
    root = root_of(cfg)
    assert [o.estado for o in outcomes] == ["publicada"] * 4
    assert {p.name for p in (root / "versions").iterdir()} == {IDS[n] for n in names}
    events = [e for e in P.read_events(root) if e["evento"] == "publicacion"]
    assert len(events) == 4
    assert events[0]["anterior"] is None
    assert [e["anterior"] for e in events[1:]] == [e["version"] for e in events[:-1]]
    assert P.read_current(root) == events[-1]["version"]
    assert all(row["valida"] for row in P.list_versions(cfg))
    assert not list(root.glob("CURRENT.tmp-*")) and not list((root / ".staging").iterdir())


def test_simultaneous_publication_of_the_same_version_publishes_it_once(cfg, runs):
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda _: P.publish_run(cfg, runs["a"]), range(3)))
    assert sorted(o.estado for o in outcomes) == ["bloqueada", "bloqueada", "publicada"]
    assert [p.name for p in (root_of(cfg) / "versions").iterdir()] == [IDS["a"]]


def test_versions_persist_across_calls(cfg, runs):
    P.publish_run(cfg, runs["a"])
    P.publish_run(cfg, runs["b"])
    listing = P.list_versions(copy.deepcopy(cfg))
    assert [row["version"] for row in listing] == [IDS["a"], IDS["b"]]
    assert [row["vigente"] for row in listing] == [False, True]
    assert all(row["valida"] for row in listing)
    assert P.read_current(root_of(cfg)) == IDS["b"]


# ---- Configuración y comando de restauración ------------------------------------------------------
def test_default_root_and_environment_overrides(tmp_path, monkeypatch):
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    monkeypatch.delenv(P.ENV_DIR, raising=False)
    assert P.load_settings(cfg).root == ROOT / "published"
    monkeypatch.setenv(P.ENV_DIR, str(tmp_path / "volumen"))
    assert P.load_settings(cfg).root == tmp_path / "volumen"
    monkeypatch.setenv(P.ENV_ENABLED, "false")
    assert not P.load_settings(cfg).enabled
    monkeypatch.setenv(P.ENV_ENABLED, "1")
    assert P.load_settings(cfg).enabled


def test_restore_command_lists_and_restores(cfg, runs, tmp_path, monkeypatch, capsys):
    P.publish_run(cfg, runs["a"])
    P.publish_run(cfg, runs["b"])
    monkeypatch.setenv(P.ENV_DIR, str(root_of(cfg)))
    assert restore_main(["--list"]) == 0
    listing = capsys.readouterr().out
    assert IDS["a"] in listing and IDS["b"] in listing and "válida" in listing
    assert restore_main(["--version", IDS["a"], "--reason", "prueba del comando"]) == 0
    assert P.read_current(root_of(cfg)) == IDS["a"]
    assert restore_main(["--version", "no-existe"]) == 2
    assert P.read_current(root_of(cfg)) == IDS["a"]


# ---- Integración con el pipeline y MLflow -------------------------------------------------------------
@pytest.fixture()
def publishing(monkeypatch):
    monkeypatch.setenv(P.ENV_ENABLED, "1")
    monkeypatch.delenv(P.ENV_DIR, raising=False)


def test_pipeline_publishes_accepted_runs_and_keeps_previous_versions(publishing, runs_dir, tmp_path, good_csv):
    cfg = make_cfg(runs_dir, tmp_path / "published")
    first = execute(cfg, data_path=str(good_csv), source_kind="sintetica")
    second = execute(cfg, data_path=str(good_csv), source_kind="sintetica")
    root = root_of(cfg)
    assert first["exit_code"] == second["exit_code"] == 0
    assert first["log"]["publicacion_detalle"]["estado"] == "publicada"
    assert second["log"]["publicacion_detalle"]["version_anterior"] == first["run_id"]
    assert "publicada como versión" in second["log"]["publicacion"]
    assert P.read_current(root) == second["run_id"]
    assert {p.name for p in (root / "versions").iterdir()} == {first["run_id"], second["run_id"]}
    assert any(s["etapa"] == "publicacion" and s["estado"] == "publicada" for s in second["log"]["etapas"])


def test_pipeline_does_not_publish_rejected_or_blocked_candidates(publishing, runs_dir, tmp_path, good_csv):
    cfg = make_cfg(runs_dir, tmp_path / "published")
    cfg["acceptance"]["min_evaluated_months"] = 99
    rejected = execute(cfg, data_path=str(good_csv), source_kind="sintetica")
    assert rejected["exit_code"] == 0 and "bloqueada por la regla" in rejected["log"]["publicacion"]
    assert "publicacion_detalle" not in rejected["log"]
    undeclared = execute(make_cfg(runs_dir, tmp_path / "published"), data_path=str(good_csv))
    assert undeclared["exit_code"] == 0
    assert undeclared["log"]["publicacion_detalle"]["estado"] == "bloqueada"
    assert "bloqueada" in undeclared["log"]["publicacion"]
    assert P.read_current(root_of(cfg)) is None
    assert not (root_of(cfg) / "versions").exists() or not list((root_of(cfg) / "versions").iterdir())


def test_pipeline_reports_a_technical_publication_failure(publishing, runs_dir, tmp_path, good_csv, monkeypatch):
    cfg = make_cfg(runs_dir, tmp_path / "published")
    monkeypatch.setattr(P, "_replace_pointer", boom)
    out = execute(cfg, data_path=str(good_csv), source_kind="sintetica")
    assert out["exit_code"] == 1
    assert out["log"]["publicacion_detalle"]["estado"] == "fallida"
    assert any("publicación fallida" in e for e in out["log"]["errores"])
    assert P.read_current(root_of(cfg)) is None


def test_publication_disabled_keeps_the_previous_behavior(runs_dir, tmp_path, good_csv):
    cfg = make_cfg(runs_dir, tmp_path / "published")
    out = execute(cfg, data_path=str(good_csv), source_kind="sintetica")
    assert out["exit_code"] == 0 and out["log"]["aceptacion"]["aceptada"]
    assert "deshabilitada" in out["log"]["publicacion"]
    assert not (tmp_path / "published").exists()


def test_publication_result_is_recorded_in_mlflow(publishing, runs_dir, tmp_path, good_csv, monkeypatch):
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    from pipeline.tracking import ENV_ENABLED as MLFLOW_ENABLED
    from pipeline.tracking import load_settings as mlflow_settings

    monkeypatch.setenv(MLFLOW_ENABLED, "1")
    cfg = make_cfg(runs_dir, tmp_path / "published")
    cfg["mlflow"]["dir"] = str(tmp_path / "mlflow")
    out = execute(cfg, data_path=str(good_csv), source_kind="sintetica")
    settings = mlflow_settings(cfg)
    client = MlflowClient(tracking_uri=settings.tracking_uri)
    experiment = client.get_experiment_by_name(settings.experiment)
    run = next(r for r in client.search_runs([experiment.experiment_id])
               if r.data.tags.get("pipeline.run_id") == out["run_id"])
    assert out["exit_code"] == 0 and out["log"]["mlflow"]["trazabilidad"] == "completa"
    assert run.data.tags["publicacion.estado"] == "publicada"
    assert run.data.tags["publicacion.version"] == out["run_id"]
    assert run.data.tags["publicacion.fecha"] == out["log"]["publicacion_detalle"]["fecha_publicacion"]
    published = read_json(root_of(cfg) / "versions" / out["run_id"] / "metadata.json")
    assert published["mlflow"]["run_id"] == run.info.run_id
    assert published["run_id"] == out["run_id"] and run.info.run_id != out["run_id"]
