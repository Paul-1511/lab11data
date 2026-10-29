"""Carga y validación del archivo de configuración."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_KEYS = ["run", "target", "source", "columns", "periods", "quality"]


class ConfigError(Exception):
    """La configuración es inválida o está incompleta."""


def resolve(path: str | Path) -> Path:
    """Rutas relativas se interpretan desde la raíz del repositorio."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_config(path: str | Path) -> dict:
    p = resolve(path)
    if not p.exists():
        raise ConfigError(f"No existe el archivo de configuración: {p}")
    text = p.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text) or {}
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ConfigError(f"Faltan secciones en la configuración: {missing}")
    for name in ("expected_start", "expected_end"):
        if name not in cfg["periods"]:
            raise ConfigError(f"periods.{name} es obligatorio")
    cfg["_meta"] = {
        "path": str(p),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    return cfg
