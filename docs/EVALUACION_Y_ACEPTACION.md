# Estimación, validación temporal y aceptación

## Procedimiento candidato

El pipeline estima el ingreso mensual total de visitantes no residentes con una
**referencia estacional ingenua**: para cada mes usa el valor observado del
mismo mes del año anterior. Por ejemplo, la estimación de marzo de 2026 usa
marzo de 2025. Es un procedimiento determinista, no reentrena los modelos del
Laboratorio 10 y conserva el patrón estacional de una serie mensual.

La referencia de comparación es el **último valor observado** antes del mes
evaluado. Ambos procedimientos se calculan con la misma información disponible
en cada origen temporal.

## Validación temporal

La evaluación cubre enero a diciembre de 2025. Para cada mes, el pipeline:

1. retira conceptualmente su observación y todas las posteriores;
2. construye la estimación solo con datos anteriores;
3. compara la estimación y la referencia con el valor observado oculto.

Se calculan MAE y RMSE para el candidato y para la referencia. El detalle queda
en `artifacts/runs/<run_id>/evaluacion_temporal.csv`. Un período sin observación
comparable se marca `NO EVALUADO`; no se convierte a cero ni genera una métrica.

La fuente disponible es una fotografía final, por lo que este mecanismo simula
el corte temporal pero no reproduce exactamente la información disponible en
enero y febrero de 2026. Esas fechas no deben recibir una métrica si no existe
observación comparable en la fuente usada en una ejecución futura.

## Regla de aceptación

La regla está versionada en `config/config.yaml` y no se cambia después de ver
una ejecución. Un candidato queda **aceptado** solamente si se cumplen todas
estas condiciones:

1. ningún control de calidad tiene estado `FAIL`;
2. existen resultados candidatos completos, sin valores faltantes, y con todos
   los metadatos obligatorios;
3. hay al menos seis meses evaluados;
4. el MAE del candidato no supera el 110 % del MAE de la referencia.

La decisión y sus verificaciones se guardan en
`decision_aceptacion.json`. Una decisión aceptada **solo autoriza** la
publicación: el módulo de publicación controlada es responsable de mover una
versión a `published/current` y de conservar la versión anterior.

## Resultados candidatos

Cada ejecución sin fallos críticos escribe:

- `evaluacion_temporal.csv`: observación, estimación, referencia y errores por
  período evaluado;
- `estimaciones_candidatas.csv`: observaciones disponibles más las proyecciones
  de julio a diciembre de 2026;
- `decision_aceptacion.json`: regla, métricas, verificaciones y motivos de
  rechazo si aplica.

Las estimaciones incluyen `destino`, `periodo`, `tipo`, `valor`, `unidad`,
`run_id`, `fecha_generacion` y `version_procedimiento`, además del método.
