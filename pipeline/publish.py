"""Publicación controlada, versionado y restauración de estimaciones aprobadas.

Estructura bajo `run.published_dir` (o `LAB11_PUBLISHED_DIR`):

    versions/<run_id>/   estimaciones.csv, metadata.json, decision_aceptacion.json,
                         quality_report.json y manifest.json (huellas SHA-256)
    CURRENT              identificador de la versión vigente, reemplazado de forma atómica
    publicaciones.jsonl  bitácora de publicaciones, restauraciones y rechazos
    .staging/            preparación temporal de una publicación en curso
    .lock                bloqueo exclusivo entre publicaciones y restauraciones simultáneas

Una versión publicada no se modifica ni se sobrescribe. Un candidato se publica solo
si cumple todas las condiciones de `check_bundle`; ante cualquier fallo, `CURRENT`
conserva la versión vigente. El tablero debe leer únicamente la versión de `CURRENT`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .checks import FAIL, SKIP, SPECS
from .config import resolve
from .contract import ContractError, validate_estimates_file
from .metadata import SOURCE_INSTITUTIONAL, SOURCE_SYNTHETIC, validate_metadata
from .tracking import redact_paths

ENV_ENABLED = "LAB11_PUBLISH_ENABLED"
ENV_DIR = "LAB11_PUBLISHED_DIR"

VERSIONS_DIR = "versions"
CURRENT_FILE = "CURRENT"
LOG_FILE = "publicaciones.jsonl"
STAGING_DIR = ".staging"
LOCK_FILE = ".lock"
MANIFEST_FILE = "manifest.json"
MANIFEST_SCHEMA = "1.0"

# Archivos de una versión publicada y su origen en la carpeta de una ejecución.
VERSION_FILES = {
    "estimaciones": "estimaciones.csv",
    "metadata": "metadata.json",
    "decision": "decision_aceptacion.json",
    "quality": "quality_report.json",
}
RUN_FILES = {
    "estimaciones": "estimaciones_candidatas.csv",
    "metadata": "metadata.json",
    "decision": "decision_aceptacion.json",
    "quality": "quality_report.json",
}

STATE_PUBLISHED = "publicada"
STATE_BLOCKED = "bloqueada"
STATE_FAILED = "fallida"
STATE_RESTORED = "restaurada"
STATE_UNCHANGED = "sin_cambios"
STATE_REJECTED = "rechazada"

# El identificador de versión es el run_id del pipeline; este patrón impide rutas relativas.
_VERSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FALSE_VALUES = {"0", "false", "no", "off"}
_REPLACE_RETRIES = 10


class PublicationError(Exception):
    """Error técnico durante una publicación o restauración."""


class LockTimeout(PublicationError):
    """No se obtuvo el bloqueo exclusivo a tiempo."""


@dataclass(frozen=True)
class PublicationSettings:
    enabled: bool
    root: Path
    allow_synthetic: bool
    lock_timeout: float


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def load_settings(cfg: dict) -> PublicationSettings:
    """Lee `publication` y `run.published_dir`; las variables de entorno tienen prioridad."""
    section = cfg.get("publication") or {}
    return PublicationSettings(
        enabled=_as_bool(os.environ.get(ENV_ENABLED), bool(section.get("enabled", True))),
        root=resolve(os.environ.get(ENV_DIR) or cfg["run"].get("published_dir", "published")),
        allow_synthetic=bool(section.get("allow_synthetic", True)),
        lock_timeout=float(section.get("lock_timeout_seconds", 30)),
    )


@dataclass
class PublishOutcome:
    estado: str
    version: str | None = None
    fecha: str | None = None
    version_anterior: str | None = None
    errores: list[ContractError] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.estado in (STATE_PUBLISHED, STATE_RESTORED, STATE_UNCHANGED)

    @property
    def codes(self) -> list[str]:
        return [e.code for e in self.errores]

    def to_dict(self) -> dict:
        return {
            "estado": self.estado, "version": self.version, "fecha_publicacion": self.fecha,
            "version_anterior": self.version_anterior,
            "errores": [e.to_dict() for e in self.errores], "advertencias": list(self.advertencias),
        }

    def message(self) -> str:
        if self.estado == STATE_PUBLISHED:
            return f"publicada como versión {self.version}; CURRENT apunta a esa versión"
        if self.estado == STATE_BLOCKED:
            return f"bloqueada por la validación de publicación ({', '.join(self.codes)}); lo publicado no cambió"
        if self.estado == STATE_FAILED:
            return f"falló la publicación ({', '.join(self.codes)}); CURRENT conserva la versión vigente"
        if self.estado == STATE_RESTORED:
            return f"versión {self.version} restaurada; CURRENT actualizado"
        if self.estado == STATE_UNCHANGED:
            return f"la versión {self.version} ya es la vigente"
        return f"restauración rechazada ({', '.join(self.codes)}); lo publicado no cambió"


def _error(code: str, message: str, **detail) -> ContractError:
    return ContractError(code, message, None, detail)


def _utc(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, _error("json_invalido", f"{Path(path).name} no es un JSON legible: {redact_paths(exc)}", archivo=Path(path).name)


# ---------------------------------------------------------------------------
# Condiciones de publicación (comunes a publicar, verificar y restaurar)
# ---------------------------------------------------------------------------
def _contract_view(meta: dict) -> dict:
    """Configuración mínima que usa el validador, tomada de los metadatos de la propia versión."""
    return {
        "target": {"destination": meta["objetivo"]["destino"], "unit": meta["objetivo"]["unidad"]},
        "periods": {"forecast": {"start": meta["periodos"]["horizonte"]["inicio"], "end": meta["periodos"]["horizonte"]["fin"]}},
    }


def check_bundle(paths: dict[str, Path], version_id: str, *, allow_synthetic: bool) -> list[ContractError]:
    """Comprueba que un conjunto de archivos se pueda publicar como `version_id`.

    `paths` usa las claves de `VERSION_FILES`. Se valida con los metadatos de la propia
    versión, de modo que una restauración no depende de la configuración vigente.
    """
    if not _VERSION_ID.match(version_id):
        return [_error("version_invalida", "el identificador de versión contiene caracteres no permitidos")]
    missing = sorted(Path(p).name for p in paths.values() if not Path(p).is_file())
    if missing:
        return [_error("archivo_faltante", f"faltan archivos necesarios: {missing}", archivos=missing)]

    errors: list[ContractError] = []
    meta, e1 = _load_json(paths["metadata"])
    decision, e2 = _load_json(paths["decision"])
    quality, e3 = _load_json(paths["quality"])
    errors += [e for e in (e1, e2, e3) if e is not None]
    if errors:
        return errors

    meta_check = validate_metadata(meta)
    if not meta_check.valid:
        return [_error("metadatos_invalidos", "los metadatos no cumplen el esquema", codigos=meta_check.codes)]

    if decision.get("aceptada") is not True:
        errors.append(_error("no_aprobada", "la decisión de aceptación no es favorable"))
    if meta["aceptacion"].get("aceptada") is not True or meta["aceptacion"].get("aceptada") != decision.get("aceptada"):
        errors.append(_error("no_aprobada", "los metadatos no coinciden con una decisión de aceptación favorable"))
    if meta["contrato"].get("valido") is not True:
        errors.append(_error("contrato_invalido", "los metadatos indican que el contrato de estimaciones no es válido"))

    ids = {"metadata": meta["run_id"], "quality_report": quality.get("run_id")}
    if any(value != version_id for value in ids.values()):
        errors.append(_error("id_inconsistente", "los identificadores no coinciden con la versión", version=version_id, encontrados=ids))

    source_type = meta["fuente"].get("tipo")
    if source_type == "no_declarada":
        errors.append(_error("fuente_no_declarada", "no se publican resultados de fuentes no declaradas"))
    elif source_type not in (SOURCE_SYNTHETIC, SOURCE_INSTITUTIONAL):
        errors.append(_error("fuente_invalida", "el tipo de fuente no es sintética ni institucional", encontrado=source_type))
    elif source_type == SOURCE_SYNTHETIC and not allow_synthetic:
        errors.append(_error("sintetica_no_permitida", "la configuración no permite publicar resultados sintéticos"))

    controls = {c.get("id"): c.get("status") for c in quality.get("controles", [])}
    bad = sorted(k for k, v in controls.items() if v in (FAIL, SKIP))
    if (
        set(controls) != set(SPECS) or bad
        or quality.get("estado_global") == FAIL or quality.get("publicacion_permitida") is not True
    ):
        errors.append(_error(
            "calidad_no_aprobada", "los seis controles de calidad deben estar sin fallos críticos",
            controles=sorted(controls), con_fallo=bad,
        ))

    try:
        contract = validate_estimates_file(
            paths["estimaciones"], _contract_view(meta), run_id=version_id, version=meta["version_procedimiento"]
        )
    except Exception as exc:
        errors.append(_error("contrato_invalido", f"no se pudo validar el contrato: {redact_paths(exc)}"))
    else:
        if not contract.valid:
            errors.append(_error("contrato_invalido", "las estimaciones no cumplen el contrato", codigos=contract.codes))

    if sha256_file(paths["estimaciones"]) != meta["integridad"].get("estimaciones_candidatas_sha256"):
        errors.append(_error("integridad_fallida", "la huella de estimaciones no coincide con la de los metadatos"))
    return errors


def verify_directory(directory: Path, version_id: str, *, allow_synthetic: bool = True) -> list[ContractError]:
    """Verifica estructura, integridad (manifest) y condiciones de una carpeta de versión."""
    directory = Path(directory)
    expected = set(VERSION_FILES.values()) | {MANIFEST_FILE}
    present = {p.name for p in directory.iterdir()}
    missing = sorted(expected - present)
    if missing:
        return [_error("archivo_faltante", f"faltan archivos de la versión: {missing}", archivos=missing)]

    errors: list[ContractError] = []
    extra = sorted(present - expected)
    if extra:
        errors.append(_error("archivos_inesperados", f"hay archivos que no pertenecen a la versión: {extra}", archivos=extra))
    manifest, problem = _load_json(directory / MANIFEST_FILE)
    if problem is not None:
        return errors + [problem]
    try:
        if manifest["version"] != version_id:
            errors.append(_error("id_inconsistente", "el manifest pertenece a otra versión", version=version_id, manifest=manifest["version"]))
        corrupt = sorted(
            name for name in VERSION_FILES.values()
            if manifest["archivos"].get(name, {}).get("sha256") != sha256_file(directory / name)
            or manifest["archivos"].get(name, {}).get("bytes") != (directory / name).stat().st_size
        )
    except (KeyError, TypeError, AttributeError):
        return errors + [_error("manifest_invalido", "el manifest no tiene la estructura esperada")]
    if corrupt:
        errors.append(_error("integridad_fallida", f"archivos que no coinciden con su huella: {corrupt}", archivos=corrupt))
    if errors:
        return errors
    return check_bundle({key: directory / name for key, name in VERSION_FILES.items()}, version_id, allow_synthetic=allow_synthetic)


# ---------------------------------------------------------------------------
# Escritura segura
# ---------------------------------------------------------------------------
def _try_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive(root: Path, timeout: float):
    """Bloqueo del sistema operativo: se libera solo si el proceso termina de forma abrupta."""
    root.mkdir(parents=True, exist_ok=True)
    handle = open(root / LOCK_FILE, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout("otra publicación o restauración mantiene el bloqueo de published/")
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()


def _fsync_file(path: Path) -> None:
    try:
        with open(path, "r+b") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _replace_pointer(source: Path, target: Path) -> None:
    """Reemplazo atómico del puntero; en Windows reintenta si otro proceso lo tiene abierto."""
    last: OSError | None = None
    for _ in range(_REPLACE_RETRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.05)
    raise last


def _write_current(root: Path, version_id: str) -> None:
    temporary = root / f"{CURRENT_FILE}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(version_id + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_pointer(temporary, root / CURRENT_FILE)
        _fsync_dir(root)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _append_event(root: Path, event: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with open(root / LOG_FILE, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_events(root: Path) -> list[dict]:
    path = Path(root) / LOG_FILE
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def read_current(root: Path) -> str | None:
    """Versión vigente, o `None` si CURRENT no existe o no contiene un identificador válido."""
    try:
        text = (Path(root) / CURRENT_FILE).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text if _VERSION_ID.match(text) else None


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _stage(paths: dict[str, Path], staging: Path, version_id: str, stamp: str, previous: str | None) -> None:
    """Prepara la versión completa en un directorio temporal, con su manifest."""
    meta = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    meta["publicacion"] = {"version_id": version_id, "fecha_publicacion": stamp, "version_anterior": previous}
    for key in ("estimaciones", "decision", "quality"):
        shutil.copyfile(paths[key], staging / VERSION_FILES[key])
    (staging / VERSION_FILES["metadata"]).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "esquema_version": MANIFEST_SCHEMA,
        "version": version_id,
        "fuente_tipo": meta["fuente"]["tipo"],
        "fecha_publicacion": stamp,
        "archivos": {
            name: {"sha256": sha256_file(staging / name), "bytes": (staging / name).stat().st_size}
            for name in VERSION_FILES.values()
        },
    }
    (staging / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in list(VERSION_FILES.values()) + [MANIFEST_FILE]:
        _fsync_file(staging / name)


def _record_rejection(settings: PublicationSettings, event: dict) -> None:
    """Deja constancia de un rechazo; un fallo al registrar no cambia el resultado."""
    try:
        with _exclusive(settings.root, settings.lock_timeout):
            _append_event(settings.root, event)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Publicación
# ---------------------------------------------------------------------------
def publish_run(cfg: dict, run_dir: str | Path, *, now: datetime | None = None) -> PublishOutcome:
    """Publica la ejecución de `run_dir` como una versión nueva e inmutable.

    No lanza excepciones: devuelve `publicada`, `bloqueada` (no cumple las condiciones;
    nada cambió) o `fallida` (error técnico; CURRENT conserva la versión vigente).
    """
    settings = load_settings(cfg)
    run_dir = Path(run_dir)
    version_id = run_dir.name
    paths = {key: run_dir / name for key, name in RUN_FILES.items()}

    errors = check_bundle(paths, version_id, allow_synthetic=settings.allow_synthetic)
    if errors:
        _record_rejection(settings, {
            "evento": "publicacion_bloqueada", "fecha": _utc(now), "version": version_id,
            "errores": [e.code for e in errors],
        })
        return PublishOutcome(STATE_BLOCKED, version_id, errores=errors)

    try:
        with _exclusive(settings.root, settings.lock_timeout):
            return _publish_locked(settings, paths, version_id, now)
    except LockTimeout as exc:
        return PublishOutcome(STATE_FAILED, version_id, errores=[_error("bloqueo_ocupado", str(exc))])
    except Exception as exc:
        return PublishOutcome(STATE_FAILED, version_id, errores=[_error("error_de_escritura", redact_paths(exc))])


def _publish_locked(settings: PublicationSettings, paths: dict, version_id: str, now: datetime | None) -> PublishOutcome:
    root = settings.root
    target = root / VERSIONS_DIR / version_id
    (root / VERSIONS_DIR).mkdir(parents=True, exist_ok=True)
    if target.exists():
        errors = [_error("version_existente", "la versión ya está publicada y no se sobrescribe")]
        _append_event(root, {"evento": "publicacion_bloqueada", "fecha": _utc(now), "version": version_id,
                             "errores": [e.code for e in errors]})
        return PublishOutcome(STATE_BLOCKED, version_id, errores=errors)

    previous = read_current(root)
    stamp = _utc(now)
    staging = root / STAGING_DIR / f"{version_id}.{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True)
        _stage(paths, staging, version_id, stamp, previous)
        problems = verify_directory(staging, version_id, allow_synthetic=settings.allow_synthetic)
        if problems:
            _cleanup(staging)
            return PublishOutcome(STATE_FAILED, version_id, stamp, previous, [
                _error("verificacion_fallida", "la versión preparada no superó la verificación", codigos=[p.code for p in problems])
            ])
        os.rename(staging, target)
    except Exception as exc:
        _cleanup(staging)
        return PublishOutcome(STATE_FAILED, version_id, stamp, previous, [_error("error_de_escritura", redact_paths(exc))])

    try:
        _write_current(root, version_id)
    except Exception as exc:
        warnings = []
        try:
            os.rename(target, staging)
            _cleanup(staging)
        except OSError:
            warnings.append(f"la versión {version_id} quedó en versions/ sin ser la vigente")
        failure = [_error("actualizacion_de_current_fallida", redact_paths(exc))]
        try:
            _append_event(root, {"evento": "publicacion_fallida", "fecha": stamp, "version": version_id,
                                 "anterior": previous, "errores": [e.code for e in failure]})
        except Exception:
            pass
        return PublishOutcome(STATE_FAILED, version_id, stamp, previous, failure, warnings)

    outcome = PublishOutcome(STATE_PUBLISHED, version_id, stamp, previous)
    manifest_sha = sha256_file(target / MANIFEST_FILE)
    try:
        _append_event(root, {
            "evento": "publicacion", "fecha": stamp, "version": version_id, "anterior": previous,
            "fuente_tipo": json.loads((target / MANIFEST_FILE).read_text(encoding="utf-8"))["fuente_tipo"],
            "estimaciones_sha256": sha256_file(target / VERSION_FILES["estimaciones"]),
            "manifest_sha256": manifest_sha,
        })
    except Exception as exc:
        outcome.advertencias.append(f"la versión se publicó, pero no se pudo registrar en {LOG_FILE}: {redact_paths(exc)}")
    return outcome


# ---------------------------------------------------------------------------
# Consulta y restauración
# ---------------------------------------------------------------------------
def list_versions(cfg: dict) -> list[dict]:
    """Versiones existentes con su validez; solo las válidas son restaurables."""
    settings = load_settings(cfg)
    versions_dir = settings.root / VERSIONS_DIR
    current = read_current(settings.root)
    rows = []
    if not versions_dir.is_dir():
        return rows
    for directory in sorted(p for p in versions_dir.iterdir() if p.is_dir()):
        errors = verify_directory(directory, directory.name, allow_synthetic=settings.allow_synthetic)
        manifest, _ = _load_json(directory / MANIFEST_FILE)
        manifest = manifest if isinstance(manifest, dict) else {}
        rows.append({
            "version": directory.name,
            "valida": not errors,
            "errores": [e.code for e in errors],
            "fecha_publicacion": manifest.get("fecha_publicacion"),
            "fuente_tipo": manifest.get("fuente_tipo"),
            "vigente": directory.name == current,
        })
    return rows


def restore_version(cfg: dict, version_id: str, *, reason: str | None = None, now: datetime | None = None) -> PublishOutcome:
    """Vuelve a señalar una versión aprobada e íntegra con CURRENT, sin regenerar estimaciones."""
    settings = load_settings(cfg)
    root = settings.root
    stamp = _utc(now)
    text = redact_paths(reason)[:500] if reason else None
    try:
        with _exclusive(root, settings.lock_timeout):
            directory = root / VERSIONS_DIR / version_id
            if not _VERSION_ID.match(version_id):
                errors = [_error("version_invalida", "el identificador de versión contiene caracteres no permitidos")]
            elif not directory.is_dir():
                errors = [_error("version_inexistente", f"no existe la versión {version_id}")]
            else:
                errors = verify_directory(directory, version_id, allow_synthetic=settings.allow_synthetic)
            if errors:
                _append_event(root, {"evento": "restauracion_rechazada", "fecha": stamp, "version": version_id,
                                     "errores": [e.code for e in errors], "motivo": text})
                return PublishOutcome(STATE_REJECTED, version_id, errores=errors)

            previous = read_current(root)
            if previous == version_id:
                return PublishOutcome(STATE_UNCHANGED, version_id, version_anterior=previous)
            try:
                _write_current(root, version_id)
            except Exception as exc:
                failure = [_error("actualizacion_de_current_fallida", redact_paths(exc))]
                _append_event(root, {"evento": "restauracion_fallida", "fecha": stamp, "version": version_id,
                                     "anterior": previous, "errores": [e.code for e in failure], "motivo": text})
                return PublishOutcome(STATE_FAILED, version_id, stamp, previous, failure)
            outcome = PublishOutcome(STATE_RESTORED, version_id, stamp, previous)
            try:
                _append_event(root, {"evento": "restauracion", "fecha": stamp, "version": version_id,
                                     "anterior": previous, "motivo": text})
            except Exception as exc:
                outcome.advertencias.append(f"CURRENT se actualizó, pero no se pudo registrar en {LOG_FILE}: {redact_paths(exc)}")
            return outcome
    except LockTimeout as exc:
        return PublishOutcome(STATE_FAILED, version_id, errores=[_error("bloqueo_ocupado", str(exc))])
    except Exception as exc:
        return PublishOutcome(STATE_FAILED, version_id, errores=[_error("error_de_escritura", redact_paths(exc))])
