import os
import io
import json
import logging
import asyncio
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import google.generativeai as genai

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_RIGHT
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, Image, KeepTogether
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"))

# ---------- Палитра как в классическом немецком образце ----------
BAR = colors.HexColor("#C3D0E0")      # голубая полоса секции
RULE = colors.HexColor("#D8DEE6")     # тонкая линия
LABEL = colors.HexColor("#8B8B8B")    # серые подписи слева
DARK = colors.HexColor("#222222")     # основной текст

# ---------- Состояния диалога ----------
(NAME, BIRTH_DATE, BIRTH_PLACE, NATIONALITY, ADDRESS, PHONE, EMAIL,
 BERUF, UNTERNEHMEN, START_DATE,
 SCHULE, WEITERBILDUNG, ERFAHRUNG, PRAKTIKA,
 SPRACHEN, FACHKENNTNISSE, FUEHRERSCHEIN, STAERKEN, INTERESSEN, MOTIVATION,
 PHOTO, CONFIRM) = range(22)

SKIP = "\n\n_Если нечего указать — напиши_ *пропустить*"

QUESTIONS = {
    NAME:        "👤 *Vor- und Nachname*\n\nНапример: `Ali Mustermann`",
    BIRTH_DATE:  "📅 *Geburtsdatum*\n\nФормат: `05.01.2002`",
    BIRTH_PLACE: "📍 *Geburtsort*\n\nНапример: `Duschanbe, Tadschikistan`",
    NATIONALITY: "🌐 *Staatsangehörigkeit*\n\nНапример: `tadschikisch`" + SKIP,
    ADDRESS:     "🏠 *Adresse в Германии*\n\nНапример: `Hauptstraße 11, 97318 Kitzingen`",
    PHONE:       "📞 *Telefonnummer*\n\nНапример: `+49 151 12345678`",
    EMAIL:       "📧 *E-Mail*\n\nНапример: `ali.mustermann@gmail.com`",

    BERUF:       "💼 *Angestrebte Ausbildung / Stelle*\n\n"
                 "Точное название профессии.\nНапример: `Hotelfachmann`, `Kfz-Mechatroniker`, `Pflegefachmann`",
    UNTERNEHMEN: "🏢 *Unternehmen*\n\nКуда подаёшься — название и город.\n"
                 "Например: `Hotel Freihof, Kitzingen`" + SKIP,
    START_DATE:  "🗓 *Verfügbar ab*\n\nКогда можешь начать.\nНапример: `01.09.2026` или `sofort`" + SKIP,

    SCHULE:      "🎓 *Schulbildung*\n\nУкажи: годы, что окончил, где.\n"
                 "Например: `2018–2020, Mittlere Reife, Schule Nr. 12, Duschanbe`",
    WEITERBILDUNG: "📜 *Weitere Ausbildung / Studium / Kurse*\n\n"
                 "Например: `2024, Ausbildung zum Hotelfachmann, IHK Würzburg`\n"
                 "или `2023, Integrationskurs B1, VHS Kitzingen`" + SKIP,

    ERFAHRUNG:   "💪 *Berufserfahrung*\n\n"
                 "Каждое место работы с новой строки в формате:\n"
                 "`период — должность — компания — чем занимался`\n\n"
                 "Например:\n"
                 "`2022–2024 — Kellner — Restaurant Adler, Kitzingen — обслуживание до 40 гостей, касса`\n"
                 "`2021–2022 — Küchenhilfe — Café Roma, Würzburg — подготовка блюд, склад`" + SKIP,
    PRAKTIKA:    "🔧 *Praktika*\n\nСтажировки и практика.\n"
                 "Например: `03.2025, 4 Wochen Praktikum im Rettungsdienst, BRK Kitzingen`" + SKIP,

    SPRACHEN:    "🌍 *Sprachkenntnisse*\n\nЯзык и уровень через запятую.\n"
                 "Например: `Tadschikisch – Muttersprache, Russisch – C2, Deutsch – B2, Englisch – A2`",
    FACHKENNTNISSE: "🛠 *Fachkenntnisse / EDV*\n\n"
                 "Программы, техника, профессиональные навыки.\n"
                 "Например: `MS Office – gut, Kassensysteme, HACCP-Grundlagen`" + SKIP,
    FUEHRERSCHEIN: "🚗 *Führerschein*\n\nНапример: `Klasse B, seit 2023`" + SKIP,

    STAERKEN:    "⭐ *Persönliche Stärken*\n\n"
                 "Например: `teamfähig, belastbar, zuverlässig, gästeorientiert`" + SKIP,
    INTERESSEN:  "🎯 *Interessen & Engagement*\n\n"
                 "Хобби, волонтёрство, спорт.\n"
                 "Например: `Fußball im Verein, Fitness, ehrenamtliche Übersetzungshilfe`" + SKIP,
    MOTIVATION:  "🔥 *Warum dieser Beruf?*\n\n"
                 "Пару предложений своими словами — можно по-русски, я переведу.\n"
                 "Это самая важная часть Anschreiben.\n\n"
                 "Например: `Люблю работать с людьми, был официантом, хочу расти в гостиничном деле`",

    PHOTO:       "📸 *Bewerbungsfoto*\n\n"
                 "Пришли фото — оно встанет в правый верхний угол резюме.\n"
                 "Лучше деловое, на светлом фоне.\n\n"
                 "_Или напиши_ *пропустить*",
}

FIELD_MAP = {
    NAME: "name", BIRTH_DATE: "birth_date", BIRTH_PLACE: "birth_place",
    NATIONALITY: "nationality", ADDRESS: "address", PHONE: "phone", EMAIL: "email",
    BERUF: "beruf", UNTERNEHMEN: "unternehmen", START_DATE: "start_date",
    SCHULE: "schule", WEITERBILDUNG: "weiterbildung",
    ERFAHRUNG: "erfahrung", PRAKTIKA: "praktika",
    SPRACHEN: "sprachen", FACHKENNTNISSE: "fachkenntnisse",
    FUEHRERSCHEIN: "fuehrerschein", STAERKEN: "staerken",
    INTERESSEN: "interessen", MOTIVATION: "motivation",
}

SKIP_WORDS = ["пропустить", "нет", "skip", "-", "keine", "нету"]


# ================= ДИАЛОГ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = NAME
    await update.message.reply_text(
        "🇩🇪 *Ausbildung Agent*\n\n"
        "Составлю профессиональный немецкий Lebenslauf и Anschreiben.\n\n"
        "Будет 20 вопросов — часть можно пропустить. "
        "Чем подробнее ответишь, тем сильнее получатся документы.\n\n"
        "Поехали 👇",
        parse_mode="Markdown"
    )
    await update.message.reply_text(QUESTIONS[NAME], parse_mode="Markdown")
    return NAME


async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("state", NAME)
    answer = update.message.text.strip()

    key = FIELD_MAP.get(current)
    if key:
        context.user_data[key] = "" if answer.lower() in SKIP_WORDS else answer

    nxt = current + 1
    context.user_data["state"] = nxt
    await update.message.reply_text(QUESTIONS[nxt], parse_mode="Markdown")
    return nxt


async def collect_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        try:
            tg_file = await update.message.photo[-1].get_file()
            raw = await tg_file.download_as_bytearray()
            context.user_data["photo"] = bytes(raw)
            await update.message.reply_text("📸 Фото принято.")
        except Exception as e:
            logger.error(f"Photo error: {e}")
            await update.message.reply_text("⚠️ Фото не удалось загрузить, продолжаем без него.")
    return await show_summary(update, context)


async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data

    def val(k, default="—"):
        return d.get(k) or default

    summary = (
        "✅ *Проверь данные:*\n\n"
        f"👤 {val('name')}\n"
        f"📅 {val('birth_date')} · {val('birth_place')}\n"
        f"🌐 {val('nationality', 'не указано')}\n"
        f"🏠 {val('address')}\n"
        f"📞 {val('phone')} · 📧 {val('email')}\n\n"
        f"💼 *{val('beruf')}*\n"
        f"🏢 {val('unternehmen', 'без конкретной фирмы')}\n"
        f"🗓 ab {val('start_date', 'nach Absprache')}\n\n"
        f"🎓 {val('schule')}\n"
        f"📜 {val('weiterbildung', 'нет')}\n"
        f"💪 {val('erfahrung', 'нет')}\n"
        f"🔧 {val('praktika', 'нет')}\n\n"
        f"🌍 {val('sprachen')}\n"
        f"🛠 {val('fachkenntnisse', 'нет')}\n"
        f"🚗 {val('fuehrerschein', 'нет')}\n"
        f"⭐ {val('staerken', 'нет')}\n"
        f"🎯 {val('interessen', 'нет')}\n"
        f"🔥 {val('motivation', 'нет')}\n"
        f"📸 {'фото есть' if d.get('photo') else 'без фото'}\n\n"
        "Создаём PDF?"
    )
    keyboard = [["✅ Да, создать PDF!", "🔄 Начать заново"]]
    await update.message.reply_text(
        summary, parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data["state"] = CONFIRM
    return CONFIRM


# ================= ГЕНЕРАЦИЯ =================

PROMPT_TEMPLATE = """Du bist ein erfahrener deutscher Bewerbungsberater und erstellst Unterlagen
für Bewerber aus Zentralasien, die eine Ausbildung in Deutschland suchen.

KANDIDATENDATEN (Rohangaben, teils auf Russisch):
- Name: {name}
- Geburtsdatum: {birth_date}
- Geburtsort: {birth_place}
- Staatsangehörigkeit: {nationality}
- Adresse: {address}
- Telefon: {phone}
- E-Mail: {email}
- Angestrebte Ausbildung: {beruf}
- Wunschunternehmen: {unternehmen}
- Verfügbar ab: {start_date}
- Schulbildung: {schule}
- Weitere Ausbildung/Kurse: {weiterbildung}
- Berufserfahrung: {erfahrung}
- Praktika: {praktika}
- Sprachen: {sprachen}
- Fachkenntnisse/EDV: {fachkenntnisse}
- Führerschein: {fuehrerschein}
- Stärken: {staerken}
- Interessen: {interessen}
- Motivation (eigene Worte, ggf. Russisch): {motivation}

REGELN:
1. Antworte AUSSCHLIESSLICH mit gültigem JSON. Kein Markdown, keine ```-Blöcke, kein Text davor oder danach.
2. Alle Ausgaben auf Deutsch. Russische Eingaben sinngemäß ins Deutsche übertragen, nicht wörtlich.
3. NIEMALS Platzhalter wie [Datum einfügen] oder [Name der Schule]. Fehlt eine Angabe, lasse den Eintrag weg.
4. Erfinde keine Arbeitgeber, Schulen, Noten oder Zeiträume, die nicht in den Daten stehen.
5. Berufserfahrung: pro Station 2-3 konkrete Tätigkeits-Stichpunkte, jeweils mit einem Verb beginnend
   (z.B. "Betreuung von bis zu 40 Gästen pro Schicht"). Keine Floskeln.
6. Anschreiben: 4 Absätze.
   - Absatz 1: konkreter Einstieg mit Bezug auf Beruf und Unternehmen. NICHT mit "Hiermit bewerbe ich mich" beginnen.
   - Absatz 2: bisherige Erfahrung und was daraus für diesen Beruf nützlich ist.
   - Absatz 3: Motivation, Sprachkenntnisse, persönliche Stärken.
   - Absatz 4: Verfügbarkeit und Bitte um ein Vorstellungsgespräch.
   Sie-Form, sachlich, keine Übertreibungen.
7. Zeiträume im Format "2022 – 2024", "seit 2024", "03/2025".

JSON-STRUKTUR (genau einhalten):
{{
  "personal": {{
    "name": "", "address": "", "phone": "", "email": "",
    "birth_date": "", "birth_place": "", "nationality": ""
  }},
  "job_title": "Angestrebte Ausbildung als ...",
  "profile": "2-3 Sätze Kurzprofil",
  "experience": [
    {{"period": "", "title": "", "org": "", "bullets": ["", ""]}}
  ],
  "praktika": [
    {{"period": "", "title": "", "org": ""}}
  ],
  "education": [
    {{"period": "", "title": "", "org": "", "bullets": [""]}}
  ],
  "languages": [{{"name": "Deutsch", "level": "B2"}}],
  "skills": [{{"label": "EDV-Kenntnisse", "value": "MS Office: gut"}}],
  "interests": [{{"label": "Engagement", "value": ""}}],
  "letter": {{
    "recipient": "Firmenname\\nStraße\\nPLZ Ort",
    "city": "Wohnort des Kandidaten",
    "subject": "Bewerbung um einen Ausbildungsplatz als ...",
    "salutation": "Sehr geehrte Damen und Herren,",
    "paragraphs": ["", "", "", ""],
    "closing": "Mit freundlichen Grüßen"
  }}
}}"""


def parse_json_response(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("В ответе модели не найден JSON")
    return json.loads(t[start:end + 1])


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "заново" in update.message.text.lower():
        return await start(update, context)

    await update.message.reply_text(
        "⏳ Генерирую документы... ~25 секунд.",
        reply_markup=ReplyKeyboardRemove()
    )

    d = context.user_data
    prompt = PROMPT_TEMPLATE.format(**{
        k: (d.get(k) or "nicht angegeben") for k in [
            "name", "birth_date", "birth_place", "nationality", "address", "phone",
            "email", "beruf", "unternehmen", "start_date", "schule", "weiterbildung",
            "erfahrung", "praktika", "sprachen", "fachkenntnisse", "fuehrerschein",
            "staerken", "interessen", "motivation"]
    })

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        data = parse_json_response(response.text)

        # Личные данные берём из ответов пользователя, а не из фантазии модели
        personal = data.setdefault("personal", {})
        for k in ("name", "address", "phone", "email",
                  "birth_date", "birth_place", "nationality"):
            if d.get(k):
                personal[k] = d[k]

        pdf_buf = build_pdf(data, d.get("photo"))
        name_clean = (d.get("name") or "Kandidat").replace(" ", "_")

        await update.message.reply_document(
            document=pdf_buf,
            filename=f"Bewerbung_{name_clean}.pdf",
            caption=(
                f"✅ *Bewerbungsunterlagen für {d.get('name','')}*\n\n"
                f"📄 Lebenslauf + Anschreiben — *{d.get('beruf','')}*\n\n"
                "Viel Erfolg! 🍀\n\nНовое резюме: /start"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}\n\nПопробуй снова: /start")

    return ConversationHandler.END


# ================= ВЁРСТКА PDF =================

LEFT_W = 4.2 * cm
RIGHT_W = 11.8 * cm


def _styles():
    base = getSampleStyleSheet()
    return {
        "doctitle": ParagraphStyle("doctitle", parent=base["Normal"], fontName="Helvetica",
                                   fontSize=25, leading=29, textColor=DARK),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName="Helvetica",
                                  fontSize=12.5, leading=15, textColor=DARK,
                                  spaceBefore=2, spaceAfter=3),
        "label": ParagraphStyle("label", parent=base["Normal"], fontName="Helvetica",
                                fontSize=9, leading=13, textColor=LABEL),
        "value": ParagraphStyle("value", parent=base["Normal"], fontName="Helvetica",
                                fontSize=9.5, leading=13.5, textColor=DARK),
        "valuebold": ParagraphStyle("valuebold", parent=base["Normal"], fontName="Helvetica-Bold",
                                    fontSize=9.5, leading=13.5, textColor=DARK),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=9.5, leading=13.5, textColor=DARK,
                                 leftIndent=9, bulletIndent=0),
        "letter": ParagraphStyle("letter", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=10, leading=15, alignment=TA_JUSTIFY,
                                 textColor=DARK, spaceAfter=9),
        "letterline": ParagraphStyle("letterline", parent=base["Normal"], fontName="Helvetica",
                                     fontSize=10, leading=14, textColor=DARK),
        "right": ParagraphStyle("right", parent=base["Normal"], fontName="Helvetica",
                                fontSize=10, leading=14, alignment=TA_RIGHT, textColor=DARK),
        "subject": ParagraphStyle("subject", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=10.5, leading=14, textColor=DARK, spaceAfter=12),
    }


def _bar(space_before=13):
    """Голубая полоса-разделитель перед заголовком секции."""
    return HRFlowable(width=4.2 * cm, thickness=5, color=BAR, lineCap="butt",
                      spaceBefore=space_before, spaceAfter=6, hAlign="LEFT")


def _thin_rule():
    return HRFlowable(width="100%", thickness=0.7, color=RULE,
                      spaceBefore=2, spaceAfter=6)


def _rows_table(rows):
    t = Table(rows, colWidths=[LEFT_W, RIGHT_W])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _photo_flowable(photo_bytes, max_w=3.3 * cm, max_h=4.4 * cm):
    try:
        reader = ImageReader(io.BytesIO(photo_bytes))
        iw, ih = reader.getSize()
        ratio = min(max_w / iw, max_h / ih)
        return Image(io.BytesIO(photo_bytes), width=iw * ratio, height=ih * ratio)
    except Exception as e:
        logger.error(f"Photo render error: {e}")
        return Spacer(1, 1)


def build_pdf(data: dict, photo_bytes: bytes | None = None) -> io.BytesIO:
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="Bewerbungsunterlagen",
    )

    p = data.get("personal", {})
    story = []

    # ---------- Шапка: заголовок слева, фото справа ----------
    title_cell = [Paragraph("Lebenslauf", s["doctitle"])]
    if data.get("job_title"):
        title_cell.append(Spacer(1, 3))
        title_cell.append(Paragraph(data["job_title"], s["label"]))

    photo_cell = _photo_flowable(photo_bytes) if photo_bytes else ""
    head = Table([[title_cell, photo_cell]], colWidths=[12 * cm, 4 * cm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "TOP"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(head)

    def section(title, rows=None, first=False):
        """Секция переносится на новую страницу целиком, без отрыва заголовка."""
        block = [
            _bar(space_before=8 if first else 13),
            Paragraph(title, s["section"]),
            _thin_rule(),
        ]
        if rows:
            block.append(_rows_table(rows))
        story.append(KeepTogether(block))

    # ---------- Persönliche Daten ----------
    rows = [[Paragraph("Name", s["label"]), Paragraph(p.get("name", ""), s["value"])]]
    if p.get("address"):
        rows.append([Paragraph("Adresse", s["label"]),
                     Paragraph(p["address"].replace(", ", "<br/>"), s["value"])])
    if p.get("phone"):
        rows.append([Paragraph("Telefon", s["label"]), Paragraph(p["phone"], s["value"])])
    if p.get("email"):
        rows.append([Paragraph("E-Mail", s["label"]), Paragraph(p["email"], s["value"])])
    geb = " in ".join(x for x in [p.get("birth_date"), p.get("birth_place")] if x)
    if geb:
        rows.append([Paragraph("Geburtsdaten", s["label"]), Paragraph(geb, s["value"])])
    if p.get("nationality"):
        rows.append([Paragraph("Staatsangehörigkeit", s["label"]),
                     Paragraph(p["nationality"], s["value"])])
    section("Persönliche Daten", rows=rows, first=True)

    # ---------- Kurzprofil ----------
    if data.get("profile"):
        section("Kurzprofil",
                rows=[[Paragraph("", s["label"]), Paragraph(data["profile"], s["value"])]])

    # ---------- Karriere ----------
    exp = data.get("experience") or []
    if exp:
        rows = []
        for it in exp:
            right = []
            if it.get("org"):
                right.append(Paragraph(it["org"], s["value"]))
            if it.get("title"):
                right.append(Paragraph(it["title"], s["valuebold"]))
            for b in it.get("bullets") or []:
                right.append(Paragraph(b, s["bullet"], bulletText="–"))
            rows.append([Paragraph(it.get("period", ""), s["label"]), right])
        section("Karriere", rows=rows)

    # ---------- Praktika ----------
    prak = data.get("praktika") or []
    if prak:
        rows = []
        for it in prak:
            right = []
            if it.get("org"):
                right.append(Paragraph(it["org"], s["value"]))
            if it.get("title"):
                right.append(Paragraph(it["title"], s["valuebold"]))
            rows.append([Paragraph(it.get("period", ""), s["label"]), right])
        section("Praktika", rows=rows)

    # ---------- Ausbildung ----------
    edu = data.get("education") or []
    if edu:
        rows = []
        for it in edu:
            right = []
            if it.get("title"):
                right.append(Paragraph(it["title"], s["valuebold"]))
            if it.get("org"):
                right.append(Paragraph(it["org"], s["value"]))
            for b in it.get("bullets") or []:
                right.append(Paragraph(b, s["bullet"], bulletText="–"))
            rows.append([Paragraph(it.get("period", ""), s["label"]), right])
        section("Ausbildung", rows=rows)

    # ---------- Interessen ----------
    interests = data.get("interests") or []
    if interests:
        rows = [[Paragraph(i.get("label", ""), s["label"]),
                 Paragraph(i.get("value", ""), s["value"])] for i in interests]
        section("Interessen", rows=rows)

    # ---------- Kenntnisse ----------
    langs = data.get("languages") or []
    skills = data.get("skills") or []
    if langs or skills:
        rows = []
        if langs:
            lang_txt = "<br/>".join(f"{l.get('name','')}: {l.get('level','')}" for l in langs)
            rows.append([Paragraph("Sprachen", s["label"]), Paragraph(lang_txt, s["value"])])
        for sk in skills:
            rows.append([Paragraph(sk.get("label", ""), s["label"]),
                         Paragraph(sk.get("value", ""), s["value"])])
        section("Kenntnisse", rows=rows)

    # ---------- Anschreiben ----------
    letter = data.get("letter") or {}
    if letter:
        story.append(PageBreak())

        story.append(Paragraph(f"<b>{p.get('name','')}</b>", s["letterline"]))
        for bit in (p.get("address"), p.get("phone"), p.get("email")):
            if bit:
                story.append(Paragraph(bit, s["letterline"]))
        story.append(Spacer(1, 26))

        for line in str(letter.get("recipient", "")).split("\n"):
            if line.strip():
                story.append(Paragraph(line.strip(), s["letterline"]))
        story.append(Spacer(1, 20))

        if letter.get("city"):
            story.append(Paragraph(letter["city"], s["right"]))
        story.append(Spacer(1, 18))

        if letter.get("subject"):
            story.append(Paragraph(letter["subject"], s["subject"]))
        if letter.get("salutation"):
            story.append(Paragraph(letter["salutation"], s["letter"]))
        for para in letter.get("paragraphs") or []:
            story.append(Paragraph(para, s["letter"]))

        story.append(Spacer(1, 8))
        story.append(Paragraph(letter.get("closing", "Mit freundlichen Grüßen"), s["letterline"]))
        story.append(Spacer(1, 30))
        story.append(Paragraph(p.get("name", ""), s["letterline"]))

    doc.build(story)
    buf.seek(0)
    return buf


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start — начать заново.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ================= ВЕБ-СЕРВЕР ДЛЯ RENDER =================

async def health(request):
    return web.Response(text="Bot is alive")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Health server running on port {PORT}")


async def main():
    await run_web_server()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    states = {st: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)]
              for st in range(NAME, PHOTO)}
    states[PHOTO] = [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND,
                                    collect_photo)]
    states[CONFIRM] = [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)]

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states=states,
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot polling started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
