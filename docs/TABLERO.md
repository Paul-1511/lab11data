# Tablero Streamlit

Implementado en `app.py` y `dashboard/`. Muestra únicamente la versión aprobada y vigente que publica el pipeline (`PUBLICACION.md`). El monitoreo y Docker todavía no están implementados.

## Iniciar

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Dependencias: `streamlit`, `plotly`, `pandas` y `pyyaml` (`requirements.txt`). El tablero no requiere MLflow, `openpyxl` ni ninguna librería de modelos. Sin una versión publicada abre igualmente y muestra el estado correspondiente; para tener una: `python -m pipeline.run --data data/synthetic/base_sintetica.csv --source-kind sintetica`.

## Páginas

| Página | Contenido |
|---|---|
| Inicio | Nombre del observatorio, destino, variable y unidad, último período y valor observados, fecha de publicación, versión, tipo de fuente y resumen de las estimaciones publicadas |
| Estimaciones | Serie temporal con observados (gris oscuro) y estimados (turquesa discontinuo), horizonte estimado sombreado, unidad en el eje y en el texto emergente, y selector de período |
| Evaluación | Decisión de aceptación y su regla, MAE y RMSE del procedimiento y de la referencia, meses y período evaluados, explicación de las métricas, limitaciones metodológicas, advertencias y controles de calidad |

Todo lo que se muestra sale de `estimaciones.csv`, `metadata.json` y `decision_aceptacion.json` de la versión vigente. No se calculan métricas ni estimaciones adicionales; un valor ausente se muestra como «No disponible».

## Lectura de versiones aprobadas

`dashboard/published.py` (sin Streamlit) implementa `load_current(root)`:

1. lee `published/CURRENT` (o `LAB11_PUBLISHED_DIR`) y obtiene el identificador de la versión;
2. localiza `versions/<run_id>/` y ninguna otra versión;
3. valida con `pipeline.publish.verify_directory`, la misma función que usan la publicación y la restauración: archivos esperados y sin extras, huellas SHA-256 del manifest, decisión favorable, seis controles de calidad, contrato de estimaciones y tipo de fuente permitido;
4. comprueba que los metadatos tengan los campos que se muestran y que `publicacion.version_id` coincida con la versión;
5. carga `estimaciones.csv` (observados y estimados) y `metadata.json`.

Nunca lanza excepciones: devuelve `ok`, `sin_publicacion` o `error`. No escribe en `published/`, no importa `pipeline.run` ni los módulos de estimación y no lee `estimaciones_candidatas.csv` ni el Excel institucional. La interfaz guarda el resultado en caché por el contenido de `CURRENT` (una nueva versión se carga de inmediato) y lo revalida cada 30 segundos; cambiar filtros solo acota lo que se dibuja.

## Estados sin cifras

| Situación | Qué muestra el tablero |
|---|---|
| `CURRENT` no existe | «Aún no hay resultados aprobados para mostrar», sin cifras ni datos de ejemplo |
| `CURRENT` con identificador inválido, versión inexistente, archivos dañados, ajenos o faltantes, huella distinta, versión no aprobada o metadatos incompletos | Error comprensible con las razones en lenguaje claro y la indicación de revisar con `python -m pipeline.restore --list`; sin rutas locales ni detalles internos |

La navegación entre páginas sigue funcionando en ambos casos.

## Fuente sintética

Si `fuente.tipo` es `sintetica`, todas las páginas muestran un aviso ámbar, las series se rotulan «sintético» y la página Evaluación indica que las métricas no son una evaluación institucional. Una versión sintética que sea la vigente reemplaza a una institucional en `CURRENT`; el aviso es la única barrera visual.

## Seguridad

- Solo lectura de la versión vigente y validada antes de mostrar cifras.
- Sin descargas ni tablas con exportación: se usan tablas estáticas y no hay botones de descarga. No se publican microdatos.
- Las rutas locales no aparecen en la interfaz; las cadenas que vienen de los metadatos se escapan antes de insertarse en HTML.
- Sin controles que ejecuten código del pipeline.

## Relación con MLflow

El tablero no consulta MLflow. La relación es el `run_id`: es el identificador de la ejecución del pipeline, de la versión publicada y la etiqueta `pipeline.run_id` de la ejecución en MLflow, donde también quedan las etiquetas `publicacion.*`.

## Origen del diseño

Se adaptaron del Laboratorio 10 la marca, neutros, tipografía, plantilla de gráficos, formatos numéricos, CSS de tarjetas y los componentes `hero`, `note`, `empty` y `plotly`, y `.streamlit/config.toml`. No se copiaron cargadores, modelos LSTM, perfiles catch22 ni resultados del Laboratorio 2.

## Limitaciones

- Muestra una sola versión (la vigente); no compara versiones.
- Un solo destino, a nivel nacional, y la serie total mensual: no hay desgloses.
- La evaluación es una ventana única con origen móvil; las métricas son las publicadas por el pipeline.
- La estimación es estacional ingenua: no modela tendencia ni incertidumbre, por lo que no hay intervalos.
- No hay autenticación.
- Los avisos de calidad de datos del monitoreo futuro no existen todavía.

## Pruebas

```powershell
python -m pytest tests/test_dashboard.py -q
```

Usan carpetas temporales con versiones reales publicadas a partir de datos sintéticos. Las pruebas de páginas usan `streamlit.testing` y se omiten si Streamlit no está instalado.
