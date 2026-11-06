# Imagen única del Laboratorio 11: la usan el pipeline, el tablero Streamlit y el servidor de MLflow.
# Python 3.11 es la versión con la que se desarrolló y probó el proyecto.
# No contiene datos: la fuente se monta en /data y los resultados viven en volúmenes (docker-compose.yml).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Usuario sin privilegios: los volúmenes nuevos heredan su propietario de las carpetas creadas más abajo.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Se copian solo estas rutas, de forma explícita: nunca data/, artifacts/, published/, mlflow/
# ni .streamlit/secrets.toml, aunque existan en la copia de trabajo (ver también .dockerignore).
COPY app.py ./app.py
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY config ./config
COPY pipeline ./pipeline
COPY dashboard ./dashboard
COPY scripts ./scripts

# Puntos de montaje: /app/artifacts (registros de ejecuciones), /app/published (versiones y CURRENT),
# /app/mlflow (base SQLite y artefactos de MLflow) y /data (fuente, solo lectura).
RUN mkdir -p /app/artifacts /app/published /app/mlflow /data \
    && chown -R app:app /app /data

# Trazabilidad del código sin copiar .git: quien construye la imagen declara el commit y si el árbol de
# trabajo tenía cambios sin confirmar (scripts/docker_build.ps1 los obtiene de Git). Vacíos significan
# "no disponible": nunca se inventa un identificador. Se declaran al final para conservar la caché de pip.
ARG GIT_COMMIT=""
ARG GIT_TREE_MODIFIED=""

ENV LAB11_MLFLOW_DIR=/app/mlflow \
    LAB11_PUBLISHED_DIR=/app/published \
    LAB11_GIT_COMMIT=$GIT_COMMIT \
    LAB11_GIT_TREE_MODIFIED=$GIT_TREE_MODIFIED

USER app

EXPOSE 5000 8501

# Sin comando por defecto que estime: el pipeline se ejecuta de forma explícita.
CMD ["python", "-m", "pipeline.run", "--help"]
