#!/usr/bin/env python3
"""Regenerate docs/QAGRedo_Pipeline_Flowchart_Drawn.html (SVG layout)."""
from pathlib import Path

W, H = 680, 920
CX = 330


def build_svg() -> str:
    parts: list[str] = []

    def add(s: str) -> None:
        parts.append(s)

    add(
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {W} {H}\" "
        "role=\"img\" aria-labelledby=\"title desc\">"
    )
    add(
        "<title id=\"title\">QAGRedo pipeline flowchart</title>"
        "<desc id=\"desc\">Slots, grounding gate, replacement, save, "
        "documents.</desc>"
        "<defs>"
        "  <marker id=\"arrowhead\" markerUnits=\"userSpaceOnUse\" "
        "markerWidth=\"9\" markerHeight=\"9\" refX=\"8\" refY=\"4.5\" "
        "orient=\"auto\" overflow=\"visible\">"
        "    <polygon points=\"0 0, 9 4.5, 0 9\" fill=\"#2563eb\"/>"
        "  </marker>"
        "</defs>"
        "<style>.lbl{font-family:\"Source Sans 3\",system-ui,sans-serif;"
        "font-size:10px;fill:#2563eb}.lbld{font-size:9px;fill:#64748b}"
        "</style>"
    )

    def rect(x: float, y: float, w: float, h: float, rx: float = 6) -> None:
        add(
            f"<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" "
            f"rx=\"{rx}\" fill=\"#f8fafc\" stroke=\"#334155\" "
            f"stroke-width=\"1.5\"/>"
        )

    def term(x: float, y: float, w: float, h: float, lines: str) -> None:
        r = int(h // 2)
        add(
            f"<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" "
            f"rx=\"{r}\" fill=\"#e2e8f0\" stroke=\"#334155\" "
            f"stroke-width=\"1.5\"/>"
        )
        for i, line in enumerate(lines.split("\n")):
            add(
                f"<text x=\"{x + w / 2}\" y=\"{y + h / 2 + 5 + i * 12}\" "
                f"text-anchor=\"middle\" "
                f"font-family=\"Source Sans 3,system-ui,sans-serif\" "
                f"font-size=\"11\" fill=\"#0f172a\">{line}</text>"
            )

    def textc(x: float, y: float, s: str, sz: float = 10) -> None:
        esc = s.replace("&", "&amp;").replace("<", "&lt;")
        ln = esc.split("\n")
        y0 = y - (len(ln) - 1) * 6
        for i, t in enumerate(ln):
            add(
                f"<text x=\"{x}\" y=\"{y0 + i * 14}\" text-anchor=\"middle\" "
                f"font-family=\"Source Sans 3,system-ui,sans-serif\" "
                f"font-size=\"{sz}\" fill=\"#0f172a\">{t}</text>"
            )

    def diamond(cx: float, cy: float, rw: float, rh: float) -> None:
        pts = f"{cx},{cy - rh} {cx + rw},{cy} {cx},{cy + rh} {cx - rw},{cy}"
        add(
            f"<polygon points=\"{pts}\" fill=\"#f8fafc\" stroke=\"#334155\" "
            f"stroke-width=\"1.5\"/>"
        )

    def line_m(
        x1: float, y1: float, x2: float, y2: float, dashed: bool = False
    ) -> None:
        d = " stroke-dasharray=\"6 5\"" if dashed else ""
        add(
            f"<line x1=\"{x1}\" y1=\"{y1}\" x2=\"{x2}\" y2=\"{y2}\" "
            f"stroke=\"#2563eb\" stroke-width=\"1.8\" "
            f"marker-end=\"url(#arrowhead)\"{d}/>"
        )

    def path_m(d: str, dashed: bool = False) -> None:
        a = " stroke-dasharray=\"6 5\"" if dashed else ""
        add(
            f"<path d=\"{d}\" fill=\"none\" stroke=\"#2563eb\" "
            f"stroke-width=\"1.8\" stroke-linejoin=\"round\" "
            f"marker-end=\"url(#arrowhead)\"{a}/>"
        )

    def lbl(
        x: float, y: float, s: str, cls: str = "lbl", anchor: str = "middle"
    ) -> None:
        add(
            f"<text x=\"{x}\" y=\"{y}\" text-anchor=\"{anchor}\" "
            f"class=\"{cls}\">{s}</text>"
        )

    term(CX - 70, 10, 140, 34, "Start")
    rect(CX - 100, 52, 200, 38)
    textc(CX, 72, "Load & normalize\n(JSONL)", 10)
    line_m(CX, 44, CX, 52)
    line_m(CX, 90, CX, 108)
    rect(CX - 120, 108, 240, 44)
    textc(CX, 128, "generate_questions\n(N per document)", 10)
    diamond(CX, 210, 78, 34)
    textc(CX, 214, "More slots?", 9)
    line_m(CX, 152, CX, 176)
    rect(478, 188, 152, 44)
    textc(554, 208, "save_results\n(+ optional filters)", 9)
    line_m(CX + 78, 210, 478, 210)
    lbl(CX + 82, 204, "no", "lbl", "start")
    rect(CX - 100, 252, 200, 38)
    textc(CX, 276, "generate_answers", 10)
    line_m(CX, 244, CX, 252)
    rect(CX - 115, 304, 230, 40)
    textc(CX, 324, "grade + build pair +\ngrounding gate", 9)
    line_m(CX, 290, CX, 304)
    diamond(CX, 398, 78, 34)
    textc(CX, 392, "Passes\nquality gate?", 8)
    line_m(CX, 344, CX, 364)
    path_m(
        f"M {CX + 78} {398} L 560 {398} L 560 172 L {CX} 172 L {CX} 176"
    )
    lbl(565, 285, "next slot", "lbl", "start")
    rx, ry = 125, 448
    diamond(rx, ry, 52, 28)
    textc(rx, 444, "Regeneration\nrounds left?", 7)
    line_m(CX - 78, 398, rx + 52, 398)
    lbl(CX - 82, 392, "no", "lbl", "end")
    rect(48, 508, 100, 32)
    textc(98, 526, "Keep last", 9)
    line_m(rx, ry + 28, rx, 508)
    lbl(rx - 4, 502, "no", "lbl", "end")
    rect(210, 468, 128, 40)
    textc(274, 482, "generate_questions\n(×1 replace)", 8)
    path_m(f"M {rx + 52} {ry} L {rx + 52} 488 L 210 488")
    lbl(rx + 58, 430, "yes", "lbl", "start")
    path_m(
        f"M 274 468 L 274 280 L {CX + 100} 280 L {CX + 100} 252",
        dashed=True,
    )
    lbl(285, 360, "re-answer (same slot)", "lbld", "start")
    path_m(f"M 98 540 L 18 540 L 18 210 L {CX - 78} 210")
    lbl(22, 360, "to slots", "lbld", "start")
    diamond(CX, 780, 78, 34)
    textc(CX, 776, "Another\ndocument?", 9)
    path_m(f"M 554 232 L 554 688 L {CX} 688 L {CX} 746")
    rect(498, 762, 118, 36, 18)
    textc(557, 784, "Pipeline end", 10)
    line_m(CX + 78, 780, 498, 780)
    lbl(CX + 82, 774, "no", "lbl", "start")
    path_m(
        f"M {CX - 78} 780 L 48 780 L 48 138 L {CX} 138 L {CX} 108"
    )
    lbl(52, 520, "next document", "lbl", "start")
    add("</svg>")
    return "\n".join(parts)


def build_html() -> str:
    svg = build_svg()
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        "  <meta name=\"viewport\" content=\"width=device-width, "
        "initial-scale=1\" />\n"
        "  <title>QAGRedo — pipeline (SVG in HTML)</title>\n"
        "  <link href=\"https://fonts.googleapis.com/css2?family="
        "Source+Sans+3:wght@400;600&display=swap\" rel=\"stylesheet\" />\n"
        "  <style>\n"
        "    body {\n"
        "      margin: 0;\n"
        "      font-family: \"Source Sans 3\", system-ui, sans-serif;\n"
        "      background: #fafbfc;\n"
        "      color: #1e293b;\n"
        "      line-height: 1.5;\n"
        "      padding: 1.5rem;\n"
        "    }\n"
        "    .wrap { max-width: 760px; margin: 0 auto; }\n"
        "    h1 { font-size: 1.35rem; margin: 0 0 0.5rem; }\n"
        "    p.lead { color: #64748b; margin: 0 0 1rem; "
        "font-size: 0.95rem; }\n"
        "    .frame {\n"
        "      background: #fff;\n"
        "      border: 1px solid #e2e8f0;\n"
        "      border-radius: 12px;\n"
        "      padding: 1rem;\n"
        "      overflow-x: auto;\n"
        "    }\n"
        "    svg {\n"
        "      display: block;\n"
        "      width: 100%;\n"
        "      height: auto;\n"
        "      max-width: 680px;\n"
        "      margin: 0 auto;\n"
        "    }\n"
        "    footer { margin-top: 1.5rem; font-size: 0.8rem; "
        "color: #94a3b8; }\n"
        "    code { font-size: 0.88em; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <div class=\"wrap\">\n"
        "    <h1>QAGRedo pipeline (drawn in HTML / SVG)</h1>\n"
        "    <p class=\"lead\">\n"
        "      Native SVG (no Mermaid). Layout matches "
        "<code>run_qa_pipeline.py</code>:\n"
        "      slot loop, grounding gate, replacement question, save, "
        "document loop.\n"
        "    </p>\n"
        "    <div class=\"frame\">\n"
        f"{svg}\n"
        "    </div>\n"
        "    <footer>\n"
        "      Regenerate:\n"
        "      <code>python3 scripts/utils/"
        "_rewrite_drawn_flowchart_html.py</code>\n"
        "      · Editable PPTX:\n"
        "      <code>python3 scripts/utils/"
        "build_qagredo_pipeline_flowchart_pptx.py</code>\n"
        "      →\n"
        "      <code>docs/architecture/diagrams/"
        "QAGRedo_Pipeline_Flowchart_editable.pptx</code>\n"
        "      · PNG/SVG:\n"
        "      <code>python3 scripts/utils/"
        "render_pipeline_flowchart_png.py</code>\n"
        "      →\n"
        "      <code>docs/architecture/diagrams/"
        "QAGRedo_Pipeline_Flowchart.png</code> (+ .svg)\n"
        "      (venv: pip install -r requirements-diagram.txt "
        "or apt librsvg2-bin)\n"
        "    </footer>\n"
        "  </div>\n"
        "</body>\n"
        "</html>\n"
    )


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out = root / "docs" / "QAGRedo_Pipeline_Flowchart_Drawn.html"
    out.write_text(build_html(), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
