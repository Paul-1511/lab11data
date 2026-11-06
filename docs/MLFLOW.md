# Registro de ejecuciones con MLflow Tracking

Cada ejecución del pipeline se registra en MLflow, también las rechazadas por la regla de aceptación, las detenidas por un control crítico y las que terminan con un error inesperado. Este módulo solo registra: no publica resultados, no restaura versiones y no está empaquetado en Docker.

## Dependencia y configuración

`mlflow>=2.14` está en `requirements.txt`. La sección `mlflow` de `config/config.yaml` define el registro:

| Clave | Valor por defecto | Significado |
|---|---|---|
| `enabled` | `true` | activa o desactiva el registro |
| `experiment` | `observatorio_turismo_guatemala` | nombre del experimento |
| `dir` | `mlflow` | carpeta base, relativa a la raíz del repositorio |
| `db_file` | `mlflow.db` | base SQLite dentro de `dir` |
| `artifacts_dir` | `artifacts` | carpeta de artefactos dentro de `dir` |

Dos variables de entorno reemplazan la configuración, por ejemplo para usar un volumen montado más adelante: `LAB11_MLFLOW_DIR` (carpeta base) y `LAB11_MLFLOW_ENABLED` (`0`, `false`, `no` u `off` desactivan el registro).

## Ubicación y persistencia

Con la configuración por defecto:

- base SQLite: `mlflow/mlflow.db`;
- artefactos: `mlflow/artifacts/`;
- ambas rutas están en `.gitignore` y no se versionan.

El experimento y las ejecuciones persisten entre ejecuciones del pipeline mientras se conserve la carpeta `mlflow/`. MLflow guarda en la base la ubicación absoluta de los artefactos del experimento: use la carpeta `mlflow/` desde un solo entorno (local o contenedor), o cree una carpeta distinta en cada uno con `LAB11_MLFLOW_DIR`.

## Ejecución e interfaz

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pipeline.run --data data/synthetic/base_sintetica.csv --source-kind sintetica
python -m mlflow ui --backend-store-uri sqlite:///mlflow/mlflow.db
```

La interfaz queda en http://127.0.0.1:5000. La última línea del pipeline indica la trazabilidad y el identificador de MLflow de la ejecución.

## Identificadores

El `run_id` del pipeline (por ejemplo `20261102T042914Z-6aa805`) y el identificador de MLflow son distintos. Se relacionan con la etiqueta `pipeline.run_id`, y el nombre de la ejecución en MLflow es el `run_id` del pipeline. El identificador de MLflow queda en `run_log.json` (`mlflow.run_id`) y en `metadata.json` (`mlflow`). En la interfaz, el buscador acepta el filtro `tags.pipeline.run_id = "<run_id>"` escribiendo el nombre de la etiqueta entre acentos graves si la versión de MLflow lo exige.

## Estados

El estado técnico de MLflow y la decisión de aceptación son independientes.

| Situación | Estado MLflow | `pipeline.resultado` | `aceptacion.aceptada` | Código de salida |
|---|---|---|---|---|
| Aceptada por la regla | FINISHED | `aceptada` | `true` | 0 |
| Rechazada por la regla | FINISHED | `rechazada` | `false` | 0 |
| Control crítico fallido | FINISHED | `bloqueada_por_calidad` | `no_evaluada` | 2 |
| Error inesperado | FAILED | `error_inesperado` | `no_evaluada` | 1 |
| Proceso interrumpido | KILLED | `interrumpida` | `no_evaluada` | — |

Una ejecución rechazada por desempeño o detenida por calidad no es una excepción técnica: su estado es FINISHED. La etiqueta `trazabilidad` indica si el registro quedó `completa`, `incompleta` (algún paso de MLflow falló), `no_disponible` (no se pudo crear la ejecución) o `deshabilitada`. Si MLflow falla, el pipeline conserva su resultado y su código de salida; los errores quedan en `run_log.json` (`mlflow.errores`) y en una advertencia.

## Qué se registra

- **Parámetros:** destino, variable objetivo, unidad, granularidad, períodos esperado, de ajuste, de evaluación y horizonte, método, período estacional, baseline, semilla y umbrales de aceptación.
- **Etiquetas:** `pipeline.run_id`, versión del procedimiento, commit de Git (`git.commit`, ausente si no está disponible), `git.commit_origen` (`git`, `variable_de_entorno` o `no_disponible`), `git.tree_modified`, huella de la configuración, `inicio_utc`, `fin_utc`, resultado y código de salida, tipo de fuente, criterio de clasificación, nombre del archivo, SHA-256, tamaño y filas de la fuente, decisión y motivos de aceptación, estado global y de cada control (`calidad.<id>`), advertencias, errores críticos y `trazabilidad`.
- **Métricas:** `mae_estimacion`, `rmse_estimacion`, `mae_referencia`, `rmse_referencia`, `razon_mae`, `meses_evaluados`, `meses_solicitados` y el número de controles por estado. Solo aparecen cuando el pipeline las calculó.
- **Artefactos, si existen:** `quality_report.json`, `quality_report.md`, `evaluacion_temporal.csv`, `estimaciones_candidatas.csv`, `decision_aceptacion.json`, `metadata.json` y `config_snapshot.yaml`.

No se registran el archivo fuente, las series mensuales ni `run_log.json` (guarda rutas locales). Los textos de error pasan por una función que reemplaza las rutas absolutas por el nombre del archivo.

## Consulta

En la interfaz, la tabla de ejecuciones permite filtrar por las etiquetas anteriores y comparar métricas. Cada ejecución muestra sus parámetros, etiquetas, métricas y los artefactos con su contenido.

## Pruebas

`tests/conftest.py` desactiva el registro durante las pruebas del pipeline. `tests/test_tracking.py` lo activa con una base y artefactos en carpetas temporales; no requiere servicios externos.
