"""report dict -> one self-contained HTML file."""

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", " ": "\\u2028", " ": "\\u2029"}


def render_html(report: dict) -> str:
    payload = json.dumps(report, ensure_ascii=False)
    for char, escaped in _ESCAPES.items():
        payload = payload.replace(char, escaped)
    return TEMPLATE.read_text(encoding="utf-8").replace("__REPORT_JSON__", payload)


def write_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path = out_dir / "report.html"
    html_path.write_text(render_html(report), encoding="utf-8")
    return html_path
