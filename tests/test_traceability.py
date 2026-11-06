"""Pruebas de trazabilidad del commit de Git: ejecución local y ejecución en la imagen (sin Git, con variables)."""

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from make_synthetic_data import make_synthetic  # noqa: E402

from pipeline import run as R  # noqa: E402
from pipeline.config import load_config  # noqa: E402

# Identificadores de prueba con el formato de un commit; no corresponden a ningún commit del proyecto.
DECLARED = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv(R.ENV_COMMIT, raising=False)
    monkeypatch.delenv(R.ENV_TREE_MODIFIED, raising=False)


@pytest.fixture()
def no_git(monkeypatch):
    """Simula la imagen de Docker: no hay repositorio ni ejecutable de Git."""
    monkeypatch.setattr(R, "_git_commit", lambda: "sin-git")
    monkeypatch.setattr(R, "_git_tree_modified", lambda: None)


def run_pipeline(tmp_path):
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    cfg["run"]["output_dir"] = str(tmp_path / "runs")
    cfg["run"]["published_dir"] = str(tmp_path / "published")
    cfg["source"]["synthetic_sha256"] = []
    data = tmp_path / "datos.csv"
    make_synthetic().to_csv(data, index=False)
    out = R.execute(cfg, data_path=str(data), source_kind="sintetica")
    metadata = json.loads((Path(out["run_dir"]) / "metadata.json").read_text(encoding="utf-8"))
    return cfg, out, metadata


# ---- Resolución del commit -------------------------------------------------------------------
def test_local_git_repository_is_the_first_source(monkeypatch):
    monkeypatch.setattr(R, "_git_commit", lambda: OTHER)
    monkeypatch.setattr(R, "_git_tree_modified", lambda: True)
    monkeypatch.setenv(R.ENV_COMMIT, DECLARED)
    info = R._code_version()
    assert info["commit_git"] == OTHER and info["commit_git_origen"] == R.ORIGIN_GIT
    assert info["arbol_git_modificado"] is True and info["advertencia"] is None


def test_declared_commit_is_used_inside_the_image(no_git, monkeypatch):
    monkeypatch.setenv(R.ENV_COMMIT, DECLARED)
    monkeypatch.setenv(R.ENV_TREE_MODIFIED, "false")
    info = R._code_version()
    assert info["commit_git"] == DECLARED and info["commit_git_origen"] == R.ORIGIN_ENV
    assert info["arbol_git_modificado"] is False and info["advertencia"] is None


@pytest.mark.parametrize("raw, expected", [("true", True), ("TRUE", True), ("false", False), ("", None), ("tal vez", None)])
def test_tree_modification_flag_is_read_from_the_image(no_git, monkeypatch, raw, expected):
    monkeypatch.setenv(R.ENV_COMMIT, DECLARED)
    monkeypatch.setenv(R.ENV_TREE_MODIFIED, raw)
    assert R._code_version()["arbol_git_modificado"] is expected


def test_uppercase_declared_commit_is_normalized(no_git, monkeypatch):
    monkeypatch.setenv(R.ENV_COMMIT, DECLARED.upper())
    assert R._code_version()["commit_git"] == DECLARED


def test_missing_commit_is_explicitly_unavailable(no_git):
    info = R._code_version()
    assert info == {"commit_git": "sin-git", "arbol_git_modificado": None,
                    "commit_git_origen": R.ORIGIN_NONE, "advertencia": None}


@pytest.mark.parametrize("value", ["HEAD", "abc123", "no-es-un-commit", DECLARED[:-1], DECLARED + "0", "g" * 40])
def test_invalid_declared_commit_is_ignored_with_a_warning(no_git, monkeypatch, value):
    monkeypatch.setenv(R.ENV_COMMIT, value)
    monkeypatch.setenv(R.ENV_TREE_MODIFIED, "false")
    info = R._code_version()
    assert info["commit_git"] == "sin-git" and info["commit_git_origen"] == R.ORIGIN_NONE
    assert info["arbol_git_modificado"] is None and R.ENV_COMMIT in info["advertencia"]


def test_sha256_object_ids_are_accepted(no_git, monkeypatch):
    monkeypatch.setenv(R.ENV_COMMIT, "ab" * 32)
    assert R._code_version()["commit_git"] == "ab" * 32


def test_local_execution_without_variables_keeps_working():
    info = R._code_version()
    assert info["commit_git_origen"] in (R.ORIGIN_GIT, R.ORIGIN_NONE)
    if info["commit_git_origen"] == R.ORIGIN_GIT:
        assert re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", info["commit_git"])
    else:
        assert info["commit_git"] == "sin-git"


# ---- Registro en run_log.json, metadata.json y MLflow ----------------------------------------
def test_declared_commit_is_recorded_in_run_log_and_metadata(no_git, monkeypatch, tmp_path):
    monkeypatch.setenv(R.ENV_COMMIT, DECLARED)
    monkeypatch.setenv(R.ENV_TREE_MODIFIED, "true")
    _, out, metadata = run_pipeline(tmp_path)
    saved = json.loads((Path(out["run_dir"]) / "run_log.json").read_text(encoding="utf-8"))
    for log in (out["log"], saved):
        assert log["commit_git"] == DECLARED and log["commit_git_origen"] == R.ORIGIN_ENV
        assert log["arbol_git_modificado"] is True
    assert metadata["commit_git"] == DECLARED and metadata["commit_git_origen"] == R.ORIGIN_ENV
    assert metadata["arbol_git_modificado"] is True


def test_unavailable_commit_is_recorded_explicitly(no_git, tmp_path):
    _, out, metadata = run_pipeline(tmp_path)
    assert out["log"]["commit_git_origen"] == R.ORIGIN_NONE
    assert metadata["commit_git"] is None and metadata["commit_git_origen"] == "no_disponible"
    assert metadata["arbol_git_modificado"] is None


def test_published_metadata_keeps_the_commit_information(no_git, monkeypatch, tmp_path):
    from pipeline import publish as P

    monkeypatch.setenv(R.ENV_COMMIT, DECLARED)
    monkeypatch.setenv(R.ENV_TREE_MODIFIED, "false")
    cfg, out, _ = run_pipeline(tmp_path)
    assert P.publish_run(cfg, out["run_dir"]).estado == P.STATE_PUBLISHED
    published = json.loads((Path(cfg["run"]["published_dir"]) / "versions" / out["run_id"] / "metadata.json")
                           .read_text(encoding="utf-8"))
    assert published["commit_git"] == DECLARED and published["commit_git_origen"] == "variable_de_entorno"


def test_commit_information_is_recorded_in_mlflow(no_git, monkeypatch, tmp_path):
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    from pipeline.tracking import ENV_ENABLED as MLFLOW_ENABLED
    from pipeline.tracking import load_settings as mlflow_settings

    monkeypatch.setenv(MLFLOW_ENABLED, "1")
    monkeypatch.setenv(R.ENV_COMMIT, DECLARED)
    monkeypatch.setenv(R.ENV_TREE_MODIFIED, "false")
    cfg = copy.deepcopy(load_config("config/config.yaml"))
    cfg["run"]["output_dir"] = str(tmp_path / "runs")
    cfg["run"]["published_dir"] = str(tmp_path / "published")
    cfg["mlflow"]["dir"] = str(tmp_path / "mlflow")
    cfg["source"]["synthetic_sha256"] = []
    data = tmp_path / "datos.csv"
    make_synthetic().to_csv(data, index=False)
    with_commit = R.execute(cfg, data_path=str(data), source_kind="sintetica")
    monkeypatch.delenv(R.ENV_COMMIT)
    monkeypatch.delenv(R.ENV_TREE_MODIFIED)
    without_commit = R.execute(cfg, data_path=str(data), source_kind="sintetica")

    settings = mlflow_settings(cfg)
    client = MlflowClient(tracking_uri=settings.tracking_uri)
    experiment = client.get_experiment_by_name(settings.experiment)
    runs = {r.data.tags["pipeline.run_id"]: r for r in client.search_runs([experiment.experiment_id])}
    tags = runs[with_commit["run_id"]].data.tags
    assert tags["git.commit"] == DECLARED and tags["git.commit_origen"] == "variable_de_entorno"
    assert tags["git.tree_modified"] == "false"
    tags = runs[without_commit["run_id"]].data.tags
    assert "git.commit" not in tags and tags["git.commit_origen"] == "no_disponible"
