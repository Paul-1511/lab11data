# Contrato de resultados, validación y metadatos

Este documento describe lo implementado en esta etapa: el contrato de las estimaciones candidatas, su validador y el archivo `metadata.json` de cada ejecución. MLflow, la publicación controlada, la restauración, el monitoreo y Docker no están implementados; los campos que dependerán de ellos quedan en `null`.

## Contrato de estimaciones

Archivo de cada ejecución: `artifacts/runs/<run_id>/estimaciones_candidatas.csv`. Lo genera `pipeline.evaluation.candidate_results` y su forma está definida en `pipeline/contract.py`.

| Columna | Tipo | Regla |
|---|---|---|
| `destino` | texto | no vacío; un único valor, igual a `target.destination` |
| `periodo` | fecha `AAAA-MM-01` | mensual; sin repetirse para un mismo destino |
| `tipo` | texto | `observado` o `estimado` |
| `valor` | decimal | finito y no negativo |
| `unidad` | texto | un único valor, igual a `target.unit` |
| `run_id` | texto | un único valor; igual al de la ejecución |
| `fecha_generacion` | ISO 8601 UTC | un único valor |
| `version_procedimiento` | texto | un único valor; igual a `pipeline.__version__` |
| `metodo` | texto | `seasonal_naive` |

Los períodos observados son anteriores a todos los estimados, y los estimados cubren exactamente el horizonte `periods.forecast` de `config/config.yaml`, sin meses repetidos, faltantes ni sobrantes. El archivo usa UTF-8, separador coma y punto decimal.

## Validaciones

`pipeline.contract.validate_estimates(candidates, cfg, run_id=None, version=None)` devuelve un `ContractResult` con la lista de errores. No corrige ni descarta filas. Cada error tiene `codigo`, `mensaje`, `columna` y `detalle`.

| Código | Qué detecta |
|---|---|
| `tipo_invalido`, `sin_filas` | la entrada no es una tabla, o no tiene filas |
| `columna_faltante` | falta una columna obligatoria |
| `campo_vacio` | valores nulos o en blanco en una columna obligatoria |
| `fecha_invalida`, `periodo_no_mensual` | `periodo` no es una fecha `AAAA-MM-DD` o no es el primer día del mes |
| `periodo_duplicado`, `estimado_repetido` | períodos repetidos; meses estimados repetidos |
| `valor_no_numerico`, `valor_no_finito`, `valor_negativo` | valores que no son números, son infinitos o son negativos |
| `destino_inconsistente`, `unidad_inconsistente`, `run_id_inconsistente`, `version_inconsistente`, `metodo_inconsistente`, `fecha_generacion_inconsistente` | más de un valor en una columna constante, o un valor distinto del esperado |
| `fecha_generacion_invalida` | `fecha_generacion` no es ISO 8601 |
| `tipo_no_permitido`, `metodo_no_permitido` | valores fuera de los permitidos |
| `observados_y_estimados_mezclados` | un observado no es anterior a todos los estimados |
| `horizonte_incompleto`, `estimado_fuera_de_horizonte` | faltan meses del horizonte configurado o hay meses fuera de él |

`validate_estimates_file(path, cfg, ...)` valida un CSV leído sin convertir tipos. `ContractResult.raise_if_invalid()` lanza `ContractViolation`.

## Integración con la regla de aceptación

`acceptance_decision` acepta ahora `run_id` y `version` opcionales. La verificación `salidas_completas` es verdadera solo si las estimaciones cumplen el contrato; una versión con salidas incompletas o inválidas no queda autorizada. `decision_aceptacion.json` incluye la clave `contrato` con el resultado estructurado. Los umbrales (`min_evaluated_months`, `max_mae_ratio_to_baseline`), las métricas y los controles de calidad no cambiaron.

## Identificación de la fuente

La fuente se clasifica como `sintetica`, `institucional` o `no_declarada`. No se usa el nombre del archivo. El orden de decisión (`pipeline.metadata.classify_source`) es:

1. Si la huella SHA-256 está en `source.synthetic_sha256`, la fuente es sintética, aunque se declare institucional; el conflicto se registra en `fuente.conflicto_con_declaracion`.
2. Si se indica `--source-kind` (o `source_kind` en `execute`), se usa esa declaración.
3. Si no se reemplazó la ruta con `--data`, se usa `source.kind` de la configuración, que describe el archivo de `source.path`.
4. En cualquier otro caso, la fuente es `no_declarada`.

Una ejecución con `--data` sin declaración ni huella registrada queda `no_declarada`. Los resultados de fuentes sintéticas o no declaradas no se presentan como cifras institucionales.

## Esquema de `metadata.json`

Se escribe en `artifacts/runs/<run_id>/metadata.json` cuando la compuerta de calidad no detiene el flujo, haya sido aceptada o rechazada la ejecución. Es independiente de rutas absolutas: `validate_metadata` rechaza cualquier texto con una ruta de unidad, UNC o POSIX, y `write_metadata` no escribe si el esquema es inválido.

| Campo | Contenido |
|---|---|
| `esquema_version` | `1.0` |
| `run_id`, `fecha_generacion`, `version_procedimiento` | identificación; `fecha_generacion` sale de las estimaciones |
| `commit_git`, `arbol_git_modificado` | `HEAD` o `null` si no hay git; si hay cambios sin confirmar, el commit no identifica el código ejecutado |
| `configuracion` | huella SHA-256 de la configuración y semilla |
| `fuente` | `tipo`, `criterio`, `conflicto_con_declaracion`, `nombre_archivo`, `sha256`, `bytes`, `filas`, `filas_poblacion`, `filtros` |
| `objetivo` | destino, variable, unidad, granularidad |
| `periodos` | esperado, ajuste, evaluación y horizonte |
| `cobertura` | primer y último mes observado, cantidad de meses observados y estimados |
| `procedimiento` | método, período estacional, baseline |
| `metricas` | MAE y RMSE de la estimación y de la referencia, `razon_mae`, meses evaluados, solicitados y no evaluados |
| `calidad` | estado global y estado de cada control |
| `aceptacion` | decisión, regla, verificaciones y motivos de rechazo |
| `contrato` | resultado estructurado del validador |
| `advertencias` | advertencias de la ejecución |
| `limitaciones` | lista de `{id, texto, evidencia}` |
| `integridad` | huella SHA-256 de `estimaciones_candidatas.csv` |
| `mlflow`, `publicacion` | `null` hasta que se implementen esas etapas |

## Limitaciones verificadas

Cada limitación de `metadata.json` se genera a partir de la configuración o de lo que hizo la ejecución y nombra su evidencia. No se incluyen limitaciones que solo existan como texto libre:

- fuente sintética o no declarada, según `fuente.tipo`;
- estimador estacional ingenuo: sin tendencia ni incertidumbre (`parameters`);
- la ventana de ajuste solo define la cobertura exigida; el estimador no ajusta parámetros con ella (`periods.fit` solo la usa `checks._required_months`);
- evaluación en una sola ventana con origen móvil, con meses evaluados y no evaluados (`metricas`);
- población de referencia (`filters`);
- indicador nacional sin reparto entre destinos (`pipeline.prepare.prepare`);
- tramo homogéneo desde 2023, solo para fuentes institucionales: depende de lo que indica la hoja Notas de la fuente, que se documenta en `ALCANCE.md` sección 2.2.

## Decisiones metodológicas

- **Destino:** el destino de trabajo es Guatemala a nivel nacional; no se afirma que sea el destino asignado oficialmente.
- **Procedimiento de estimación:** no se modificó el algoritmo ni los umbrales.
- **`allocation_factor`:** no aplica al indicador implementado. Ver `ALCANCE.md` sección 2.
