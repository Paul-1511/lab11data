"""Pruebas de configuración de Docker: leen los archivos; no ejecutan Docker ni lo requieren."""

import re
from pathlib import Path

import pytest
import yaml

from pipeline.config import load_config

ROOT = Path(__file__).resolve().parent.parent
SERVICES = {"pipeline", "streamlit", "mlflow"}
WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/]|\\\\")


@pytest.fixture(scope="module")
def dockerfile():
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ignore_entries():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfg():
    return load_config("config/config.yaml")


def instructions(dockerfile, name):
    return [line.split(None, 1)[1].strip() for line in dockerfile.splitlines() if line.upper().startswith(name + " ")]


def mounts(service):
    return {m["target"]: m for m in service.get("volumes", [])}


def command_text(service):
    command = service["command"]
    return command if isinstance(command, str) else " ".join(str(part) for part in command)


# ---- Existencia y estructura -----------------------------------------------------------------
def test_docker_files_exist():
    for name in ("Dockerfile", "docker-compose.yml", ".dockerignore", ".env.example"):
        assert (ROOT / name).is_file(), name


def test_compose_defines_exactly_the_required_services(compose):
    assert set(compose["services"]) == SERVICES


def test_dockerfile_uses_the_project_python_and_declared_dependencies(dockerfile):
    assert re.search(r"^FROM python:3\.11", dockerfile, re.MULTILINE)
    assert "pip install -r requirements.txt" in dockerfile
    assert instructions(dockerfile, "WORKDIR") == ["/app"]
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("streamlit", "plotly", "pandas", "pyyaml", "mlflow", "openpyxl"):
        assert package in requirements


def test_dockerfile_runs_as_an_unprivileged_user(dockerfile):
    user = instructions(dockerfile, "USER")[-1]
    assert user not in ("root", "0") and user == "app"
    assert dockerfile.rindex("USER app") > dockerfile.index("chown -R app:app")


def test_dockerfile_does_not_start_estimations_by_default(dockerfile):
    cmd = instructions(dockerfile, "CMD")[-1]
    assert "--help" in cmd and "ENTRYPOINT" not in dockerfile


# ---- Exclusión de datos institucionales ------------------------------------------------------
def test_dockerfile_copies_only_explicit_project_paths(dockerfile):
    sources = [line.split()[1] for line in dockerfile.splitlines() if line.upper().startswith("COPY ")]
    assert "." not in sources and "./" not in sources
    for source in sources:
        assert (ROOT / source).exists(), source
    top_level = {source.split("/")[0] for source in sources}
    assert {"pipeline", "dashboard", "config", "app.py", "requirements.txt"} <= top_level
    assert not top_level & {"data", "artifacts", "published", "mlflow", "mlruns", "docs", "tests"}
    assert ".streamlit/secrets.toml" not in sources


def test_dockerfile_does_not_mention_institutional_data(dockerfile):
    text = dockerfile.lower()
    for token in ("data/raw", ".xlsx", "base_migracion", "copy data"):
        assert token not in text
    assert not instructions(dockerfile, "ADD")


def test_dockerignore_excludes_data_results_and_local_secrets(ignore_entries):
    for entry in (".git", "data", "**/*.xlsx", "artifacts", "published", "mlflow", "**/*.db", ".env",
                  ".streamlit/secrets.toml", "**/__pycache__"):
        assert entry in ignore_entries, entry


def test_dockerignore_does_not_remove_what_the_dockerfile_copies(dockerfile, ignore_entries):
    sources = {line.split()[1].rstrip("/") for line in dockerfile.splitlines() if line.upper().startswith("COPY ")}
    assert not sources & set(ignore_entries)


def test_env_file_is_ignored_by_git_and_example_has_no_secrets():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"password|secret|token|key", example, re.IGNORECASE)


# ---- Servicios: separación de responsabilidades ----------------------------------------------
def test_pipeline_is_explicit_and_not_started_by_up(compose):
    service = compose["services"]["pipeline"]
    assert service["profiles"] == ["pipeline"]
    assert "--help" in command_text(service)
    assert "restart" not in service and "ports" not in service


def test_pipeline_receives_the_data_read_only_from_an_external_configurable_path(compose):
    data = mounts(compose["services"]["pipeline"])["/data"]
    assert data["type"] == "bind" and data["read_only"] is True
    assert data["source"].startswith("${LAB11_DATA_DIR")


def test_dashboard_does_not_run_the_pipeline_or_receive_data(compose):
    service = compose["services"]["streamlit"]
    command = command_text(service)
    assert command.startswith("streamlit run app.py")
    assert "pipeline" not in command
    assert all(m["type"] == "volume" for m in service["volumes"])
    assert set(mounts(service)) == {"/app/published"}
    assert mounts(service)["/app/published"]["read_only"] is True
    assert "profiles" not in service and "depends_on" not in service


def test_mlflow_service_does_not_receive_data_or_published_versions(compose):
    assert set(mounts(compose["services"]["mlflow"])) == {"/app/mlflow"}


def test_only_the_pipeline_mounts_data(compose):
    for name, service in compose["services"].items():
        assert ("/data" in mounts(service)) == (name == "pipeline")


def test_ports_are_local_only(compose, cfg):
    streamlit = compose["services"]["streamlit"]["ports"]
    mlflow = compose["services"]["mlflow"]["ports"]
    assert len(streamlit) == len(mlflow) == 1
    assert streamlit[0].startswith("127.0.0.1:") and streamlit[0].endswith(":8501")
    assert mlflow[0].startswith("127.0.0.1:") and mlflow[0].endswith(":5000")
    assert "8501" in command_text(compose["services"]["streamlit"])
    assert "5000" in command_text(compose["services"]["mlflow"])


# ---- Persistencia ----------------------------------------------------------------------------
def test_results_live_in_named_volumes_not_in_the_image(compose):
    assert set(compose["volumes"]) == {"mlflow_data", "published_data", "runs_data"}
    pipeline = mounts(compose["services"]["pipeline"])
    expected = {"/app/mlflow": "mlflow_data", "/app/published": "published_data", "/app/artifacts": "runs_data"}
    for target, volume in expected.items():
        assert pipeline[target]["type"] == "volume" and pipeline[target]["source"] == volume
    assert mounts(compose["services"]["mlflow"])["/app/mlflow"]["source"] == "mlflow_data"
    assert mounts(compose["services"]["streamlit"])["/app/published"]["source"] == "published_data"
    for volume in compose["volumes"].values():
        assert not volume or "external" not in volume


def test_mount_points_exist_in_the_image_owned_by_the_unprivileged_user(dockerfile):
    for path in ("/app/artifacts", "/app/published", "/app/mlflow"):
        assert f"mkdir -p {path}" in dockerfile or path in dockerfile.split("mkdir -p", 1)[1].split("&&", 1)[0]
    assert "chown -R app:app /app" in dockerfile


def test_container_paths_match_the_configuration_of_the_project(compose, cfg, dockerfile):
    """Las rutas de contenedor coinciden con lo que lee el código: rutas relativas a /app y variables de entorno."""
    assert cfg["run"]["output_dir"].startswith("artifacts/")
    assert cfg["run"]["published_dir"] == "published"
    pipeline_env = compose["services"]["pipeline"]["environment"]
    assert pipeline_env["LAB11_PUBLISHED_DIR"] == "/app/published"
    assert compose["services"]["streamlit"]["environment"]["LAB11_PUBLISHED_DIR"] == "/app/published"
    assert "LAB11_PUBLISHED_DIR=/app/published" in dockerfile and "LAB11_MLFLOW_DIR=/app/mlflow" in dockerfile


def test_mlflow_server_uses_the_same_store_as_the_pipeline(compose, cfg):
    directory = compose["services"]["pipeline"]["environment"]["LAB11_MLFLOW_DIR"]
    database = f"{directory}/{cfg['mlflow']['db_file']}"
    artifacts = f"{directory}/{cfg['mlflow']['artifacts_dir']}"
    command = compose["services"]["mlflow"]["command"]
    assert command[command.index("--backend-store-uri") + 1] == f"sqlite:///{database}"
    assert command[command.index("--default-artifact-root") + 1] == artifacts
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert directory == "/app/mlflow"


def test_pipeline_enables_the_stores_it_needs(compose):
    env = compose["services"]["pipeline"]["environment"]
    assert env["LAB11_MLFLOW_ENABLED"] == "1" and env["LAB11_PUBLISH_ENABLED"] == "1"
    assert env["LAB11_MONITOR_ENABLED"] == "1"


# ---- Portabilidad y compatibilidad con el proyecto -------------------------------------------
def test_docker_files_have_no_windows_paths_or_credentials():
    for name in ("Dockerfile", "docker-compose.yml", ".dockerignore"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not WINDOWS_PATH.search(text), name
        assert not re.search(r"password|secret_key|api_key|token", text, re.IGNORECASE), name


def test_all_services_share_the_same_image_definition(compose):
    builds = {name: (s["build"]["context"], s["image"]) for name, s in compose["services"].items()}
    assert len(set(builds.values())) == 1


def test_synthetic_demo_input_is_available_in_the_repository_for_the_data_mount():
    assert (ROOT / "data" / "synthetic" / "base_sintetica.csv").is_file()


def test_modules_run_by_the_services_exist():
    for module in ("pipeline/run.py", "pipeline/restore.py", "app.py", "dashboard/published.py", "config/config.yaml"):
        assert (ROOT / module).is_file(), module


def test_tracking_accepts_a_container_directory_override(cfg, monkeypatch):
    from pipeline.tracking import ENV_DIR, load_settings

    monkeypatch.setenv(ENV_DIR, "/app/mlflow")
    settings = load_settings(cfg)
    assert settings.db_path.name == cfg["mlflow"]["db_file"] and settings.artifact_dir.name == cfg["mlflow"]["artifacts_dir"]
    assert settings.db_path.parent == settings.artifact_dir.parent
