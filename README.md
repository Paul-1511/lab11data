# Observatorio de Turismo Sostenible — Guatemala · Laboratorio 11 (MLOps)

Pipeline reproducible que estima el ingreso mensual de visitantes no residentes a Guatemala, lo evalúa,
lo registra en MLflow, lo publica en versiones inmutables y lo muestra en un tablero Streamlit que lee
únicamente la versión aprobada y vigente.

Documentación: [`docs/ALCANCE.md`](docs/ALCANCE.md) · [`docs/CONTROLES_CALIDAD.md`](docs/CONTROLES_CALIDAD.md) · [`docs/TABLERO.md`](docs/TABLERO.md) · [`docs/diagrama_flujo.png`](docs/diagrama_flujo.png)

## Tablero

Tres páginas: **Inicio**, **Estimaciones** y **Evaluación**. Muestran solo la versión que señala `published/CURRENT`
después de validar su integridad y su aprobación; no ejecutan el pipeline, no calculan estimaciones al cambiar
filtros, no usan modelos de otros laboratorios y no leen el Excel institucional. Si no hay versión aprobada, o la
vigente está dañada, muestran un mensaje y ninguna cifra. Una versión sintética se identifica en todas las páginas.
Detalle en [`docs/TABLERO.md`](docs/TABLERO.md).

```bash
pip install -r requirements.txt
python -m pipeline.run --data data/synthetic/base_sintetica.csv --source-kind sintetica   # publica una versión
streamlit run app.py
```

Luego abra la URL que indica Streamlit (por defecto http://localhost:8501). El diseño visual (marca turquesa,
tipografía, tarjetas y formatos) se adaptó del tablero del Laboratorio 10; los componentes que dependían de los
modelos del Laboratorio 2 no se incluyeron.

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

Código de salida: `0` completado, `2` control crítico fallido (nada se publica), `1` error inesperado (incluye un fallo técnico al publicar).
Cada ejecución crea `artifacts/runs/<run_id>/` con `quality_report.md|json`, `run_log.json`, `config_snapshot.yaml` y la serie preparada.

## Estimación y aceptación

Después de pasar los controles, el flujo calcula una estimación estacional
ingenua, la evalúa sin usar datos futuros y la compara con la referencia de
último valor observado. Genera `evaluacion_temporal.csv`,
`estimaciones_candidatas.csv` y `decision_aceptacion.json` en la carpeta de la
ejecución. La decisión aceptada autoriza la publicación controlada (ver más abajo);
un candidato rechazado nunca modifica `published/`.

La regla y el horizonte están en `config/config.yaml`. La metodología completa
está en [`docs/EVALUACION_Y_ACEPTACION.md`](docs/EVALUACION_Y_ACEPTACION.md).

## Contrato de resultados y metadatos

Cada ejecución sin fallos críticos escribe además `metadata.json`, y la regla de
aceptación exige que las estimaciones cumplan el contrato de
[`docs/CONTRATO_RESULTADOS.md`](docs/CONTRATO_RESULTADOS.md). La fuente se
clasifica como sintética, institucional o no declarada; al usar `--data` con un
archivo propio, declárela con `--source-kind sintetica` o `--source-kind institucional`:

```bash
python -m pipeline.run --data data/synthetic/base_sintetica.csv --source-kind sintetica
```

Los resultados de una fuente sintética o no declarada no deben presentarse como
cifras institucionales.

## Registro en MLflow

Cada ejecución (aceptada, rechazada, detenida por calidad o con error) se registra en MLflow Tracking local:
base SQLite en `mlflow/mlflow.db` y artefactos en `mlflow/artifacts/`, ambos fuera de Git. Configuración,
estados y consulta en [`docs/MLFLOW.md`](docs/MLFLOW.md). El resultado de la publicación también queda en etiquetas `publicacion.*`.
El tablero no consulta MLflow: la trazabilidad se relaciona por el `run_id`, que es también el identificador de la versión publicada.

```bash
python -m pipeline.run --data data/synthetic/base_sintetica.csv --source-kind sintetica
python -m mlflow ui --backend-store-uri sqlite:///mlflow/mlflow.db
```

`LAB11_MLFLOW_DIR` cambia la carpeta base y `LAB11_MLFLOW_ENABLED=0` desactiva el registro.

## Publicación controlada y restauración

Una ejecución aceptada se publica como versión inmutable en `published/versions/<run_id>/`
(`estimaciones.csv`, `metadata.json`, `decision_aceptacion.json`, `quality_report.json` y
`manifest.json`). El archivo `published/CURRENT` señala la versión vigente y se reemplaza de forma
atómica; es la única que consulta el tablero. No se publican candidatos
rechazados, con contrato inválido ni de fuentes `no_declarada`; las versiones sintéticas se publican
identificadas como tales. Se conservan todas las versiones. Detalle en [`docs/PUBLICACION.md`](docs/PUBLICACION.md).

```bash
python -m pipeline.restore --list
python -m pipeline.restore --version RUN_ID --reason "motivo"
```

`LAB11_PUBLISHED_DIR` cambia la carpeta de publicación y `LAB11_PUBLISH_ENABLED=0` desactiva la publicación.
El tablero respeta `LAB11_PUBLISHED_DIR`.

## Pruebas

```bash
python -m pytest -q
```

Cubren cada control (caso correcto y caso fallido), la población de referencia y la clave real y verifican que, ante cada falla inyectada, el pipeline se detiene sin publicar.
Para provocar una falla a mano: `python scripts/make_synthetic_data.py --fault duplicate_key --out /tmp/malo.csv`; para inyectarla en una copia de los datos reales, añadir `--base RUTA/Base.xlsx`.
`tests/test_dashboard.py` prueba el cargador y las páginas del tablero con versiones sintéticas en carpetas temporales.

## Estructura

```
app.py                    Punto de entrada y navegación del tablero (3 páginas)
dashboard/
  published.py            Lectura y validación de la versión vigente (sin Streamlit)
  charts.py               Figuras Plotly de lo publicado
  theme.py, ui.py         Tema adaptado del Lab 10 y componentes de interfaz
  views/                  inicio.py, estimaciones.py, evaluacion.py
.streamlit/config.toml    Tema de la aplicación
config/config.yaml        Rutas, períodos, parámetros y umbrales
pipeline/                 config, io, dates, checks (6 controles), prepare, evaluation, contract,
                          metadata, tracking, publish, restore, report, run
scripts/make_synthetic_data.py   Datos sintéticos y fallas inyectables
tests/                    Pruebas automáticas
docs/                     Alcance, controles, contrato, MLflow, publicación, tablero y diagrama
```
