"""Render SPEC.md to a styled PDF via Microsoft Edge headless.

Usage: python build_pdf.py [input.md] [output.pdf]
Defaults to SPEC.md -> SPEC.pdf.
"""

import os
import subprocess
import sys
from pathlib import Path

import markdown

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

CSS = """
@page { size: A4; margin: 18mm 16mm; }
html, body { color: #172b4d; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; line-height: 1.55; font-size: 11pt; }
body { max-width: 760px; margin: 0 auto; padding: 0 4px; }
h1 { font-size: 22pt; margin: 0 0 4pt; border-bottom: 1px solid #dfe1e6; padding-bottom: 6pt; }
h2 { font-size: 15pt; margin: 22pt 0 6pt; color: #0747a6; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 14pt 0 4pt; color: #253858; page-break-after: avoid; }
p, li { font-size: 11pt; }
ul, ol { padding-left: 22pt; }
li { margin-bottom: 3pt; }
a { color: #0052cc; text-decoration: none; }
strong { color: #172b4d; }
hr { border: none; border-top: 1px solid #dfe1e6; margin: 18pt 0; }
code { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; font-size: 9.5pt; background: #f4f5f7; padding: 1pt 4pt; border-radius: 3px; color: #b3261e; }
pre { background: #f4f5f7; border: 1px solid #e4e6ea; border-radius: 4pt; padding: 10pt 12pt; overflow-x: auto; font-size: 9.5pt; line-height: 1.4; page-break-inside: avoid; }
pre code { background: none; padding: 0; color: #172b4d; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 10pt; page-break-inside: avoid; }
th, td { border: 1px solid #dfe1e6; padding: 5pt 8pt; text-align: left; vertical-align: top; }
th { background: #f4f5f7; font-weight: 600; }
blockquote { border-left: 3px solid #c1c7d0; margin: 8pt 0; padding: 2pt 14pt; color: #5e6c84; }
"""

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def find_renderer() -> str:
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("Couldn't find Microsoft Edge or Chrome to render the PDF.")


def build(input_md: Path, output_pdf: Path) -> None:
    md_text = input_md.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
    title = input_md.stem
    html_doc = HTML_TEMPLATE.format(title=title, css=CSS, body=html_body)

    tmp_html = output_pdf.with_suffix(".tmp.html")
    tmp_html.write_text(html_doc, encoding="utf-8")

    renderer = find_renderer()
    file_uri = tmp_html.resolve().as_uri()
    cmd = [
        renderer,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={output_pdf.resolve()}",
        file_uri,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    tmp_html.unlink(missing_ok=True)
    if result.returncode != 0 or not output_pdf.exists():
        raise RuntimeError(
            f"Edge/Chrome exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def main() -> int:
    input_md = Path(sys.argv[1] if len(sys.argv) > 1 else "SPEC.md")
    output_pdf = Path(sys.argv[2] if len(sys.argv) > 2 else input_md.with_suffix(".pdf").name)
    if not input_md.exists():
        print(f"Input not found: {input_md}", file=sys.stderr)
        return 1
    build(input_md, output_pdf)
    print(f"Wrote {output_pdf} ({output_pdf.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
