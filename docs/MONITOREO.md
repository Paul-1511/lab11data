# Monitoreo, alertas y recuperación ante fallos

Implementado en `pipeline/monitoring.py` y llamado por `python -m pipeline.run` después de la regla de aceptación y antes de la publicación. Docker todavía no está implementado.

## Qué hace y qué no hace

El monitoreo **informa**: no cambia los códigos de salida, la regla de aceptación ni la decisión de publicar. Reutiliza los detalles de los seis controles de calidad (`CheckResult.details`) y el contrato de estimaciones; no repite sus verificaciones. No calcula errores predictivos: no hay observaciones comparables para los meses estimados. Un fallo del propio monitoreo no detiene el pipeline: se registra `monitoreo.estado: error` y una advertencia.

Las comparaciones usan como **línea base** la versión que señala `CURRENT` antes de publicar la ejecución, siempre que sea íntegra (`verify_directory`), de la misma clase de fuente (sintética o institucional) y la publicación esté habilitada. Si no hay línea base (`linea_base.estado`: `sin_version_vigente`, `version_vigente_no_valida`, `tipo_de_fuente_distinto`, `publicacion_deshabilitada` o `error_de_lectura`) solo se aplican las reglas absolutas.

## Severidades

| Severidad | Significado |
|---|---|
| `critica` | La condición ya impide publicar por otra vía (control de calidad fallido o contrato inválido). El monitoreo la nombra para que sea visible |
| `advertencia` | Requiere revisión humana. No bloquea y, por sí sola, no demuestra un problema de precisión |

## Alertas

| Categoría | Código | Severidad | Cuándo |
|---|---|---|---|
| actualización | `fuente_desactualizada` | advertencia | El último período observado está más de `max_months_behind` meses antes de `periods.expected_end` |
| actualización | `meses_requeridos_ausentes` | crítica | Faltan meses de los períodos de ajuste o evaluación (control b). Nunca se rellenan con cero |
| actualización | `huecos_en_la_serie` | advertencia | Meses sin cobertura dentro de la serie observada, fuera de los períodos requeridos. Los meses finales sin dato no se repiten aquí: los explica `fuente_desactualizada` |
| actualización | `horizonte_incompleto` | crítica | Las estimaciones finitas no cubren todos los meses de `periods.forecast` |
| actualización | `publicacion_desactualizada` | advertencia | La versión vigente llega a un último período observado atrasado respecto de `expected_end` |
| actualización | `publicacion_antigua` | advertencia | La versión vigente se publicó hace más de `max_publication_age_days` días |
| actualización | `candidato_con_menos_datos` | advertencia | El último período observado de la ejecución es anterior al de la versión vigente |
| calidad | `faltantes_altos` | advertencia (crítica sobre el umbral que detiene) | Fracción de faltantes de una columna mayor que `missing_warn_fraction` |
| calidad | `faltantes_aumentaron` | advertencia (crítica sobre el umbral que detiene) | La fracción de faltantes de una columna aumentó más de `missing_max_increase` frente a la versión vigente |
| calidad | `duplicados_aumentaron` | crítica si el control c falla; advertencia si no | La fracción de filas con clave duplicada aumentó más de `duplicates_max_increase` (sin línea base se compara con 0) |
| calidad | `perdida_de_cobertura` | advertencia | Más de `coverage_max_months_lost` meses observados en la versión vigente ya no tienen observación |
| resultados | `estimaciones_negativas` | crítica | Alguna estimación es negativa |
| resultados | `valores_no_finitos` | crítica | Alguna estimación es NaN o infinita |
| resultados | `variacion_mensual_alta` | advertencia | La variación relativa entre meses consecutivos (último observado y estimaciones) supera `max_monthly_change` |

**Fuente desactualizada frente a horizonte incompleto.** La primera mide los *datos*: el último mes observado respecto del esperado. El segundo mide la *salida*: cuántos meses del horizonte tienen estimación finita. Pueden ocurrir por separado: con la fuente atrasada dos meses, la estimación estacional de julio a diciembre aún puede estar completa.

## Umbrales (`monitoring` en `config/config.yaml`)

Todos son configurables y se validan (números no negativos). `LAB11_MONITOR_ENABLED=0` desactiva el monitoreo.

| Umbral | Valor | Justificación y límite |
|---|---|---|
| `freshness.max_months_behind` | 0 | La fuente es mensual y `expected_end` declara el último mes esperado: cualquier atraso significa que la fuente no llegó a ese mes. Límite: `expected_end` se edita a mano; si no se actualiza, la alerta no detecta una fuente que avanzó |
| `freshness.max_publication_age_days` | 45 | Una fuente mensual debería renovarse cada ~30 días; se añaden dos semanas de tolerancia. No se conoce el calendario real de publicación de la fuente institucional, por eso es una política conservadora |
| `quality.missing_warn_fraction` | 0.02 | Avisa antes del umbral que detiene el pipeline (`quality.missing.max_allowed_frac` = 0.05). El nivel documentado de la fuente es de 13 filas con «0» en «Región dos» de 161.036, cerca de 0,008 %: 2 % es un salto grande frente a ese nivel |
| `quality.missing_max_increase` | 0.005 | Medio punto porcentual sobre la versión vigente: muy por encima del nivel documentado, para no avisar por ruido |
| `quality.duplicates_max_increase` | 0.0 | La clave es exacta (mes × vía × frontera × país × agrupación × tipo) y el control c ya detiene con cualquier duplicado |
| `quality.coverage_max_months_lost` | 0 | La cobertura publicada es mensual y sin huecos; perder un mes observado ya merece revisión |
| `results.max_monthly_change` | 0.40 | **No se midió la variabilidad mensual de la fuente real en esta etapa.** La estimación estacional ingenua repite el patrón del año anterior, de modo que sus cambios mensuales copian los de ese año; 40 % es un nivel de revisión inicial deliberadamente conservador, no un valor derivado de los datos |

Para calibrar `max_monthly_change`, la alerta incluye `maxima_variacion_observada`: la mayor variación mensual entre observaciones consecutivas desde el inicio de la ventana de ajuste (`periods.fit.start`), calculada con la misma serie. Si las variaciones observadas normales ya superan 40 %, el umbral se sube con una decisión del grupo; no se cambia después de ver una alerta concreta. Los pares con mes anterior igual a cero no se calculan (no se divide por cero) y se cuentan en `pares_no_calculables`.

## Interpretación

- **Advertencias:** son una invitación a revisar. `variacion_mensual_alta` puede deberse a la estacionalidad real del mes anterior; no indica pérdida de precisión.
- **Alertas críticas:** coinciden con condiciones que ya impiden publicar. La ejecución terminó con código `2` (control de calidad) o fue rechazada por el contrato; `CURRENT` no cambió.
- **`faltantes_*` y `duplicados_aumentaron`** comparan contra la versión vigente; sin línea base solo se evalúa el nivel absoluto.

## Registro por ejecución

`run_log.json` contiene: `inicio`, `fin`, `duracion_segundos`, `etapas` (con inicio, fin y estado de cada una), `etapas_completadas`, `advertencias`, `errores`, `publicacion`, `resultado_publicacion`, `version_publicada` (o `null`), `monitoreo` (estado, línea base, resumen y umbrales) y `alertas`. El detalle de monitoreo también queda en `monitoreo.json` de la carpeta de la ejecución. Las alertas no se agregan a `advertencias` ni a `metadata.json`.

En MLflow quedan las etiquetas `monitoreo.estado`, `monitoreo.alertas` (códigos) y `monitoreo.linea_base`, las métricas `monitoreo.alertas_criticas` y `monitoreo.alertas_advertencia`, y el artefacto `monitoreo.json`. Si el monitoreo falló no se registran métricas.

## Fallos críticos

| Situación | Código de salida | Efecto en lo publicado |
|---|---|---|
| Control de calidad crítico fallido (duplicados, faltantes que impiden estimar, valores negativos, meses requeridos ausentes…) | 2 | `CURRENT` no cambia |
| Candidato rechazado por la regla de aceptación o por el contrato | 0 | `CURRENT` no cambia |
| Error inesperado o fallo técnico al publicar | 1 | `CURRENT` conserva la versión vigente |

El tablero lee siempre `CURRENT`, así que sigue mostrando la última versión aprobada mientras no se publique otra.

## Procedimiento de recuperación

1. Revisar la salida del pipeline y `artifacts/runs/<run_id>/quality_report.md`, `monitoreo.json` y `run_log.json`.
2. Confirmar que `published/CURRENT` no cambió y que el tablero muestra la versión anterior.
3. Corregir la causa en la fuente (por ejemplo, eliminar las filas duplicadas) y ejecutar de nuevo el pipeline.
4. Si la nueva ejecución es aceptada, se publica como versión nueva y `CURRENT` la señala.
5. Si hay que volver a una versión anterior aprobada: `python -m pipeline.restore --list` y `python -m pipeline.restore --version <run_id> --reason "motivo"`. No se regeneran estimaciones.

## Evidencias que debe recopilar el usuario

- Salida de `python -m pytest -q` y de `python -m pytest tests/test_monitoring.py -q`.
- Salida de la ejecución válida, de la ejecución con falla (código de salida `2` y las líneas «Monitoreo») y de la ejecución corregida.
- `published\CURRENT` antes y después de la falla, y después de la corrección y de la restauración.
- `python -m pipeline.restore --list` y `published\publicaciones.jsonl`.
- `monitoreo.json` y `run_log.json` de cada ejecución.
- Capturas de MLflow con las etiquetas `monitoreo.*` y de la página Inicio del tablero durante la falla (debe mostrar la versión anterior).

## Limitaciones

- Las alertas se consultan en la consola, `run_log.json`, `monitoreo.json` y MLflow; no hay notificaciones externas, programación periódica ni vista de alertas en el tablero.
- Los umbrales son iniciales; no se calibraron con la serie institucional.
- El monitoreo corre al ejecutar el pipeline: no vigila `published/` entre ejecuciones.
- La línea base es solo la versión vigente; no hay tendencias de varias versiones.
- Una ejecución que termina con error inesperado (código `1`) antes de la compuerta de calidad no genera alertas de monitoreo.
