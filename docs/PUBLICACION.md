# Publicación controlada, versionado y restauración

Implementado en `pipeline/publish.py` (publicación, verificación, listado y restauración) y `pipeline/restore.py` (comando). El tablero (`TABLERO.md`) lee solamente la versión que señala `CURRENT`. El monitoreo (`MONITOREO.md`) usa la versión vigente como línea base sin modificarla. Docker (volumen `published_data`) se describe en `DOCKER.md`; su configuración aún no está verificada.

## Estructura

La carpeta es `run.published_dir` (`published/` por defecto, relativa a la raíz del repositorio; `LAB11_PUBLISHED_DIR` la reemplaza). Está en `.gitignore`.

```
published/
  versions/<run_id>/
    estimaciones.csv
    metadata.json
    decision_aceptacion.json
    quality_report.json
    manifest.json
  CURRENT
  publicaciones.jsonl
  .staging/
  .lock
```

- **Versión:** el identificador es el `run_id` del pipeline. Cada versión está en su propia carpeta y no se modifica ni se sobrescribe una vez publicada.
- **`manifest.json`:** huella SHA-256 y tamaño de los otros cuatro archivos, el identificador de la versión, el tipo de fuente y la fecha de publicación. Es lo que permite comprobar la integridad.
- **`CURRENT`:** archivo de texto con el `run_id` de la versión vigente. Es la única referencia que debe usar el tablero: `published/versions/<contenido de CURRENT>/estimaciones.csv`.
- **`publicaciones.jsonl`:** una línea JSON por evento (`publicacion`, `publicacion_bloqueada`, `publicacion_fallida`, `restauracion`, `restauracion_rechazada`, `restauracion_fallida`) con fecha UTC, versión, versión anterior, tipo de fuente y huellas. No guarda rutas locales ni microdatos.
- **`.staging/` y `.lock`:** preparación temporal y bloqueo entre procesos.
- El `metadata.json` publicado es el de la ejecución con el campo `publicacion` completado (`version_id`, `fecha_publicacion`, `version_anterior`). El `metadata.json` de la carpeta de la ejecución no se modifica.
- No hay enlaces simbólicos: la estructura funciona igual en Windows y en Linux (por ejemplo, un volumen de Docker más adelante).

## Condiciones de publicación

Se publica solo si se cumple todo lo siguiente (`check_bundle`):

1. existen los cuatro artefactos de la ejecución;
2. la decisión de aceptación es favorable (`aceptada` verdadero en `decision_aceptacion.json` y en `metadata.json`);
3. el contrato de estimaciones es válido (columnas, valores finitos y no negativos, períodos sin duplicados, consistencia de destino, unidad, `run_id`, fecha y versión, horizonte completo), validado con los períodos y el destino de los propios metadatos;
4. los seis controles de calidad están presentes y ninguno tiene fallo crítico ni quedó sin evaluar;
5. los metadatos cumplen el esquema y no contienen rutas absolutas;
6. los identificadores coinciden: carpeta de la ejecución, `metadata.json`, `quality_report.json` y estimaciones;
7. la huella de `estimaciones` coincide con `integridad` en los metadatos;
8. la fuente es `sintetica` o `institucional`; una fuente `no_declarada` nunca se publica, y las sintéticas solo si `publication.allow_synthetic` es verdadero;
9. la versión no existe todavía.

Si algo falla, el candidato queda `bloqueada`, `CURRENT` no cambia y se registra el evento.

## Publicación atómica

1. Se toma un bloqueo exclusivo del sistema operativo (`.lock`); dos publicaciones simultáneas se ejecutan una tras otra y cada una registra como anterior la versión que dejó la otra. El bloqueo se libera solo si el proceso termina de forma abrupta.
2. La versión se prepara completa en `.staging/<run_id>.<token>/`.
3. La carpeta preparada se verifica con la misma función que se usa en la restauración.
4. Se mueve a `versions/<run_id>/` con un único renombrado, que no sobrescribe.
5. `CURRENT` se actualiza escribiendo un archivo temporal en la misma carpeta, forzando su escritura a disco y reemplazando el puntero con `os.replace`, que es atómico en Windows y en Linux. En Windows se reintenta si otro proceso tiene el puntero abierto.
6. Se añade el evento a `publicaciones.jsonl`.

Si falla la preparación o el movimiento, se elimina la carpeta temporal. Si falla la actualización de `CURRENT`, la versión recién movida se retira y `CURRENT` conserva la vigente (si no se pudiera retirar, queda registrada una advertencia). Si falla el registro en `publicaciones.jsonl` después de actualizar `CURRENT`, la publicación es válida y se informa una advertencia. Una interrupción del proceso deja a lo sumo una carpeta en `.staging/` que ningún lector utiliza.

## En el pipeline

Después de la regla de aceptación y solo si fue favorable, `python -m pipeline.run` intenta publicar. `run_log.json` incluye `publicacion_detalle` (estado, versión, fecha, versión anterior, errores y advertencias) y la etapa `publicacion`; MLflow recibe las etiquetas `publicacion.estado`, `publicacion.version`, `publicacion.fecha`, `publicacion.version_anterior` y `publicacion.errores`.

| Situación | Estado de publicación | Código de salida |
|---|---|---|
| Aceptada y publicada | `publicada` | 0 |
| Aceptada pero sin cumplir una condición (por ejemplo, fuente no declarada) | `bloqueada` | 0 |
| Rechazada por la regla o detenida por calidad | no se intenta | 0 o 2, como antes |
| Error técnico al publicar (escritura, bloqueo) | `fallida` | 1 |

Los códigos existentes conservan su significado. Un error técnico al publicar usa el código 1 (error inesperado) porque el candidato aprobado no pudo quedar disponible; `CURRENT` conserva la versión vigente. `publication.enabled: false` o `LAB11_PUBLISH_ENABLED=0` desactiva la publicación.

## Publicación sintética

Con `publication.allow_synthetic: true` (valor por defecto) se pueden publicar versiones generadas con datos sintéticos para pruebas locales. Quedan identificadas como tales: `metadata.json` (`fuente.tipo: sintetica` y la limitación `fuente_sintetica`), `manifest.json` (`fuente_tipo`) y `publicaciones.jsonl`. Una versión sintética que pase a ser la vigente reemplaza a `CURRENT` como cualquier otra: el consumidor debe mostrar siempre `fuente.tipo` y nunca presentar estas cifras como institucionales.

## Restauración

```powershell
python -m pipeline.restore --list
python -m pipeline.restore --version <run_id> --reason "motivo"
```

- `--list` muestra la versión vigente y, por cada versión, si es válida y por qué no lo es.
- `--version` vuelve a señalar con `CURRENT` una versión existente. No regenera estimaciones ni modifica versiones.
- Se rechaza una versión inexistente, con identificador no permitido, corrupta (huella distinta a la del manifest, archivos faltantes o ajenos) o no aprobada. El rechazo no cambia `CURRENT` y queda registrado.
- Si la versión ya es la vigente, no hace cambios.
- Se usa el mismo bloqueo y el mismo reemplazo atómico que en la publicación; el evento `restauracion` guarda la versión anterior y el motivo.
- Códigos de salida: `0` restaurada, ya vigente o listado; `2` versión rechazada; `1` error técnico.

## Fallos y recuperación

| Situación | Efecto | Qué hacer |
|---|---|---|
| Falla la escritura o el movimiento | no queda versión parcial; `CURRENT` sin cambios | corregir la causa y ejecutar de nuevo |
| Falla la actualización de `CURRENT` | se retira la versión nueva; `CURRENT` sin cambios | ídem |
| Proceso interrumpido | puede quedar una carpeta en `.staging/` | borrarla; no la lee ningún proceso |
| Una versión se altera después de publicada | `--list` la marca inválida y no se puede restaurar | restaurar otra versión válida |
| `CURRENT` falta o tiene un contenido no válido | no hay versión vigente | `python -m pipeline.restore --version <run_id>` |

Todas las versiones aprobadas se conservan; no hay limpieza automática.

## Verificación

```powershell
python -m pytest tests/test_publish.py -q
python -m pipeline.run --data data/synthetic/base_sintetica.csv --source-kind sintetica
Get-Content published\CURRENT
python -m pipeline.restore --list
Get-Content published\publicaciones.jsonl
```

Las pruebas usan datos sintéticos y carpetas temporales; `tests/conftest.py` desactiva la publicación y MLflow en las pruebas del pipeline para que no escriban en `published/` ni en `mlflow/`.
