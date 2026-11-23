"""Extract explicit question/answer pairs; never infer a missing answer key."""

from __future__ import annotations

import hashlib
import random
import re
import time
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import fitz
import pytesseract
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from lxml import etree
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from botapp.config import MB, Settings
from botapp.document_layout import clean_paper, font_path, orient_document
from botapp.media import MediaError, ocr_available
from botapp.office import validate_office

QUIZ_MAX_BYTES = 10 * MB
QUIZ_MAX_PAGES = 30
QUIZ_MAX_QUESTIONS = 100
LABELS = "ABCDEF"
_ANSWER_PREFIX = (
    r"^(?:(?:to['’`ʻ‘]?g['’`ʻ‘]?ri|correct|правильный)\s+)?"
    r"(?:javob(?:i)?|answer|ответ|жавоб(?:и)?|ж)\s*[:：=\-–—]\s*"
)
_ANSWER = re.compile(_ANSWER_PREFIX + r"(.+)$", re.I)
_OPEN_ANSWER = re.compile(_ANSWER_PREFIX, re.I)
_ANSWER_REFERENCE = re.compile(
    r"(\d{1,3})\s*[-–]?\s*савол(?:да ёритилган| билан бир хил)[.!]?", re.I
)
_QUESTION = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*(.*)$")
_EXPLICIT_QUESTION = re.compile(r"^(?:savol|савол|question|вопрос)\s*[:：]\s*(.*)$", re.I)
_OPTION = re.compile(r"(?<!\w)([*+]?)([A-FАВСДЕ])\s*[.)]\s*", re.I)
_KEY_HEADING = re.compile(
    r"^\s*(?:javoblar(?:i)?|javoblar\s+kaliti|answers?|ответы|answer\s+key)\s*[:：]?\s*$", re.I
)


def read_quiz_docx(path: Path) -> dict:
    if not 0 < path.stat().st_size <= QUIZ_MAX_BYTES:
        raise MediaError("Test fayli 10 MB dan oshmasin.")
    validate_office(path, "docx")
    with ZipFile(path) as archive:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 16 * MB:
            raise MediaError("Word faylining tuzilishi juda katta.")
        xml = etree.fromstring(
            archive.read(info), etree.XMLParser(resolve_entities=False, no_network=True)
        )

    rows = []
    for paragraph in xml.iter(qn("w:p")):
        parts = []
        for node in paragraph.iter(qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")):
            parts.append((node.text or "") if node.tag == qn("w:t") else " ")
        if text := "".join(parts).strip():
            highlighted = any(
                node.get(qn("w:val")) == "yellow" for node in paragraph.iter(qn("w:highlight"))
            )
            rows.append((text, highlighted))

    return {"questions": _parse_document_questions(rows), "scanned": False}


def _parse_document_questions(rows: list[tuple[str, bool]], *, pdf: bool = False) -> list[dict]:
    starts = []
    for index, (text, highlighted) in enumerate(rows):
        number = _QUESTION.fullmatch(text)
        explicit = _EXPLICIT_QUESTION.fullmatch(text)
        if not number and not explicit:
            continue
        prompt = number[2] if number else explicit[1]
        answer_start = index + 1
        # A PDF can wrap a question over a few visual lines before the answer marker.
        if pdf:
            while answer_start < min(index + 4, len(rows)):
                line = rows[answer_start][0]
                if (
                    _OPEN_ANSWER.match(line)
                    or _QUESTION.match(line)
                    or _EXPLICIT_QUESTION.match(line)
                    or _OPTION.match(line)
                ):
                    break
                prompt += " " + line
                answer_start += 1
        next_is_answer = answer_start < len(rows) and _OPEN_ANSWER.match(rows[answer_start][0])
        if prompt and (highlighted or explicit or next_is_answer):
            starts.append((index, answer_start, int(number[1]) if number else None, _text(prompt)))

    # Standard multiple-choice and answer-key files keep their original parser.
    # In text PDFs, an unpaired answer marker may belong to a multiple-choice block.
    paired = {
        start
        for _, start, _, _ in starts
        if start < len(rows) and _OPEN_ANSWER.match(rows[start][0])
    }
    if not paired or (
        pdf
        and any(i not in paired and _OPEN_ANSWER.match(text) for i, (text, _) in enumerate(rows))
    ):
        return parse_questions("\n".join(text for text, _ in rows))

    if len(starts) > QUIZ_MAX_QUESTIONS:
        raise MediaError("Bitta faylda ko'pi bilan 100 savol bo'lsin.")
    questions = []
    answers_by_number = {}
    for offset, (_, answer_start, number, question) in enumerate(starts):
        end = starts[offset + 1][0] if offset + 1 < len(starts) else len(rows)
        number = number if number is not None else offset + 1
        if answer_start >= end or not (marker := _OPEN_ANSWER.match(rows[answer_start][0])):
            raise MediaError(f"{number}-savoldan keyin «Javob:» yoki «Ж:» yozing.")
        parts = [rows[answer_start][0][marker.end() :].strip()]
        parts.extend(text for text, _ in rows[answer_start + 1 : end])
        answer = "\n".join(part for part in parts if part)
        if not answer or len(question) > 2200:
            raise MediaError(f"{number}-savol matni yoki javobi to'liq emas.")
        questions.append({"question": question, "model_answer": answer})
        answers_by_number.setdefault(number, []).append(answer)

    for question in questions:
        answer = question["model_answer"]
        reference = _ANSWER_REFERENCE.fullmatch(answer)
        if reference:
            matches = answers_by_number.get(int(reference[1]), [])
            if len(matches) == 1 and not _ANSWER_REFERENCE.fullmatch(matches[0]):
                question["model_answer"] = f"{answer}\n\n{matches[0]}"
    return questions


def _text(value: str) -> str:
    return " ".join(value.split()).strip()


def _label(value: str) -> str:
    return value.upper().translate(
        str.maketrans({"А": "A", "В": "B", "С": "C", "Д": "D", "Е": "E"})
    )


def parse_questions(text: str) -> list[dict]:
    text = text.replace("\x00", "").replace("\r", "")
    if len(text) > 350000:
        raise MediaError("PDFda matn juda ko'p. 100 savolgacha bo'lgan qismini yuboring.")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    keys: dict[int, str] = {}
    body = []
    in_keys = False
    for line in lines:
        match = re.match(
            r"^(javoblar(?:i)?(?:\s+kaliti)?|answer\s+key|ответы)\s*[:：]\s*(.+)$",
            line,
            re.I,
        )
        if _KEY_HEADING.match(line) or match:
            in_keys = True
            line = match.group(2) if match else ""
        if in_keys:
            for number, answer in re.findall(r"(\d{1,3})\s*[-:.)=]?\s*([A-FАВСДЕ])\b", line, re.I):
                number, answer = int(number), _label(answer)
                if number in keys and keys[number] != answer:
                    raise MediaError(f"{number}-savolning javob kalitlari bir-biriga zid.")
                keys[number] = answer
        else:
            body.append(line)
    numbered = any(_QUESTION.match(line) for line in body)
    records, current = [], None
    for line in body:
        q = _QUESTION.match(line) if numbered else None
        explicit_q = re.match(r"^(?:savol|question|вопрос)\s*[:：]\s*(.*)$", line, re.I)
        if q or explicit_q or (not numbered and current is None):
            if current:
                records.append(current)
            current = {"number": int(q.group(1)) if q else len(records) + 1, "lines": []}
            line = q.group(2) if q else explicit_q.group(1) if explicit_q else line
        if current is not None and line:
            current["lines"].append(line)
        if not numbered and current and _ANSWER.match(line):
            records.append(current)
            current = None
    if current:
        records.append(current)
    if not records:
        raise MediaError(
            "Savollar topilmadi. Savollar 1., 2. bilan, javoblar «Javob:» bilan yozilsin."
        )
    if len(records) > QUIZ_MAX_QUESTIONS:
        raise MediaError("Bitta PDFda ko'pi bilan 100 savol bo'lsin.")
    numbers = [record["number"] for record in records]
    if len(set(numbers)) != len(numbers):
        raise MediaError(
            "Savol raqamlari takrorlangan. Bitta variantni alohida PDF qilib yuboring."
        )
    if set(keys) - set(numbers):
        raise MediaError("Javob kalitidagi ayrim raqamlarga mos savol topilmadi.")

    parsed = []
    for record in records:
        n = record["number"]
        prompt, options, answers, marked = [], {}, [], []
        active = None
        for line in record["lines"]:
            answer_match = _ANSWER.match(line)
            if answer_match:
                answers.append(_text(answer_match.group(1)))
                active = None
                continue
            matches = list(_OPTION.finditer(line))
            if matches:
                before = _text(line[: matches[0].start()])
                if before:
                    (prompt if active is None else options[active]).append(before)
                for index, option in enumerate(matches):
                    label = _label(option.group(2))
                    if label in options:
                        raise MediaError(f"{n}-savolda {label} varianti takrorlangan.")
                    value = line[
                        option.end() : matches[index + 1].start()
                        if index + 1 < len(matches)
                        else len(line)
                    ]
                    if option.group(1) or re.search(r"[✓✔✅]", value):
                        marked.append(label)
                    options[label] = [_text(re.sub(r"[✓✔✅]", "", value))]
                    active = label
            elif active:
                options[active].append(line)
            else:
                prompt.append(line)
        question = _text(" ".join(prompt))
        question = re.sub(r"^(?:savol|question|вопрос)\s*[:：]\s*", "", question, flags=re.I)
        if not question or len(question) > 2200:
            raise MediaError(f"{n}-savol matni topilmadi yoki juda uzun. PDFni tekshiring.")
        if len(answers) > 1 and len(set(answers)) > 1:
            raise MediaError(f"{n}-savolda bir nechta turli javob berilgan.")
        if options:
            labels = list(options)
            values = [_text(" ".join(options[label])) for label in labels]
            if not 2 <= len(values) <= 6 or any(not value or len(value) > 700 for value in values):
                raise MediaError(f"{n}-savolda 2–6 ta to'liq javob varianti bo'lishi kerak.")
            correct_labels = list(marked)
            if n in keys:
                correct_labels.append(keys[n])
            for answer in answers:
                label_match = re.fullmatch(r"([A-FАВСДЕ])[.)]?", answer, re.I)
                if label_match:
                    correct_labels.append(_label(label_match.group(1)))
                elif answer.casefold() in [v.casefold() for v in values]:
                    correct_labels.append(
                        labels[[v.casefold() for v in values].index(answer.casefold())]
                    )
                else:
                    raise MediaError(f"{n}-savol javobi variantlarga mos kelmadi.")
            if len(set(correct_labels)) != 1 or correct_labels[0] not in labels:
                raise MediaError(
                    f"{n}-savolning yagona to'g'ri javobi aniqlanmadi. «Javob: A» kabi belgilang."
                )
            parsed.append(
                {
                    "question": question,
                    "options": values,
                    "correct": labels.index(correct_labels[0]),
                }
            )
        else:
            if len(answers) != 1 or not answers[0] or len(answers[0]) > 700:
                raise MediaError(f"{n}-savol uchun «Javob: ...» matni kerak.")
            parsed.append({"question": question, "answer": answers[0]})

    for item in parsed:
        if "answer" not in item:
            continue
        answer = item.pop("answer")
        candidates = []
        for other in parsed:
            value = other.get("answer") or (
                other["options"][other["correct"]] if "options" in other else None
            )
            if (
                value
                and value.casefold() != answer.casefold()
                and value.casefold() not in [v.casefold() for v in candidates]
            ):
                candidates.append(value)
        if not candidates:
            raise MediaError(
                "Variant tuzish uchun kamida ikkita turli savol-javob "
                "yoki tayyor A/B variantlari kerak."
            )
        rng = random.Random(hashlib.sha256(item["question"].encode()).digest())
        rng.shuffle(candidates)
        choices = [answer] + candidates[:3]
        rng.shuffle(choices)
        item.update(options=choices, correct=choices.index(answer), generated_options=True)
    for index, item in enumerate(parsed, 1):
        if len(item["question"]) + sum(map(len, item["options"])) > 3700:
            raise MediaError(f"{index}-savol va variantlari juda uzun. Matnni qisqartiring.")
        if len({value.casefold() for value in item["options"]}) != len(item["options"]):
            raise MediaError(f"{index}-savolda bir xil javob variantlari takrorlangan.")
    return parsed


def read_quiz_pdf(path: Path, settings: Settings) -> dict:
    if not 0 < path.stat().st_size <= QUIZ_MAX_BYTES:
        raise MediaError("Test uchun PDF 10 MB dan oshmasin.")
    pages, scanned, characters = [], 0, 0
    deadline = time.monotonic() + min(settings.job_timeout, 120)
    try:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise MediaError("Parolsiz PDF yuboring.")
            if not 1 <= len(document) <= QUIZ_MAX_PAGES:
                raise MediaError("Test PDFi 30 sahifadan oshmasin.")
            for page in document:
                if time.monotonic() > deadline:
                    raise MediaError(
                        "Matnni tanish vaqti tugadi. PDFni kichikroq qismlarga bo'ling."
                    )
                text = page.get_text(sort=True).strip()
                if len(re.sub(r"\W", "", text)) < 25 and page.get_images():
                    scanned += 1
                    if scanned > 5:
                        raise MediaError(
                            "Skanerlangan test 5 sahifadan oshmasin. Matnli PDF 30 sahifagacha."
                        )
                    if scanned == 1 and not ocr_available(settings):
                        raise MediaError(
                            "Skanerlangan PDF uchun matn tanish dasturi serverda tayyor emas."
                        )
                    factor = min(2, 1600 / max(page.rect.width, page.rect.height))
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(factor, factor), alpha=False)
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    image = orient_document(image, settings)
                    image = clean_paper(image)
                    try:
                        text = pytesseract.image_to_string(
                            image,
                            lang=settings.ocr_languages,
                            config="--psm 3",
                            timeout=max(1, min(25, deadline - time.monotonic())),
                        ).strip()
                    finally:
                        image.close()
                characters += len(text)
                if characters > 350000:
                    raise MediaError("PDFda matn juda ko'p. Faylni qismlarga bo'lib yuboring.")
                pages.append(text)
    except MediaError:
        raise
    except (fitz.FileDataError, RuntimeError, ValueError, pytesseract.TesseractError) as exc:
        raise MediaError("PDFni o'qib bo'lmadi. Matni tiniq va parolsiz fayl yuboring.") from exc
    rows = [(line.strip(), False) for page in pages for line in page.splitlines() if line.strip()]
    return {"questions": _parse_document_questions(rows, pdf=True), "scanned": bool(scanned)}


def export_quiz(questions: list[dict], output: Path, kind: str) -> None:
    if kind == "word":
        doc = Document()
        section = doc.sections[0]
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.top_margin = section.bottom_margin = Cm(1.8)
        section.left_margin = section.right_margin = Cm(2)
        for style in ("Normal", "Title", "Heading 1", "Heading 2"):
            doc.styles[style].font.name = "Arial"
            doc.styles[style].font.color.rgb = RGBColor(0, 0, 0)
            for border in list(doc.styles[style].element.iter(qn("w:pBdr"))):
                border.getparent().remove(border)
        doc.styles["Normal"].font.size = Pt(11)
        doc.add_heading("Test savollari", 0)
        doc.add_paragraph("Ism-familiya: _______________________    Sana: ______________")
        for index, question in enumerate(questions, 1):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.keep_with_next = True
            paragraph.add_run(f"{index}. {question['question']}").bold = True
            for label, option in zip(LABELS, question.get("options", []), strict=False):
                doc.add_paragraph(f"{label}) {option}")
            if "model_answer" in question:
                doc.add_paragraph("Javob: ______________________________________________")
        doc.add_page_break()
        doc.add_heading("Javoblar kaliti", 1)
        for index, question in enumerate(questions, 1):
            if "model_answer" in question:
                prompt = doc.add_paragraph()
                prompt.paragraph_format.keep_with_next = True
                prompt.add_run(f"{index}. {question['question']}").bold = True
                for line in question["model_answer"].splitlines():
                    if line:
                        doc.add_paragraph(line)
                continue
            doc.add_paragraph(
                f"{index}. {LABELS[question['correct']]} — "
                f"{question['options'][question['correct']]}"
            )
        doc.save(output)
        return
    if kind != "pdf":
        raise MediaError("Test chiqishi PDF yoki Word bo'lishi kerak.")
    name = "QuizText"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path())))
    normal = ParagraphStyle("QuizBody", fontName=name, fontSize=11, leading=15, spaceAfter=7)
    heading = ParagraphStyle(
        "QuizTitle", parent=normal, fontSize=17, leading=22, spaceAfter=15, textColor=colors.black
    )
    question_style = ParagraphStyle("Question", parent=normal, spaceBefore=9, keepWithNext=True)
    story = [
        Paragraph("Test savollari", heading),
        Paragraph("Ism-familiya: _______________________    Sana: ______________", normal),
    ]
    for index, question in enumerate(questions, 1):
        prompt = escape(f"{index}. {question['question']}").replace("\n", "<br/>")
        story.append(Paragraph(prompt, question_style))
        for label, option in zip(LABELS, question.get("options", []), strict=False):
            story.append(Paragraph(escape(f"{label}) {option}"), normal))
        if "model_answer" in question:
            story.append(Paragraph("Javob: ______________________________________________", normal))
        story.append(Spacer(1, 5))
    story += [PageBreak(), Paragraph("Javoblar kaliti", heading)]
    for index, question in enumerate(questions, 1):
        if "model_answer" in question:
            story.append(Paragraph(escape(f"{index}. {question['question']}"), question_style))
            story.extend(
                Paragraph(escape(line), normal)
                for line in question["model_answer"].splitlines()
                if line
            )
            continue
        story.append(
            Paragraph(
                escape(
                    f"{index}. {LABELS[question['correct']]} — "
                    f"{question['options'][question['correct']]}"
                ),
                normal,
            )
        )
    SimpleDocTemplate(
        str(output),
        pagesize=(21 * cm, 29.7 * cm),
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
    ).build(story)
