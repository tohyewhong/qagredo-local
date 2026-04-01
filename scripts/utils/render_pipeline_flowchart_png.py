#!/usr/bin/env python3
"""Rasterize the drawn pipeline SVG to PNG (and optional standalone SVG)."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_build_svg():
    root = _repo_root()
    path = root / "scripts" / "utils" / "_rewrite_drawn_flowchart_html.py"
    spec = importlib.util.spec_from_file_location("_rewrite_fc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_svg


def _png_via_rsvg(svg_path: Path, png_path: Path, width: int) -> bool:
    exe = shutil.which("rsvg-convert")
    if not exe:
        return False
    cmd = [exe, "-w", str(width), "-o", str(png_path), str(svg_path)]
    r = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return r.returncode == 0


def _png_via_cairosvg(svg_bytes: bytes, png_path: Path, width: int) -> bool:
    try:
        import cairosvg
    except ImportError:
        return False
    cairosvg.svg2png(
        bytestring=svg_bytes,
        write_to=str(png_path),
        output_width=width,
    )
    return True


def _venv_diagram_python(root: Path) -> Path | None:
    cand = root / ".venv_diagram" / "bin" / "python"
    return cand if cand.is_file() else None


def _png_via_venv_cairosvg(
    venv_py: Path,
    svg_bytes: bytes,
    png_path: Path,
    width: int,
) -> bool:
    """Rasterize using cairosvg inside the project venv."""
    snippet = (
        "import cairosvg, os, sys\n"
        "cairosvg.svg2png(\n"
        "    bytestring=sys.stdin.buffer.read(),\n"
        "    write_to=os.environ['QAGREDO_PNG_OUT'],\n"
        "    output_width=int(os.environ['QAGREDO_PNG_W']),\n"
        ")\n"
    )
    env = {
        **os.environ,
        "QAGREDO_PNG_OUT": str(png_path),
        "QAGREDO_PNG_W": str(width),
    }
    r = subprocess.run(
        [str(venv_py), "-c", snippet],
        input=svg_bytes,
        capture_output=True,
        check=False,
    )
    return r.returncode == 0


def main() -> int:
    root = _repo_root()
    ap = argparse.ArgumentParser(
        description="Export QAGRedo pipeline flowchart to PNG.",
    )
    d_out = (
        root / "docs" / "architecture" / "diagrams" /
        "QAGRedo_Pipeline_Flowchart.png"
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=d_out,
        help="Output PNG path",
    )
    ap.add_argument(
        "--svg",
        type=Path,
        default=None,
        help="Also write standalone SVG (default: beside PNG)",
    )
    ap.add_argument(
        "--width",
        type=int,
        default=1360,
        help="PNG width in pixels",
    )
    ap.add_argument(
        "--no-svg",
        action="store_true",
        help="Do not write .svg next to PNG",
    )
    args = ap.parse_args()
    build_svg = _load_build_svg()
    svg = build_svg()
    svg_bytes = svg.encode("utf-8")

    out_png = args.output.resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)

    if args.no_svg:
        svg_path = out_png.with_suffix(".svg.tmp")
        svg_path.write_bytes(svg_bytes)
        tmp = True
    else:
        svg_path = args.svg.resolve() if args.svg else out_png.with_suffix(
            ".svg",
        )
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_bytes(svg_bytes)
        tmp = False

    ok = _png_via_rsvg(svg_path, out_png, args.width)
    if not ok:
        ok = _png_via_cairosvg(svg_bytes, out_png, args.width)
    if not ok:
        vpy = _venv_diagram_python(root)
        if vpy is not None:
            ok = _png_via_venv_cairosvg(vpy, svg_bytes, out_png, args.width)

    if tmp:
        svg_path.unlink(missing_ok=True)

    if not ok:
        vpy = _venv_diagram_python(root)
        if vpy is not None:
            pip = str(vpy.parent / "pip")
            extra = (
                f"Project venv exists ({vpy}). Install raster deps:\n"
                f"  {pip} install -r requirements-diagram.txt\n"
            )
        else:
            extra = (
                "Create a project venv (gitignored):\n"
                "  python3 -m venv .venv_diagram && "
                ".venv_diagram/bin/pip install -r "
                "requirements-diagram.txt\n"
            )
        print(
            "Could not rasterize SVG. Pick one:\n"
            "  • apt: sudo apt install librsvg2-bin\n"
            "  • pip:\n" + extra +
            "Then re-run: python3 scripts/utils/"
            "render_pipeline_flowchart_png.py",
            file=sys.stderr,
        )
        return 1

    print(f"Wrote {out_png}")
    if not args.no_svg and not tmp:
        print(f"Wrote {svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
