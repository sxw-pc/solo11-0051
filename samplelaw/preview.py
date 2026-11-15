"""Optional read-only local report browser (Flask is imported lazily)."""

from __future__ import annotations

from pathlib import Path

from samplelaw.util.errors import SamplelawError

def run_preview(directory: str | Path, port: int = 8765) -> None:
    try:
        from flask import Flask, abort, send_from_directory
    except ImportError as exc:
        raise SamplelawError(
            "CLI-005",
            "Flask is not installed; install the optional preview dependency first",
        ) from exc
    root = Path(directory).resolve()
    if not root.is_dir():
        raise SamplelawError("CLI-006", f"report directory not found: {root}")
    app = Flask(__name__)
    app.config.update(SEND_FILE_MAX_AGE_DEFAULT=0)
    @app.get("/")
    def index() -> str:
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.html") if path.is_file())
        items = "".join(f'<li><a href="/{item}">{item}</a></li>' for item in files)
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>samplelaw reports</title></head><body>"
            "<h1>samplelaw reports</h1><p>Read-only local preview.</p>"
            f"<ul>{items or '<li>No HTML reports found.</li>'}</ul>"
            "</body></html>"
        )
    @app.get("/<path:filename>")
    def report_file(filename: str) -> object:
        candidate = (root / filename).resolve()
        if root not in candidate.parents and candidate != root:
            abort(404)
        return send_from_directory(root, filename)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
