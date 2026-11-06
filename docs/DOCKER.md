# Docker Compose: pipeline, tablero y MLflow

**Estado: configuración creada, sin verificar.** Los archivos `Dockerfile`, `docker-compose.yml`, `.dockerignore` y `.env.example` se probaron solo con pruebas de lectura de archivos (`tests/test_docker.py`). Nada de lo que sigue se considera verificado hasta ejecutar los comandos de `README.md` en Docker Desktop y comprobar la persistencia.

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

## Verificación pendiente

Debe comprobar, en este orden: construcción de la imagen; servicios `mlflow` y `streamlit` saludables; ejecución del pipeline con datos sintéticos; versión visible en el tablero; ejecuciones visibles en MLflow con sus artefactos; los mismos datos tras `down` y `up -d`; y una copia limpia del repositorio funcionando. Los comandos están en `README.md`.

## Limitaciones y riesgos

- Las dependencias sin versión fija en `requirements.txt` (por ejemplo `mlflow>=2.14`) se resuelven al construir la imagen y pueden diferir de las del entorno local; para fijarlas hace falta generar un archivo de versiones congeladas.
- La imagen no incluye Git: el commit no se registra en los metadatos (`commit_git` queda vacío).
- Ejecutar el pipeline mientras el visor de MLflow lee la base SQLite comparte un mismo archivo; los accesos son breves, pero un bloqueo ocasional es posible.
- Si `LAB11_DATA_DIR` apunta a una carpeta inexistente, Docker la crea vacía.
- El tablero y MLflow no tienen autenticación: no los exponga fuera del equipo.
- La clasificación automática de la muestra sintética por huella SHA-256 puede fallar si Git cambió los saltos de línea del CSV; por eso los comandos declaran `--source-kind`.
