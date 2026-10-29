"""Preparación: serie mensual SIN rellenar con ceros los meses sin cobertura."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .dates import month_range


def prepare(df: pd.DataFrame, dates: pd.DataFrame, cfg: dict) -> dict[str, pd.DataFrame]:
    """Construye la serie mensual total y los desgloses por dimensión.

    Regla clave: un mes sin filas en la fuente queda con `valor = NaN` y
    `cobertura = False`; nunca se convierte automáticamente en cero.
    """
    unit = cfg["target"]["unit"]
    idx = month_range(cfg["periods"]["expected_start"], cfg["periods"]["expected_end"])

    v = pd.to_numeric(df[cfg["columns"]["value"]], errors="coerce")
    ok = dates["fecha"].notna() & np.isfinite(v) & dates["fecha"].isin(idx)
    base = pd.DataFrame({"fecha": dates.loc[ok, "fecha"], "valor": v[ok]})

    sums = base.groupby("fecha")["valor"].sum()
    total = pd.DataFrame({"fecha": idx})
    total["valor"] = total["fecha"].map(sums)            # NaN donde no hay cobertura
    total["cobertura"] = total["fecha"].isin(sums.index)
    total["unidad"] = unit

    sentinels = [str(s) for s in cfg["columns"].get("missing_sentinels", [])]
    frames = []
    for name, col in cfg["columns"].get("dimensions", {}).items():
        if col not in df.columns:
            continue
        cat = df.loc[ok, col].astype("string").str.strip()
        cat = cat.where(cat.notna() & ~cat.isin(sentinels), "Sin dato")
        g = (
            pd.DataFrame({"fecha": base["fecha"], "categoria": cat, "valor": base["valor"]})
            .groupby(["fecha", "categoria"], as_index=False)["valor"].sum()
        )
        g.insert(0, "dimension", name)
        g["unidad"] = unit
        frames.append(g)
    by_dim = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["dimension", "fecha", "categoria", "valor", "unidad"])
    )
    return {"total": total, "por_dimension": by_dim}


def save_prepared(prepared: dict[str, pd.DataFrame], run_dir: Path) -> dict[str, str]:
    paths = {}
    for name, frame in prepared.items():
        path = run_dir / f"serie_mensual_{name}.csv"
        frame.to_csv(path, index=False, date_format="%Y-%m-%d")
        paths[name] = str(path)
    return paths
