"""Genera datos SINTÉTICOS con la misma estructura que la fuente real.

Sirve para probar el pipeline sin datos institucionales y para la prueba
controlada de fallos (ejercicio 7.4 del Laboratorio 11).

    python scripts/make_synthetic_data.py --out data/synthetic/base_sintetica.csv
    python scripts/make_synthetic_data.py --fault duplicate_key --out /tmp/malo.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

FAULTS = ["missing_column", "duplicate_key", "zero_total", "negative_value",
          "missing_month", "invalid_date", "daily_dates_in_value_text"]


def make_synthetic(seed: int = 42, start="2009-01", end="2026-06") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    vias = {"Aérea": 1.0, "Terrestre": 0.8, "Marítima": 0.05}
    regiones = ["América Del Centro", "América Del Norte", "Europa", "Asia"]
    paises = ["País A", "País B", "País C"]
    for d in pd.date_range(start, end, freq="MS"):
        t = (d.year - 2009) + d.month / 12
        season = 1 + 0.25 * np.sin((d.month - 2) / 12 * 2 * np.pi)
        covid = 0.25 if pd.Timestamp("2020-04-01") <= d <= pd.Timestamp("2021-03-01") else 1.0
        for via, wv in vias.items():
            for reg in regiones:
                for pais in paises:
                    base = 40 * wv * (1 + 0.04 * t) * season * covid
                    rows.append({"Año": d.year, "Mes": d.month, "Vía": via, "Región dos": reg,
                                 "Agrupación Residencia": f"{reg[:4]}-{pais}",
                                 "Viajero": float(max(1, rng.poisson(base)))})
    return pd.DataFrame(rows)


def inject_fault(df: pd.DataFrame, fault: str) -> pd.DataFrame:
    df = df.copy()
    if fault == "missing_column":
        return df.drop(columns=["Viajero"])
    if fault == "duplicate_key":
        return pd.concat([df, df.head(5)], ignore_index=True)
    if fault == "zero_total":
        df.loc[(df["Año"] == 2025) & (df["Mes"] == 3), "Viajero"] = 0.0
        return df
    if fault == "negative_value":
        df.loc[df.index[10], "Viajero"] = -7.0
        return df
    if fault == "missing_month":
        return df[~((df["Año"] == 2024) & (df["Mes"] == 5))].reset_index(drop=True)
    if fault == "invalid_date":
        df["Mes"] = df["Mes"].astype(object)
        df.loc[df.index[:1000], "Mes"] = "Mesinvalido"
        return df
    if fault == "daily_dates_in_value_text":
        df["Viajero"] = df["Viajero"].astype(object)
        df.loc[df.index[0], "Viajero"] = "N/D"
        return df
    raise ValueError(f"falla desconocida: {fault}. Opciones: {FAULTS}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic/base_sintetica.csv")
    ap.add_argument("--fault", choices=FAULTS, default=None)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    data = make_synthetic(a.seed)
    if a.fault:
        data = inject_fault(data, a.fault)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(a.out, index=False)
    print(f"escrito {a.out}: {len(data)} filas" + (f" (falla: {a.fault})" if a.fault else ""))
