"""Reporte de resultados de los controles de calidad."""

from __future__ import annotations

import json
from pathlib import Path

from .checks import FAIL, WARN, CheckResult


def overall_status(results: list[CheckResult]) -> str:
    statuses = {r.status for r in results}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return "PASS"


def write_quality_report(results: list[CheckResult], run_dir: Path, run_id: str) -> dict:
    overall = overall_status(results)
    payload = {
        "run_id": run_id,
        "estado_global": overall,
        "publicacion_permitida": overall != FAIL,
        "controles": [r.to_dict() for r in results],
    }
    (run_dir / "quality_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        f"# Reporte de calidad — ejecución `{run_id}`",
        "",
        f"**Estado global:** {overall} — "
        + ("la publicación puede continuar." if overall != FAIL else "la publicación queda BLOQUEADA."),
        "",
        "| Control | Estado | Resultado |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.name} | **{r.status}** | {r.message} |")
    lines += ["", "## Detalle por control", ""]
    for r in results:
        lines += [
            f"### {r.name} — {r.status}",
            f"- **Qué verifica:** {r.verifies}",
            f"- **Advertencia cuando:** {r.warn_when}",
            f"- **Se detiene cuando:** {r.stop_when}",
            f"- **Resultado:** {r.message}",
            "",
        ]
    (run_dir / "quality_report.md").write_text("\n".join(lines), encoding="utf-8")
    return payload
