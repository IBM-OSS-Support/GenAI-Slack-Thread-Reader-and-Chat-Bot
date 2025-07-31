#!/usr/bin/env python3
# summary_to_pdf.py

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
import io
import re

def render_summary_to_pdf(text: str) -> io.BytesIO:
    """
    Renders a plain-text summary (with simple markdown-like *bold* markers and - bullets)
    into a PDF stored in an in-memory buffer.

    - Lines beginning with *Heading* are rendered in the Heading style.
    - Inline *bold* markers are converted to <b>…</b>.
    - Lines starting with "- " are treated as bullet items.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40, leftMargin=40,
        topMargin=40, bottomMargin=40,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='Heading',
        parent=styles['Heading2'],
        fontSize=14,
        leading=18,
        fontName='Helvetica-Bold'
    ))
    styles.add(ParagraphStyle(
        name='Body',
        parent=styles['BodyText'],
        fontSize=10,
        leading=12
    ))

    flowables = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 1) Heading: lines that start with *…* (first asterisk pair)
        m = re.match(r'^\*(.+?)\*\s*(.*)$', line)
        if m:
            heading_text = m.group(1).strip()
            rest_text    = m.group(2).strip()
            # Convert any inline *bold* in the trailing text
            rest_text = re.sub(r'\*(.+?)\*', r'<b>\1</b>', rest_text)
            flowables.append(Paragraph(heading_text, styles['Heading']))
            flowables.append(Spacer(1, 6))
            if rest_text:
                flowables.append(Paragraph(rest_text, styles['Body']))
                flowables.append(Spacer(1, 6))
            continue

        # 2) Bullet list item
        if line.startswith('- '):
            item_text = line[2:].strip()
            # Convert inline *bold* to <b>…</b>
            item_text = re.sub(r'\*(.+?)\*', r'<b>\1</b>', item_text)
            flowables.append(
                ListFlowable(
                    [ ListItem(Paragraph(item_text, styles['Body']), leftIndent=12) ],
                    bulletType='bullet'
                )
            )
            flowables.append(Spacer(1, 6))
            continue

        # 3) Regular paragraph: convert inline *bold* to <b>…</b>
        paragraph_text = re.sub(r'\*(.+?)\*', r'<b>\1</b>', line)
        flowables.append(Paragraph(paragraph_text, styles['Body']))
        flowables.append(Spacer(1, 6))

    doc.build(flowables)
    buffer.seek(0)
    return buffer


if __name__ == "__main__":
    # Example usage: read from a text file and write out PDF
    import sys
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.txt> <output.pdf>")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    pdf_buffer = render_summary_to_pdf(content)
    with open(output_path, 'wb') as out_f:
        out_f.write(pdf_buffer.getvalue())
    print(f"PDF written to {output_path}")
