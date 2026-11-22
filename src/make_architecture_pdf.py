from pathlib import Path
import textwrap


def make_pdf():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        raise SystemExit("Install reportlab: python -m pip install reportlab")

    source = Path("ARCHITECTURE.md")
    text = source.read_text(encoding="utf-8")
    out = Path("architecture.pdf")
    c = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, "FrontierAtlas Technical Architecture")
    y -= 28
    c.setFont("Helvetica", 9)
    for line in text.splitlines():
        if y < 50:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = height - 50
        if not line.strip():
            y -= 8
            continue
        for wrapped in textwrap.wrap(line, width=100, break_long_words=False):
            if y < 50:
                c.showPage()
                c.setFont("Helvetica", 9)
                y = height - 50
            c.drawString(50, y, wrapped)
            y -= 12
    c.save()
    return out


if __name__ == "__main__":
    print(make_pdf())
