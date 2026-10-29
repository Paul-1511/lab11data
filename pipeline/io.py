"""Lectura de la fuente y huella digital."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


def fingerprint(path: Path) -> dict:
    """Huella digital (SHA-256) y tamaño del archivo fuente."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": str(path), "sha256": h.hexdigest(), "bytes": path.stat().st_size}


def read_source(path: Path, cfg: dict) -> pd.DataFrame:
    """Lee la fuente tal cual (sin transformar); el tipo se infiere de la extensión."""
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró la fuente de datos: {path}. Indique la ruta en "
            "config/config.yaml (source.path) o con --data."
        )
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=cfg["source"].get("sheet", 0))
    if suffix in (".csv", ".txt"):
        return pd.read_csv(path)
    raise ValueError(f"Formato de fuente no soportado: {suffix}")
