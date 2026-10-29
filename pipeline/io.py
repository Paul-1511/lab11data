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


def apply_filters(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Aplica la población de referencia (config `filters`) y reporta lo que quedó."""
    info = {"filas_fuente": len(df), "filtros": [], "valores_no_encontrados": {}}
    out = df
    for flt in cfg.get("filters") or []:
        col, values = flt["column"], [str(v) for v in flt["values"]]
        present = set(out[col].astype("string").str.strip().dropna().unique())
        not_found = [v for v in values if v not in present]
        if not_found:
            info["valores_no_encontrados"][col] = not_found
        out = out[out[col].astype("string").str.strip().isin(values)]
        info["filtros"].append({"columna": col, "valores": values})
    info["filas_poblacion"] = len(out)
    return out, info
