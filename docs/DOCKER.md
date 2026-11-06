# Docker Compose: pipeline, tablero y MLflow

**Estado.** Según los resultados que reportó el equipo, la imagen se construye, MLflow y Streamlit funcionan, el pipeline se ejecuta dentro de Docker, las estimaciones aprobadas se publican y `CURRENT`, el tablero y MLflow conservan sus datos al recrear los contenedores. La trazabilidad del commit (sección «Trazabilidad del commit de Git») es posterior a esa verificación y **no está verificada** hasta ejecutar los comandos de `README.md`.

## Arquitectura

Una sola imagen (`lab11-observatorio:local`, Python 3.11) con tres usos:

| Servicio | Qué hace | Puerto en el equipo | Se inicia con `up` |
|---|---|---|---|
| `mlflow` | Visor de MLflow sobre la base SQLite y los artefactos que escribe el pipeline | `127.0.0.1:5000` | sí |
| `streamlit` | Tablero: lee solo `published/CURRENT`; no monta datos ni ejecuta el pipeline | `127.0.0.1:8501` | sí |
| `pipeline` | Ejecuta `pipeline.run`, `pipeline.restore` o cualquier comando que se le indique | ninguno | no (perfil `pipeline`) |

El pipeline escribe directamente en la base SQLite compartida (`LAB11_MLFLOW_DIR`); el servicio `mlflow` no recibe sus llamadas, solo lee ese mismo almacén. Por eso `/app/mlflow` tiene la misma ruta en ambos contenedores: el experimento guarda la ubicación de sus artefactos como una URI de archivo (`file:///app/mlflow/artifacts`). El tablero no usa MLflow. Los puertos solo se publican en `127.0.0.1` porque MLflow y Streamlit no tienen autenticación.

`docker compose up` nunca estima: el comando por defecto del servicio `pipeline` muestra la ayuda y el servicio tiene un perfil propio. El pipeline se ejecuta con `docker compose run --rm pipeline <comando>`.

## Volúmenes persistentes

| Volumen | Punto de montaje | Contenido | Servicios |
|---|---|---|---|
| `mlflow_data` | `/app/mlflow` | `mlflow.db` y `artifacts/` | `mlflow` (lectura y escritura), `pipeline` |
| `published_data` | `/app/published` | `versions/`, `CURRENT`, `publicaciones.jsonl` | `pipeline` (lectura y escritura), `streamlit` (solo lectura) |
| `runs_data` | `/app/artifacts` | `runs/<run_id>/` con `run_log.json`, `monitoreo.json`, reporte de calidad, etc. | `pipeline` |

Son volúmenes con nombre gestionados por Docker (dentro de la máquina Linux de Docker Desktop), no carpetas de Windows: SQLite y el bloqueo de publicación (`flock`) no son fiables sobre carpetas de Windows montadas. Por diseño `docker compose down` y `docker compose up --force-recreate` no los eliminan; solo `docker compose down -v` o `docker volume rm` los borran. El nombre real lleva el prefijo del proyecto (el nombre de la carpeta, por ejemplo `lab11data_published_data`).

La fuente de datos es un montaje de tipo *bind*, solo lectura, en `/data`, tomado de `LAB11_DATA_DIR` (por defecto `./data`, que contiene `data/synthetic/base_sintetica.csv`).

## Datos

- La imagen no contiene datos: el `Dockerfile` copia solo `app.py`, `.streamlit/config.toml`, `config/`, `pipeline/`, `dashboard/` y `scripts/`, y `.dockerignore` excluye `data`, `*.xlsx`, `*.csv`, resultados, `.env` y `secrets.toml`.
- Demostración con datos sintéticos: `--data /data/synthetic/base_sintetica.csv --source-kind sintetica`.
- Datos institucionales: coloque el Excel en una carpeta del equipo, indique esa carpeta en `LAB11_DATA_DIR` (en `.env`, con barras normales: `D:/datos/institucional`) y ejecute con `--data /data/<nombre del archivo> --source-kind institucional`. El Excel nunca entra en la imagen, el montaje es de solo lectura y los resultados publicados no contienen microdatos.

## Variables de entorno

| Variable | Dónde | Valor | Efecto |
|---|---|---|---|
| `LAB11_DATA_DIR` | `.env` (equipo) | `./data` | Carpeta del equipo que se monta en `/data` |
| `LAB11_STREAMLIT_PORT` | `.env` (equipo) | `8501` | Puerto local del tablero |
| `LAB11_MLFLOW_PORT` | `.env` (equipo) | `5000` | Puerto local de MLflow |
| `LAB11_MLFLOW_DIR` | contenedores | `/app/mlflow` | Carpeta base de la base SQLite y los artefactos |
| `LAB11_PUBLISHED_DIR` | contenedores | `/app/published` | Carpeta de versiones publicadas y `CURRENT` |
| `LAB11_MLFLOW_ENABLED`, `LAB11_PUBLISH_ENABLED`, `LAB11_MONITOR_ENABLED` | servicio `pipeline` | `1` | Registro, publicación y monitoreo activos |

Las rutas de `config/config.yaml` son relativas a `/app`, así que no hay rutas de Windows dentro de los contenedores. No hay credenciales ni secretos: el proyecto no los usa.

## Relación con la ejecución local

El almacén de Docker es independiente del de la ejecución local (`mlflow/`, `published/`, `artifacts/` del repositorio): son carpetas distintas. No monte esas carpetas en los contenedores ni copie la base de MLflow entre ambos entornos, porque las URI de los artefactos apuntan a `/app/mlflow/...` en un caso y a rutas de Windows en el otro.

## Recuperación

Dentro de Docker se usan los mismos comandos de siempre (`pipeline.restore --list`, `--version`), ejecutados con `docker compose run --rm pipeline ...`. El procedimiento ante fallos es el de `MONITOREO.md`.

## Trazabilidad del commit de Git

La imagen no contiene `.git` ni el ejecutable de Git. Para saber qué código produjo una ejecución, la construcción recibe dos argumentos que el `Dockerfile` convierte en variables de entorno de la imagen:

| Argumento de construcción | Variable en la imagen | Contenido |
|---|---|---|
| `GIT_COMMIT` | `LAB11_GIT_COMMIT` | Identificador completo del commit (40 o 64 caracteres hexadecimales) |
| `GIT_TREE_MODIFIED` | `LAB11_GIT_TREE_MODIFIED` | `true` si había cambios sin confirmar en archivos versionados, `false` si no |

`scripts/docker_build.ps1` los obtiene con `git rev-parse HEAD` y `git status --porcelain --untracked-files=no` y ejecuta `docker compose build`. Si no hay un commit verificable no declara nada. Al ejecutar, `pipeline.run` resuelve el commit en este orden: repositorio Git local (ejecución local); variable `LAB11_GIT_COMMIT` si es un identificador completo; `no_disponible`. Nunca se inventa un identificador y un valor inválido se ignora con una advertencia.

El origen queda en `commit_git_origen` de `run_log.json` y `metadata.json`, y en la etiqueta `git.commit_origen` de MLflow, junto con `commit_git`/`git.commit` y `arbol_git_modificado`/`git.tree_modified`:

| `commit_git_origen` | Significado |
|---|---|
| `git` | Commit leído de un repositorio local en el momento de la ejecución |
| `variable_de_entorno` | Commit declarado al construir la imagen |
| `no_disponible` | No hay commit; `commit_git` es `null` y la etiqueta `git.commit` no existe |

Limitaciones:
- Es una declaración de quien construye: si se construye con `docker compose build` sin el script, o con una variable `GIT_COMMIT` equivocada, la imagen registra `no_disponible` o ese valor.
- El commit identifica el código solo si `arbol_git_modificado` es `false`. Con `true` la imagen contiene cambios sin confirmar y el commit es solo una referencia; con `null` se desconoce. Los archivos nuevos sin seguimiento no se detectan.
- Reconstruir la imagen con otro commit no modifica las ejecuciones anteriores.
- El valor queda visible en las capas de la imagen (`docker history`); un identificador de commit no es secreto.

## Verificación pendiente

Falta comprobar: la imagen construida con `scripts/docker_build.ps1` y el commit visible en `run_log.json`, `metadata.json` y MLflow; y una copia limpia del repositorio funcionando. Los comandos están en `README.md`.

## Limitaciones y riesgos

- Las dependencias sin versión fija en `requirements.txt` (por ejemplo `mlflow>=2.14`) se resuelven al construir la imagen y pueden diferir de las del entorno local; para fijarlas hace falta generar un archivo de versiones congeladas.
- El commit registrado dentro de Docker es una **declaración** hecha al construir la imagen, no una verificación: ver la sección siguiente.
- Ejecutar el pipeline mientras el visor de MLflow lee la base SQLite comparte un mismo archivo; los accesos son breves, pero un bloqueo ocasional es posible.
- Si `LAB11_DATA_DIR` apunta a una carpeta inexistente, Docker la crea vacía.
- El tablero y MLflow no tienen autenticación: no los exponga fuera del equipo.
- La clasificación automática de la muestra sintética por huella SHA-256 puede fallar si Git cambió los saltos de línea del CSV; por eso los comandos declaran `--source-kind`.
