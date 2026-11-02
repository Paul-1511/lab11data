# Observatorio de Turismo Sostenible — Guatemala

Tablero interactivo del **Laboratorio 10 (Visualización Interactiva)**, CC3066 Data Science (UVG).
Reutiliza los datos y los modelos de pronóstico (redes LSTM) del **Laboratorio 2**; aquí no se
entrena ningún modelo nuevo, solo se leen los resultados guardados en `Lab2DS/outputs/`.

## Requisitos

- Python 3.10+
- El proyecto del Laboratorio 2 en la subcarpeta `Lab2DS/` (incluye el Excel de datos y
  `outputs/` con modelos, predicciones y métricas).

## Instalación y ejecución

```bash
pip install -r requirements.txt
streamlit run app.py
```

Luego abra el navegador en la URL que indica Streamlit (por defecto http://localhost:8501).

## Estructura

```
app.py                      Punto de entrada y navegación (4 páginas)
dashboard/
  loaders.py                Reconstrucción de series + carga de predicciones/métricas del Lab 2
  theme.py                  Paleta de colores, tipografía y plantilla de gráficos
  ui.py                     Datos cacheados y componentes de interfaz reutilizables
  views/
    inicio.py               Contexto, lectura rápida y panorama general
    explorar.py             Exploración de datos (tendencia, estacionalidad, composición, origen)
    modelos.py              Comparación y desempeño de 3 modelos de pronóstico
    perfil.py               Perfil de las series (análisis catch22)
.streamlit/config.toml      Tema de la aplicación
DISENO.md                   Documento de diseño (entregable)
```

## Páginas y visualizaciones

1. **Inicio** — contexto, tarjetas de lectura rápida y panorama del total de visitantes.
2. **Explorar los datos** — serie mensual (con zoom), estacionalidad (mapa de calor),
   composición por vía de ingreso, y el par **enlazado** origen → detalle en el tiempo.
3. **Modelos y pronósticos** — comparación de 3 modelos (LSTM simple, apilado y global catch22),
   pronóstico vs. observado, error en el tiempo y búsqueda de hiperparámetros. El selector de
   modelo **enlaza** cuatro gráficos a la vez.
4. **Perfil de series** — mapa de similitud (PCA de catch22) y distancia entre series.

> Nota: los modelos se evalúan sobre la ventana abril 2021 – junio 2026, que incluye la caída por
> COVID-19 y la recuperación. Es un período atípico, por lo que los errores son altos y los
> pronósticos deben leerse como referencia de tendencia, no como cifras exactas.


---

# Laboratorio 11 — Pipeline reproducible (MLOps) · AVANCE

Documentación: [`docs/ALCANCE.md`](docs/ALCANCE.md) · [`docs/CONTROLES_CALIDAD.md`](docs/CONTROLES_CALIDAD.md) · [`docs/diagrama_flujo.png`](docs/diagrama_flujo.png)

## Ejecutar el pipeline (un comando)

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Con datos institucionales: copie Base_Migracion_2009-2026jun.xlsx (la del Lab 2) a data/raw/
# (está en .gitignore) o indique la ruta con --data. La lectura del Excel tarda ~30 s.
python -m pipeline.run --config config/config.yaml

# Sin datos institucionales (sintéticos):
python scripts/make_synthetic_data.py
python -m pipeline.run --data data/synthetic/base_sintetica.csv
```

Código de salida: `0` completado, `2` control crítico fallido (nada se publica), `1` error inesperado.
Cada ejecución crea `artifacts/runs/<run_id>/` con `quality_report.md|json`, `run_log.json`, `config_snapshot.yaml` y la serie preparada.

## Estimación y aceptación

Después de pasar los controles, el flujo calcula una estimación estacional
ingenua, la evalúa sin usar datos futuros y la compara con la referencia de
último valor observado. Genera `evaluacion_temporal.csv`,
`estimaciones_candidatas.csv` y `decision_aceptacion.json` en la carpeta de la
ejecución. La decisión aceptada autoriza la publicación; todavía no reemplaza
ninguna versión en `published/`.

La regla y el horizonte están en `config/config.yaml`. La metodología completa
está en [`docs/EVALUACION_Y_ACEPTACION.md`](docs/EVALUACION_Y_ACEPTACION.md).

## Pruebas

```bash
python -m pytest -q
```

Cubren cada control (caso correcto y caso fallido), la población de referencia y la clave real y verifican que, ante cada falla inyectada, el pipeline se detiene sin publicar.
Para provocar una falla a mano: `python scripts/make_synthetic_data.py --fault duplicate_key --out /tmp/malo.csv`; para inyectarla en una copia de los datos reales, añadir `--base RUTA/Base.xlsx`.

## Estructura nueva

```
config/config.yaml        Rutas, períodos, parámetros y umbrales
pipeline/                 config, io, dates, checks (6 controles), prepare, report, run
scripts/make_synthetic_data.py   Datos sintéticos y fallas inyectables
tests/test_pipeline.py    Pruebas automáticas
docs/                     Alcance, controles de calidad y diagrama
```
