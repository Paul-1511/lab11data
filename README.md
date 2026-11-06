# Observatorio de Turismo Sostenible — Guatemala · Laboratorio 11 (MLOps)

Pipeline reproducible que estima el ingreso mensual de visitantes no residentes a Guatemala, lo evalúa,
lo registra en MLflow, lo publica en versiones inmutables y lo muestra en un tablero Streamlit que lee
únicamente la versión aprobada y vigente.

Documentación: [`docs/ALCANCE.md`](docs/ALCANCE.md) · [`docs/CONTROLES_CALIDAD.md`](docs/CONTROLES_CALIDAD.md) · [`docs/TABLERO.md`](docs/TABLERO.md) · [`docs/MONITOREO.md`](docs/MONITOREO.md) · [`docs/DOCKER.md`](docs/DOCKER.md) · [`docs/diagrama_flujo.png`](docs/diagrama_flujo.png)

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

## Monitoreo y recuperación ante fallos

Cada ejecución evalúa señales de actualización (fuente desactualizada frente a horizonte incompleto, meses requeridos ausentes),
de calidad (faltantes, claves duplicadas y cobertura frente a la versión vigente) y de resultados (estimaciones negativas o no
finitas, variaciones mensuales altas). Las alertas se informan en la consola, en `run_log.json`, `monitoreo.json` y MLflow
(`monitoreo.*`); no cambian los códigos de salida ni la decisión de publicar. Ante un fallo crítico `CURRENT` no cambia y el
tablero conserva la versión anterior. Umbrales, interpretación y procedimiento de recuperación en [`docs/MONITOREO.md`](docs/MONITOREO.md).
`LAB11_MONITOR_ENABLED=0` desactiva el monitoreo.

## Docker Compose

Tres servicios con una sola imagen: `mlflow` (puerto 5000), `streamlit` (puerto 8501) y `pipeline` (solo bajo demanda).
Las versiones publicadas, los registros de ejecuciones y MLflow viven en volúmenes de Docker; los datos se montan desde
fuera, en solo lectura, y no entran en la imagen. Arquitectura, volúmenes y variables en [`docs/DOCKER.md`](docs/DOCKER.md).
Construcción, servicios, publicación y persistencia fueron comprobados por el equipo; el registro del commit de Git (paso 3) está
escrito pero **sin verificar** hasta ejecutar los comandos de su sección.

Convención: los bloques están en **PowerShell, en el equipo**. En `docker compose run --rm pipeline <comando>`, lo que va
después de `pipeline` se ejecuta **dentro del contenedor** (Linux, carpeta `/app`, rutas con `/`).

**1. Requisitos previos.** Docker Desktop para Windows con el motor WSL 2 iniciado.

```powershell
docker --version
docker compose version
```

**2. Configuración inicial** (opcional; sin `.env` se usan `./data` y los puertos 8501 y 5000).

```powershell
cd C:\Users\mjyee\Downloads\DataScience\lab11data
Copy-Item .env.example .env
```

**3. Construcción de la imagen.** El script declara a la imagen el commit de Git del código que empaqueta (la imagen no
contiene `.git`) y luego ejecuta `docker compose build`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker_build.ps1
```

`docker compose build` a secas también funciona, pero entonces la imagen registra el commit como `no_disponible`. El commit
queda en `run_log.json`, `metadata.json` (`commit_git`, `commit_git_origen`, `arbol_git_modificado`) y en las etiquetas
`git.*` de MLflow; si `arbol_git_modificado` es `true`, la imagen incluye cambios sin confirmar. Detalle y limitaciones en
[`docs/DOCKER.md`](docs/DOCKER.md). Para comprobarlo después de ejecutar el pipeline:

```powershell
docker compose run --rm pipeline sh -c "printenv LAB11_GIT_COMMIT LAB11_GIT_TREE_MODIFIED"
docker compose run --rm pipeline cat artifacts/runs/RUN_ID/run_log.json
```

**4. Inicio de servicios** (MLflow y tablero; no se ejecuta el pipeline).

```powershell
docker compose up -d
docker compose ps
```

**5. Ejecución del pipeline con datos sintéticos** (el comando después de `pipeline` corre en el contenedor).

```powershell
docker compose run --rm pipeline python -m pipeline.run --data /data/synthetic/base_sintetica.csv --source-kind sintetica
```

Con datos institucionales: en `.env`, `LAB11_DATA_DIR=D:/ruta/a/la/carpeta/del/excel` (barras normales), y

```powershell
docker compose up -d --force-recreate
docker compose run --rm pipeline python -m pipeline.run --data /data/Base_Migracion_2009-2026jun.xlsx --source-kind institucional
```

**6. Tablero Streamlit:** <http://localhost:8501>. **7. MLflow:** <http://localhost:5000> (experimento `observatorio_turismo_guatemala`).

**8. Registros.**

```powershell
docker compose logs --tail 100 streamlit
docker compose logs --tail 100 mlflow
docker compose run --rm pipeline ls artifacts/runs
docker compose run --rm pipeline cat artifacts/runs/RUN_ID/monitoreo.json
docker compose run --rm pipeline cat published/CURRENT
docker compose run --rm pipeline cat published/publicaciones.jsonl
```

`RUN_ID` es uno de los identificadores que muestra `ls artifacts/runs`.

**9. Restauración de versiones.**

```powershell
docker compose run --rm pipeline python -m pipeline.restore --list
docker compose run --rm pipeline python -m pipeline.restore --version RUN_ID --reason "motivo"
```

**10. Detención** (conserva los volúmenes).

```powershell
docker compose stop
docker compose down
```

**11. Recreación de contenedores** (conserva los volúmenes).

```powershell
docker compose down
docker compose up -d
docker compose up -d --force-recreate
```

**12. Verificación de persistencia.** Anote `CURRENT` y las versiones, recree los contenedores y compare.

```powershell
docker compose run --rm pipeline cat published/CURRENT
docker compose run --rm pipeline python -m pipeline.restore --list
docker compose down
docker compose up -d
docker compose run --rm pipeline cat published/CURRENT
docker compose run --rm pipeline python -m pipeline.restore --list
docker volume ls --filter name=_data
```

Deben coincidir; el tablero y MLflow deben seguir mostrando la versión y las ejecuciones anteriores.
**No use `docker compose down -v`** salvo que quiera borrar versiones, registros y MLflow.

**13. Ejecución desde una copia limpia.** Requiere que los archivos de Docker estén confirmados en Git. Detenga antes el
entorno actual para liberar los puertos; la copia obtiene volúmenes nuevos y vacíos (se nombran por su carpeta).

```powershell
cd C:\Users\mjyee\Downloads\DataScience\lab11data
docker compose down
git clone C:\Users\mjyee\Downloads\DataScience\lab11data C:\Users\mjyee\Downloads\DataScience\lab11-limpio
cd C:\Users\mjyee\Downloads\DataScience\lab11-limpio
powershell -ExecutionPolicy Bypass -File .\scripts\docker_build.ps1
docker compose up -d
docker compose run --rm pipeline python -m pipeline.run --data /data/synthetic/base_sintetica.csv --source-kind sintetica
```

Abra <http://localhost:8501> y <http://localhost:5000>. Al terminar: `docker compose down -v` dentro de la copia elimina solo sus volúmenes.

## Diagrama de flujo

La fuente es `docs/flujo.dot`. El `.svg` y el `.png` se regeneran con Graphviz (`dot -V` comprueba que está instalado):

```powershell
dot -Tsvg docs/flujo.dot -o docs/diagrama_flujo.svg
dot -Tpng -Gdpi=150 docs/flujo.dot -o docs/diagrama_flujo.png
```

`tests/test_docker.py` comprueba que el `.svg` ya no mencione la estructura anterior de publicación.

## Pruebas

```bash
python -m pytest -q
```

Cubren cada control (caso correcto y caso fallido), la población de referencia y la clave real y verifican que, ante cada falla inyectada, el pipeline se detiene sin publicar.
Para provocar una falla a mano: `python scripts/make_synthetic_data.py --fault duplicate_key --out /tmp/malo.csv`; para inyectarla en una copia de los datos reales, añadir `--base RUTA/Base.xlsx`.
`tests/test_dashboard.py` prueba el cargador y las páginas del tablero con versiones sintéticas en carpetas temporales.
`tests/test_docker.py` revisa solo los archivos de configuración de Docker; no ejecuta Docker.
`tests/test_traceability.py` prueba la resolución y el registro del commit de Git, con y sin repositorio.
`tests/test_monitoring.py` prueba las alertas y el ciclo de fallo controlado, conservación de `CURRENT`, corrección y restauración.

## Estructura

```
app.py                    Punto de entrada y navegación del tablero (3 páginas)
dashboard/
  published.py            Lectura y validación de la versión vigente (sin Streamlit)
  charts.py               Figuras Plotly de lo publicado
  theme.py, ui.py         Tema adaptado del Lab 10 y componentes de interfaz
  views/                  inicio.py, estimaciones.py, evaluacion.py
.streamlit/config.toml    Tema de la aplicación
Dockerfile, docker-compose.yml, .dockerignore, .env.example   Entorno Docker (sin verificar)
config/config.yaml        Rutas, períodos, parámetros y umbrales
pipeline/                 config, io, dates, checks (6 controles), prepare, evaluation, contract,
                          metadata, tracking, monitoring, publish, restore, report, run
scripts/make_synthetic_data.py   Datos sintéticos y fallas inyectables
tests/                    Pruebas automáticas
docs/                     Alcance, controles, contrato, MLflow, publicación, tablero y diagrama
```
