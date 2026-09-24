#!/usr/bin/env python3
"""
pizarra.py — arma la PPT y el resumen en una sola página alta para GoodNotes (whiteboard).

Uso:
  pip install weasyprint markdown pikepdf --break-system-packages -q
  python3 pizarra.py slides.pdf resumen.md "Clase X - Pizarra.pdf" [--escala 0.5]

En resumen.md, cada título que va al lado de slides termina con {n} o {n-m}:
  ## Direct exchange {23-24}
Esas slides quedan apiladas a la izquierda, arrancando a la altura del título.
Las slides que no aparecen en ningún título (carátula, cierre, etc.) no se incluyen.
"""
import argparse, io, re, sys
import markdown, weasyprint, pikepdf

MM = 72 / 25.4
MV, MH = 8 * MM, 10 * MM          # márgenes de la hoja A4 del resumen (se recortan al armar)
GAP_SLIDES, GAP_COLS, PAD = 6, 10, 3

CSS = f"""
@page {{ size: A4; margin: {MV}pt {MH}pt; }}
body {{ font: 9.5pt/1.38 'DejaVu Sans', sans-serif; color: #1f1f1f; }}
h1 {{ font-size: 13pt; margin: 0 0 4pt; }}
h2 {{ font-size: 11pt; margin: 8pt 0 3pt; color: #1b4f8a; }}
h3 {{ font-size: 10pt; margin: 6pt 0 2pt; }}
h2:first-child, h1 + h2 {{ margin-top: 0; }}
p, ul, ol, table, blockquote {{ margin: 0 0 4pt; }}
ul, ol {{ padding-left: 13pt; }}
li {{ margin: 0 0 1.5pt; }}
.d {{ font-size: 7pt; color: #9a9a9a; font-weight: normal; margin-left: 4pt; }}
table {{ border-collapse: collapse; font-size: 8.5pt; }}
th, td {{ border: 0.5pt solid #bbb; padding: 1.5pt 4pt; text-align: left; vertical-align: top; }}
code {{ font-family: 'DejaVu Sans Mono', monospace; font-size: 8.5pt; background: #f1f1f1; }}
blockquote {{ padding-left: 6pt; border-left: 2pt solid #ccc; color: #444; }}
"""

HEAD = re.compile(r'^(#{1,4})\s+(.*?)\s*\{\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*\}\s*$')


def preparar_md(texto):
    """Convierte los títulos con {n-m} en HTML con id, y devuelve [(id, [slides])]."""
    anclas, out = [], []
    for linea in texto.splitlines():
        m = HEAD.match(linea)
        if m:
            nivel, titulo, a = len(m[1]), m[2], int(m[3])
            b = int(m[4]) if m[4] else a
            aid = f"s{len(anclas)}"
            rango = f"{a}" if a == b else f"{a}–{b}"
            titulo_html = markdown.markdown(titulo)[3:-4]  # quita <p></p>
            out += ["", f'<h{nivel} id="{aid}">{titulo_html}<span class="d">diap. {rango}</span></h{nivel}>', ""]
            anclas.append((aid, list(range(a, b + 1))))
        else:
            out.append(linea)
    return "\n".join(out), anclas


def render_resumen(md_texto):
    md_html, anclas = preparar_md(md_texto)
    html = markdown.markdown(md_html, extensions=["tables", "sane_lists"])
    doc = weasyprint.HTML(string=f"<html><body>{html}</body></html>").render(
        stylesheets=[weasyprint.CSS(string=CSS)])
    # posición de cada título en coordenada continua (pt, páginas pegadas sin márgenes)
    pos, base = {}, 0.0
    for p in doc.pages:
        for aid, caja in p.anchors.items():
            y = caja[1]  # (x0, y0, x1, y1) en px CSS
            pos[aid] = base + y * 0.75 - MV
        base += p.height * 0.75 - 2 * MV
    return doc.write_pdf(), anclas, pos, base


def armar(slides_pdf, md_path, salida, escala):
    with open(md_path, encoding="utf-8") as f:
        pdf_bytes, anclas, pos, total = render_resumen(f.read())
    S = pikepdf.open(slides_pdf)
    R = pikepdf.open(io.BytesIO(pdf_bytes))
    out = pikepdf.new()

    rw, rh = float(R.pages[0].mediabox[2]), float(R.pages[0].mediabox[3])
    Wr = rw - 2 * MH
    Ws = Wr * escala
    ns = len(S.pages)

    # grupos: slides que van con cada título, en orden
    grupos, usadas = [], set()
    for aid, sl in anclas:
        sl = [s for s in sl if 1 <= s <= ns and s not in usadas]
        if not sl or aid not in pos:
            continue
        usadas.update(sl)
        a = max(0.0, pos[aid] - PAD)
        if grupos and a < grupos[-1]["a"]:
            a = grupos[-1]["a"]
        grupos.append({"a": a, "s": sl})

    def alto(i):
        mb = S.pages[i - 1].mediabox
        return (float(mb[3]) - float(mb[1])) * Ws / (float(mb[2]) - float(mb[0]))

    filas, y = [], 0.0
    if not grupos or grupos[0]["a"] > 0:
        fin = grupos[0]["a"] if grupos else total
        filas.append({"y": y, "seg": (0.0, fin), "s": []}); y += fin
    for k, g in enumerate(grupos):
        seg = (g["a"], grupos[k + 1]["a"] if k + 1 < len(grupos) else total)
        hs = sum(alto(i) + GAP_SLIDES for i in g["s"])
        filas.append({"y": y, "seg": seg, "s": g["s"]})
        y += max(seg[1] - seg[0], hs)
    H, W = y, Ws + GAP_COLS + Wr
    xr = Ws + GAP_COLS

    # cada página fuente se copia una sola vez como XObject y se dibuja recortada
    xo, ops = {}, ["1 1 1 rg", f"0 0 {W:.2f} {H:.2f} re f"]
    def nombre(pdf, idx, pref):
        key = f"/{pref}{idx}"
        if key not in xo:
            xo[key] = out.copy_foreign(pdf.pages[idx].as_form_xobject())
        return key

    hpag = rh - 2 * MV
    for f in filas:
        # tramos del resumen
        ua, ub = f["seg"]
        for pi in range(len(R.pages)):
            p0 = pi * hpag
            s, e = max(ua, p0), min(ub, p0 + hpag)
            if e - s < 0.5:
                continue
            ytop = H - (f["y"] + (s - ua))          # borde superior en destino
            src_top = rh - MV - (s - p0)             # borde superior en la página fuente
            n = nombre(R, pi, "R")
            ops.append(f"q {xr:.2f} {ytop - (e - s):.2f} {Wr:.2f} {e - s:.2f} re W n "
                       f"1 0 0 1 {xr - MH:.2f} {ytop - src_top:.2f} cm {n} Do Q")
        # slides
        yy = f["y"]
        for i in f["s"]:
            mb = S.pages[i - 1].mediabox
            sw, sx0, sy0 = float(mb[2]) - float(mb[0]), float(mb[0]), float(mb[1])
            k, h = Ws / sw, alto(i)
            n = nombre(S, i - 1, "S")
            ops.append(f"q {k:.5f} 0 0 {k:.5f} {-sx0 * k:.2f} {H - yy - h - sy0 * k:.2f} cm {n} Do Q")
            yy += h + GAP_SLIDES

    page = pikepdf.Page(out.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, W, H],
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary({k: v for k, v in xo.items()})),
        Contents=out.make_stream("\n".join(ops).encode()))))
    out.pages.append(page)
    out.save(salida)

    sin = [i for i in range(1, ns + 1) if i not in usadas]
    print(f"OK: {salida} ({W:.0f} x {H:.0f} pt)")
    print("Slides no incluidas:", ", ".join(map(str, sin)) or "ninguna")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("slides"); ap.add_argument("resumen_md"); ap.add_argument("salida")
    ap.add_argument("--escala", type=float, default=0.5, help="ancho de slide / ancho del resumen")
    a = ap.parse_args()
    armar(a.slides, a.resumen_md, a.salida, a.escala)
