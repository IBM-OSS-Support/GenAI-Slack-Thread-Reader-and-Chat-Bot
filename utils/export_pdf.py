from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
import io
import re
def render_summary_to_pdf(text: str) -> io.BytesIO:
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
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # 1) Detect "*Heading*" at start, possibly with trailing text
        m = re.match(r'^\*(.+?)\*\s*(.*)$', line)
        if m:
            heading = m.group(1).strip()
            rest    = m.group(2).strip()
            flowables.append(Paragraph(heading, styles['Heading']))
            flowables.append(Spacer(1, 6))
            if rest:
                flowables.append(Paragraph(rest, styles['Body']))
                flowables.append(Spacer(1, 6))
            continue

        # 2) Bullet list item
        if line.startswith('- '):
            # collect consecutive bullets into one ListFlowable
            items = []
            # note: we only have one line at a time here,
            # so for simplicity treat each "- " line as its own list
            item = line[2:].strip()
            items.append(ListItem(Paragraph(item, styles['Body']), leftIndent=12))
            flowables.append(ListFlowable(items, bulletType='bullet'))
            flowables.append(Spacer(1, 6))
            continue

        # 3) Regular paragraph
        flowables.append(Paragraph(line, styles['Body']))
        flowables.append(Spacer(1, 6))

    doc.build(flowables)
    buffer.seek(0)
    return buffer
