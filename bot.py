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
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"))

# ---------- Фирменные цвета документа ----------
ACCENT = colors.HexColor("#1F3A5F")   # тёмно-синий
GREY = colors.HexColor("#6B7280")
LINE = colors.HexColor("#C9D2DD")

(NAME, BIRTH_DATE, BIRTH_PLACE, ADDRESS, PHONE, EMAIL,
 BERUF, UNTERNEHMEN, BILDUNG, SPRACHEN, ERFAHRUNG, STAERKEN, CONFIRM) = range(13)

QUESTIONS = {
    NAME:        "👤 Wie heißt du? (Vor- und Nachname)\n\nНапример: *Ali Mustermann*",
    BIRTH_DATE:  "📅 Geburtsdatum?\n\nФормат: *01.01.2000*",
    BIRTH_PLACE: "📍 Geburtsort?\n\nНапример: *Dushanbe, Tadschikistan*",
    ADDRESS:     "🏠 Aktuelle Adresse in Deutschland?\n\nНапример: *Musterstraße 1, 97318 Kitzingen*",
    PHONE:       "📞 Telefonnummer?\n\nНапример: *+49 151 12345678*",
    EMAIL:       "📧 E-Mail-Adresse?\n\nНапример: *ali@gmail.com*",
    BERUF:       "💼 Welche Ausbildung möchtest du machen?\n\nНапример: *Koch, Kfz-Mechatroniker, Krankenpfleger*",
    UNTERNEHMEN: "🏢 Bei welchem Unternehmen? (или напиши *пропустить*)\n\nНапример: *BMW, BRK Kitzingen*",
    BILDUNG:     "🎓 Welchen Schulabschluss hast du?\n\nНапример: *Mittlere Reife, Abitur*",
    SPRACHEN:    "🌍 Welche Sprachen sprichst du?\n\nНапример: *Deutsch B2, Russisch, Englisch A2*",
    ERFAHRUNG:   "💪 Berufserfahrung? (или напиши *нет*)\n\nНапример: *2 Jahre als Kellner in Kitzingen*",
    STAERKEN:    "⭐ Stärken und Hobbys? (или напиши *пропустить*)\n\nНапример: *teamfähig, Fußball, Lesen*",
}

FIELD_MAP = {
    NAME: "name", BIRTH_DATE: "birth_date", BIRTH_PLACE: "birth_place",
    ADDRESS: "address", PHONE: "phone", EMAIL: "email",
    BERUF: "beruf", UNTERNEHMEN: "unternehmen", BILDUNG: "bildung",
    SPRACHEN: "sprachen", ERFAHRUNG: "erfahrung", STAERKEN: "staerken"
}


# ============ ДИАЛОГ ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = NAME
    await update.message.reply_text(
        "🇩🇪 *Willkommen beim Ausbildung Agent!*\n\n"
        "Я помогу создать профессиональное резюме и сопроводительное письмо для Ausbildung.\n\n"
        "Отвечай на вопросы — получишь готовый PDF! 👇",
        parse_mode="Markdown"
    )
    await update.message.reply_text(QUESTIONS[NAME], parse_mode="Markdown")
    return NAME


async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_state = context.user_data.get("state", NAME)
    answer = update.message.text.strip()
    key = FIELD_MAP.get(current_state)
    if key:
        val = answer if answer.lower() not in ["пропустить", "нет", "skip", "-"] else ""
        context.user_data[key] = val

    next_state = current_state + 1

    if next_state <= STAERKEN:
        context.user_data["state"] = next_state
        await update.message.reply_text(QUESTIONS[next_state], parse_mode="Markdown")
        return next_state

    d = context.user_data
    summary = (
        "✅ *Данные собраны! Проверь:*\n\n"
        f"👤 {d.get('name','—')}\n"
        f"📅 {d.get('birth_date','—')} | 📍 {d.get('birth_place','—')}\n"
        f"🏠 {d.get('address','—')}\n"
        f"📞 {d.get('phone','—')} | 📧 {d.get('email','—')}\n"
        f"💼 {d.get('beruf','—')} @ {d.get('unternehmen','') or 'allgemein'}\n"
        f"🎓 {d.get('bildung','—')}\n"
        f"🌍 {d.get('sprachen','—')}\n"
        f"💪 {d.get('erfahrung','') or 'нет'}\n"
        f"⭐ {d.get('staerken','') or 'не указаны'}\n\n"
        "Всё верно?"
    )
    keyboard = [["✅ Да, создать PDF!", "🔄 Начать заново"]]
    await update.message.reply_text(
        summary, parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data["state"] = CONFIRM
    return CONFIRM


# ============ ГЕНЕРАЦИЯ ============

PROMPT_TEMPLATE = """Du bist ein erfahrener deutscher Bewerbungsberater.
Erstelle aus den Kandidatendaten professionelle Bewerbungsunterlagen.

KANDIDATENDATEN:
- Name: {name}
- Geburtsdatum: {birth_date}
- Geburtsort: {birth_place}
- Adresse: {address}
- Telefon: {phone}
- E-Mail: {email}
- Angestrebte Ausbildung: {beruf}
- Wunschunternehmen: {unternehmen}
- Schulabschluss: {bildung}
- Sprachen: {sprachen}
- Berufserfahrung: {erfahrung}
- Stärken/Hobbys: {staerken}

REGELN:
1. Antworte AUSSCHLIESSLICH mit gültigem JSON. Kein Markdown, keine ```-Blöcke, kein Text davor oder danach.
2. Verwende NIEMALS Platzhalter wie [Datum einfügen] oder [Name der Schule]. Wenn eine Information fehlt, lasse das Feld weg oder formuliere sinnvoll aus dem Kontext.
3. Das Anschreiben muss konkret sein: Bezug auf den Beruf, auf die Erfahrung des Kandidaten und auf seine Sprachkenntnisse. Keine Floskeln wie "hiermit bewerbe ich mich" als einziger Inhalt.
4. Sprache: durchgehend Deutsch, Sie-Form im Anschreiben.
5. Bei Zeiträumen nutze das Format "2022 – 2024" oder "seit 2024". Erfinde keine exakten Daten, die nicht ableitbar sind.

JSON-STRUKTUR (genau einhalten):
{{
  "personal": {{
    "name": "",
    "address": "",
    "phone": "",
    "email": "",
    "birth_date": "",
    "birth_place": ""
  }},
  "job_title": "Angestrebte Ausbildung als ...",
  "profile": "2-3 Sätze Kurzprofil über den Kandidaten",
  "experience": [
    {{"period": "", "title": "", "org": "", "bullets": ["", ""]}}
  ],
  "education": [
    {{"period": "", "title": "", "org": ""}}
  ],
  "languages": [
    {{"name": "Deutsch", "level": "B2"}}
  ],
  "skills": ["", ""],
  "interests": ["", ""],
  "letter": {{
    "recipient": "Firmenname und ggf. Abteilung",
    "city": "Stadt des Kandidaten",
    "subject": "Bewerbung um einen Ausbildungsplatz als ...",
    "salutation": "Sehr geehrte Damen und Herren,",
    "paragraphs": ["Absatz 1", "Absatz 2", "Absatz 3", "Absatz 4"],
    "closing": "Mit freundlichen Grüßen"
  }}
}}"""


def parse_json_response(text: str) -> dict:
    """Достаём JSON из ответа модели, даже если она обернула его в ```."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("В ответе модели не найден JSON")
    return json.loads(t[start:end + 1])


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "заново" in text.lower():
        return await start(update, context)

    await update.message.reply_text(
        "⏳ Генерирую документы... ~20 секунд.",
        reply_markup=ReplyKeyboardRemove()
    )

    d = context.user_data
    prompt = PROMPT_TEMPLATE.format(
        name=d.get('name', ''),
        birth_date=d.get('birth_date', ''),
        birth_place=d.get('birth_place', ''),
        address=d.get('address', ''),
        phone=d.get('phone', ''),
        email=d.get('email', ''),
        beruf=d.get('beruf', ''),
        unternehmen=d.get('unternehmen', '') or 'nicht angegeben',
        bildung=d.get('bildung', ''),
        sprachen=d.get('sprachen', ''),
        erfahrung=d.get('erfahrung', '') or 'keine',
        staerken=d.get('staerken', '') or 'nicht angegeben',
    )

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        data = parse_json_response(response.text)

        # подстраховка: свои данные важнее, чем то, что придумала модель
        personal = data.setdefault("personal", {})
        for k in ("name", "address", "phone", "email", "birth_date", "birth_place"):
            if d.get(k):
                personal[k] = d[k]

        pdf_buf = build_pdf(data)
        name_clean = (d.get('name') or 'Kandidat').replace(' ', '_')

        await update.message.reply_document(
            document=pdf_buf,
            filename=f"Bewerbung_{name_clean}.pdf",
            caption=(
                f"✅ *Bewerbungsunterlagen für {d.get('name','')}*\n\n"
                f"📄 Lebenslauf + Anschreiben für *{d.get('beruf','')}*\n\n"
                "Viel Erfolg! 🍀\n\nДля нового резюме: /start"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}\n\nПопробуй снова: /start")

    return ConversationHandler.END


# ============ ВЁРСТКА PDF ============

def _styles():
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle("name", parent=base["Normal"], fontName="Helvetica-Bold",
                               fontSize=22, leading=26, textColor=ACCENT, spaceAfter=2),
        "role": ParagraphStyle("role", parent=base["Normal"], fontName="Helvetica",
                               fontSize=11, leading=14, textColor=GREY, spaceAfter=6),
        "contact": ParagraphStyle("contact", parent=base["Normal"], fontName="Helvetica",
                                  fontSize=9, leading=13, textColor=colors.black),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=10.5, leading=13, textColor=ACCENT,
                                  spaceBefore=12, spaceAfter=4),
        "period": ParagraphStyle("period", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=9, leading=13, textColor=GREY),
        "title": ParagraphStyle("title", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=10, leading=13),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=13.5),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=9.5, leading=13.5, leftIndent=10,
                                 bulletIndent=2, spaceAfter=1),
        "letterbody": ParagraphStyle("letterbody", parent=base["Normal"], fontName="Helvetica",
                                     fontSize=10, leading=15, alignment=TA_JUSTIFY,
                                     spaceAfter=9),
        "right": ParagraphStyle("right", parent=base["Normal"], fontName="Helvetica",
                                fontSize=10, leading=14, alignment=TA_RIGHT),
        "subject": ParagraphStyle("subject", parent=base["Normal"], fontName="Helvetica-Bold",
                                  fontSize=10.5, leading=14, spaceAfter=10),
    }


def _rule():
    return HRFlowable(width="100%", thickness=0.8, color=LINE,
                      spaceBefore=1, spaceAfter=6)


def _two_col(rows, s):
    """Таблица: слева период, справа содержимое."""
    t = Table(rows, colWidths=[3.4 * cm, 12.6 * cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def build_pdf(data: dict) -> io.BytesIO:
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.5 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="Bewerbungsunterlagen",
    )

    p = data.get("personal", {})
    story = []

    # ---------- Шапка ----------
    story.append(Paragraph(p.get("name", ""), s["name"]))
    if data.get("job_title"):
        story.append(Paragraph(data["job_title"], s["role"]))

    contact_bits = []
    if p.get("address"):
        contact_bits.append(p["address"])
    if p.get("phone"):
        contact_bits.append(p["phone"])
    if p.get("email"):
        contact_bits.append(p["email"])
    if contact_bits:
        story.append(Paragraph(" &nbsp;·&nbsp; ".join(contact_bits), s["contact"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.6, color=ACCENT, spaceAfter=4))

    story.append(Paragraph("LEBENSLAUF", s["section"]))

    # ---------- Кратко о себе ----------
    if data.get("profile"):
        story.append(Paragraph("Kurzprofil", s["section"]))
        story.append(_rule())
        story.append(Paragraph(data["profile"], s["body"]))

    # ---------- Личные данные ----------
    rows = []
    if p.get("birth_date"):
        rows.append([Paragraph("Geburtsdatum", s["period"]),
                     Paragraph(p["birth_date"], s["body"])])
    if p.get("birth_place"):
        rows.append([Paragraph("Geburtsort", s["period"]),
                     Paragraph(p["birth_place"], s["body"])])
    if rows:
        story.append(Paragraph("Persönliche Daten", s["section"]))
        story.append(_rule())
        story.append(_two_col(rows, s))

    # ---------- Опыт работы ----------
    exp = data.get("experience") or []
    if exp:
        story.append(Paragraph("Berufserfahrung", s["section"]))
        story.append(_rule())
        rows = []
        for item in exp:
            right = []
            head = item.get("title", "")
            if item.get("org"):
                head = f"{head} &nbsp;|&nbsp; {item['org']}" if head else item["org"]
            if head:
                right.append(Paragraph(head, s["title"]))
            for b in item.get("bullets") or []:
                right.append(Paragraph(b, s["bullet"], bulletText="•"))
            rows.append([Paragraph(item.get("period", ""), s["period"]), right])
        story.append(_two_col(rows, s))

    # ---------- Образование ----------
    edu = data.get("education") or []
    if edu:
        story.append(Paragraph("Ausbildung & Schulbildung", s["section"]))
        story.append(_rule())
        rows = []
        for item in edu:
            head = item.get("title", "")
            if item.get("org"):
                head = f"{head} &nbsp;|&nbsp; {item['org']}" if head else item["org"]
            rows.append([Paragraph(item.get("period", ""), s["period"]),
                         Paragraph(head, s["title"])])
        story.append(_two_col(rows, s))

    # ---------- Языки ----------
    langs = data.get("languages") or []
    if langs:
        story.append(Paragraph("Sprachkenntnisse", s["section"]))
        story.append(_rule())
        rows = [[Paragraph(l.get("name", ""), s["title"]),
                 Paragraph(l.get("level", ""), s["body"])] for l in langs]
        story.append(_two_col(rows, s))

    # ---------- Навыки ----------
    skills = data.get("skills") or []
    if skills:
        story.append(Paragraph("Kenntnisse & Stärken", s["section"]))
        story.append(_rule())
        story.append(Paragraph(" &nbsp;·&nbsp; ".join(skills), s["body"]))

    # ---------- Интересы ----------
    interests = data.get("interests") or []
    if interests:
        story.append(Paragraph("Interessen", s["section"]))
        story.append(_rule())
        story.append(Paragraph(" &nbsp;·&nbsp; ".join(interests), s["body"]))

    # ---------- Anschreiben ----------
    letter = data.get("letter") or {}
    if letter:
        story.append(PageBreak())

        story.append(Paragraph(p.get("name", ""), s["title"]))
        for bit in (p.get("address"), p.get("phone"), p.get("email")):
            if bit:
                story.append(Paragraph(bit, s["contact"]))
        story.append(Spacer(1, 22))

        if letter.get("recipient"):
            for line in str(letter["recipient"]).split("\n"):
                story.append(Paragraph(line, s["letterbody"]))
        story.append(Spacer(1, 14))

        if letter.get("city"):
            story.append(Paragraph(letter["city"], s["right"]))
        story.append(Spacer(1, 16))

        if letter.get("subject"):
            story.append(Paragraph(letter["subject"], s["subject"]))
        if letter.get("salutation"):
            story.append(Paragraph(letter["salutation"], s["letterbody"]))

        for para in letter.get("paragraphs") or []:
            story.append(Paragraph(para, s["letterbody"]))

        story.append(Spacer(1, 10))
        story.append(Paragraph(letter.get("closing", "Mit freundlichen Grüßen"), s["letterbody"]))
        story.append(Spacer(1, 26))
        story.append(Paragraph(p.get("name", ""), s["body"]))

    doc.build(story)
    buf.seek(0)
    return buf


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start — начать заново.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ============ ВЕБ-СЕРВЕР ДЛЯ RENDER ============

async def health(request):
    return web.Response(text="Bot is alive")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health server running on port {PORT}")


async def main():
    await run_web_server()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    states = {st: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)]
              for st in range(NAME, CONFIRM)}
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
