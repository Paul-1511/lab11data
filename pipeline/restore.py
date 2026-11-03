"""Comando de restauración de versiones aprobadas.

    python -m pipeline.restore --list
    python -m pipeline.restore --version <run_id> [--reason TEXTO]

Códigos de salida: 0 = restaurada o ya vigente (o listado), 2 = versión rechazada
(inexistente, corrupta o no aprobada), 1 = error técnico. No se regeneran estimaciones.
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .publish import STATE_REJECTED, list_versions, load_settings, read_current, restore_version


def _print_versions(cfg: dict) -> None:
    current = read_current(load_settings(cfg).root)
    print(f"Versión vigente (CURRENT): {current or 'ninguna'}")
    rows = list_versions(cfg)
    if not rows:
        print("No hay versiones publicadas.")
        return
    print(f"{'':2}{'versión':<34}{'estado':<10}{'fuente':<14}{'publicada (UTC)':<27}errores")
    for row in rows:
        mark = "→ " if row["vigente"] else "  "
        state = "válida" if row["valida"] else "inválida"
        print(f"{mark}{row['version']:<34}{state:<10}{(row['fuente_tipo'] or '-'):<14}"
              f"{(row['fecha_publicacion'] or '-'):<27}{', '.join(row['errores'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Restauración de versiones aprobadas (Lab 11)")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--list", action="store_true", help="lista las versiones y su validez")
    ap.add_argument("--version", default=None, help="run_id de la versión que pasa a ser la vigente")
    ap.add_argument("--reason", default=None, help="motivo de la restauración, para la bitácora")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.version is None:
        _print_versions(cfg)
        return 0

    outcome = restore_version(cfg, args.version, reason=args.reason)
    print(f"Restauración de {args.version}: {outcome.message()}")
    for error in outcome.errores:
        print(f"  ERROR {error.code}: {error.message}", file=sys.stderr)
    for warning in outcome.advertencias:
        print(f"  ADVERTENCIA: {warning}", file=sys.stderr)
    if outcome.ok:
        return 0
    return 2 if outcome.estado == STATE_REJECTED else 1


if __name__ == "__main__":
    sys.exit(main())
