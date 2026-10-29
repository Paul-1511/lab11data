"""Genera datos SINTÉTICOS con la misma estructura que la hoja «Datos» de la fuente real,
e inyecta fallas para la prueba controlada (ejercicio 7.4 del Laboratorio 11).

    python scripts/make_synthetic_data.py --out data/synthetic/base_sintetica.csv
    python scripts/make_synthetic_data.py --fault duplicate_key --out /tmp/malo.csv
    # Falla sobre una COPIA de los datos reales (CSV o Excel; la copia no se versiona):
    python scripts/make_synthetic_data.py --base data/raw/Base.xlsx --fault zero_total --out /tmp/copia_mala.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

FAULTS = ["missing_column", "duplicate_key", "zero_total", "negative_value",
          "missing_month", "invalid_date", "daily_dates_in_value_text"]
VISITORS = ["Turista", "Excursionista"]
MES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def make_synthetic(seed: int = 42, start="2009-01", end="2026-06") -> pd.DataFrame:
    """Una fila por mes × vía × frontera × país × agrupación × tipo de viajero (como la fuente)."""
    rng = np.random.default_rng(seed)
    rows = []
    vias = {"Aérea": ("01 La Aurora", 1.0), "Terrestre": ("07 Valle Nuevo", 0.8), "Marítima": ("02 Puerto Barrios", 0.05)}
    regiones = {"América Del Centro": "Resto de Centro América", "América Del Norte": "Estados Unidos de América",
                "Europa": "Europa", "Asia": "Resto del Mundo"}
    tipos = {"Turista": 1.0, "Excursionista": 0.3, "Viajero": 2.0}   # «Viajero» NO es visitante
    for d in pd.date_range(start, end, freq="MS"):
        t = (d.year - 2009) + d.month / 12
        season = 1 + 0.25 * np.sin((d.month - 2) / 12 * 2 * np.pi)
        covid = 0.25 if pd.Timestamp("2020-04-01") <= d <= pd.Timestamp("2021-03-01") else 1.0
        for via, (frontera, wv) in vias.items():
            for reg, agrup in regiones.items():
                for k in range(3):
                    for tipo, wt in tipos.items():
                        base = 40 * wv * wt * (1 + 0.04 * t) * season * covid
                        rows.append({"Año": d.year, "Mes cod": d.month, "Mes": MES[d.month - 1], "Vía": via,
                                     "Frontera": frontera, "País": f"{reg[:5]}-País{k}", "Región dos": reg,
                                     "Agrupación Residencia": agrup, "Tipo de Viajero": tipo,
                                     "Viajero": float(max(1, rng.poisson(base)))})
            if via == "Marítima" and d.year <= 2022:     # cruceristas: solo hasta 2022, como en la fuente
                rows.append({"Año": d.year, "Mes cod": d.month, "Mes": MES[d.month - 1], "Vía": via,
                             "Frontera": frontera, "País": "Crucero", "Región dos": "Cruceristas",
                             "Agrupación Residencia": "Resto del Mundo", "Tipo de Viajero": "Cruceristas",
                             "Viajero": float(rng.poisson(30))})
    return pd.DataFrame(rows)


def inject_fault(df: pd.DataFrame, fault: str) -> pd.DataFrame:
    df = df.copy()
    visitor = df["Tipo de Viajero"].isin(VISITORS) if "Tipo de Viajero" in df.columns else None
    if fault == "missing_column":
        return df.drop(columns=["Viajero"])
    if fault == "duplicate_key":
        return pd.concat([df, df.head(5)], ignore_index=True)
    if fault == "zero_total":                      # total mensual de visitantes = 0
        df.loc[visitor & (df["Año"] == 2025) & (df["Mes cod"] == 3), "Viajero"] = 0.0
        return df
    if fault == "negative_value":
        df.loc[df.index[visitor.to_numpy().argmax()], "Viajero"] = -7.0
        return df
    if fault == "missing_month":
        return df[~((df["Año"] == 2024) & (df["Mes cod"] == 5))].reset_index(drop=True)
    if fault == "invalid_date":
        df["Mes cod"] = df["Mes cod"].astype(object)
        df.loc[df.index[:1000], "Mes cod"] = "Mesinvalido"
        return df
    if fault == "daily_dates_in_value_text":
        df["Viajero"] = df["Viajero"].astype(object)
        df.loc[df.index[0], "Viajero"] = "N/D"
        return df
    raise ValueError(f"falla desconocida: {fault}. Opciones: {FAULTS}")


def _read_base(path: str) -> pd.DataFrame:
    p = Path(path)
    return pd.read_excel(p, sheet_name="Datos") if p.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(p)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic/base_sintetica.csv")
    ap.add_argument("--fault", choices=FAULTS, default=None)
    ap.add_argument("--base", default=None, help="datos sobre los que inyectar la falla (copia de los reales)")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    data = _read_base(a.base) if a.base else make_synthetic(a.seed)
    if a.fault:
        data = inject_fault(data, a.fault)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(a.out, index=False)
    print(f"escrito {a.out}: {len(data)} filas" + (f" (falla: {a.fault})" if a.fault else ""))
