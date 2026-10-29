# Controles automáticos de calidad (ejercicio 3)

Cada control devuelve **PASS**, **WARN** (se reporta, el flujo continúa) o **FAIL** (el pipeline se detiene antes de publicar y lo ya publicado no cambia). Los umbrales están en `config/config.yaml → quality`. El reporte de cada ejecución queda en `artifacts/runs/<run_id>/quality_report.md` y `.json`.

| Control | Qué se verifica | Advertencia (WARN) | Detiene (FAIL) |
|---|---|---|---|
| a) Columnas y tipos | Existen las columnas indispensables con el tipo esperado | Falta una columna opcional | Falta una columna requerida o hay valores no convertibles |
| b) Fechas y cobertura | Fechas válidas; cada mes de ajuste y evaluación tiene datos | Fechas inválidas bajo el umbral; meses sin datos fuera de los períodos requeridos; fechas fuera del rango | Fechas inválidas sobre `max_invalid_frac`; meses sin datos en ajuste o evaluación |
| c) Unicidad | Sin duplicados para la clave `fecha + Vía + Región dos + Agrupación Residencia` | Duplicados dentro de `max_duplicate_frac` (si > 0) | Duplicados sobre el umbral (por defecto, cualquiera) |
| d) Valores faltantes | Se distinguen faltantes permitidos (dimensiones, etiquetadas «Sin dato») de los que impiden estimar (`Viajero`) | Faltantes permitidos bajo `max_allowed_frac` | Cualquier faltante en `Viajero`; permitidos sobre el umbral |
| e) Valores y denominadores | Sin negativos, sin infinitos, total mensual ≠ 0 (denominador del reparto) | Total mensual 0 si `zero_total: warn` | Negativos, infinitos o total mensual 0 (`zero_total: fail`) |
| f) Regla metodológica | La serie mensual viene de la fuente mensual (no de sumar presencias diarias), cuadra con ella y no rellena meses sin cobertura con 0 | — (cumplimiento estricto) | Fuente declarada o detectada diaria; la serie no cuadra con la suma de la fuente; un mes sin cobertura tiene valor |

Si el control a) falla, b)–f) se marcan **NO EVALUADO**. Si falla cualquier control, la preparación se omite y no se escribe ninguna serie.

**Pendiente de validar con los datos reales:** la clave de unicidad (si el Excel trae más columnas de granularidad, deben añadirse) y los nombres de `Año`/`Mes`. El reporte indica ejemplos de claves duplicadas para ajustarlo.
