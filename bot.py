import os
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import google.generativeai as genai
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

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
        f"💼 {d.get('beruf','—')} @ {d.get('unternehmen','—') or 'allgemein'}\n"
        f"🎓 {d.get('bildung','—')}\n"
        f"🌍 {d.get('sprachen','—')}\n"
        f"💪 {d.get('erfahrung','—') or 'нет'}\n"
        f"⭐ {d.get('staerken','—') or 'не указаны'}\n\n"
        "Всё верно?"
    )
    keyboard = [["✅ Да, создать PDF!", "🔄 Начать заново"]]
    await update.message.reply_text(
        summary, parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data["state"] = CONFIRM
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "заново" in text.lower():
        return await start(update, context)

    await update.message.reply_text(
        "⏳ Генерирую документы... ~20 секунд.",
        reply_markup=ReplyKeyboardRemove()
    )

    d = context.user_data
    prompt = f"""Du bist ein professioneller deutscher Bewerbungsassistent.
Erstelle für folgenden Kandidaten professionelle Bewerbungsunterlagen auf Deutsch.

Kandidatendaten:
- Name: {d.get('name','')}
- Geburtsdatum: {d.get('birth_date','')}
- Geburtsort: {d.get('birth_place','')}
- Adresse: {d.get('address','')}
- Telefon: {d.get('phone','')}
- E-Mail: {d.get('email','')}
- Angestrebte Ausbildung: {d.get('beruf','')}
- Unternehmen: {d.get('unternehmen','') or 'allgemein'}
- Bildung: {d.get('bildung','')}
- Sprachen: {d.get('sprachen','')}
- Erfahrung: {d.get('erfahrung','') or 'keine'}
- Stärken: {d.get('staerken','') or 'nicht angegeben'}

Erstelle:
1. Professionellen tabellarischen LEBENSLAUF
2. Überzeugendes ANSCHREIBEN

Trenne mit ===LEBENSLAUF=== und ===ANSCHREIBEN===
Nur Dokumente, keine Erklärungen."""

    try:
        response = model.generate_content(prompt)
        result = response.text
        lv_text = ""
        as_text = ""

        if "===LEBENSLAUF===" in result:
            s = result.index("===LEBENSLAUF===") + len("===LEBENSLAUF===")
            e = result.index("===ANSCHREIBEN===") if "===ANSCHREIBEN===" in result else len(result)
            lv_text = result[s:e].strip()
        if "===ANSCHREIBEN===" in result:
            s = result.index("===ANSCHREIBEN===") + len("===ANSCHREIBEN===")
            as_text = result[s:].strip()
        if not lv_text and not as_text:
            lv_text = result.strip()

        pdf_buf = generate_pdf(lv_text, as_text, d.get('name', 'Kandidat'))
        name_clean = d.get('name', 'Kandidat').replace(' ', '_')

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


def generate_pdf(lv_text: str, as_text: str, name: str) -> io.BytesIO:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_s = ParagraphStyle('T', parent=styles['Heading1'],
                             fontSize=16, spaceAfter=12, alignment=TA_CENTER)
    head_s = ParagraphStyle('H', parent=styles['Heading2'],
                            fontSize=11, spaceAfter=6, spaceBefore=10)
    body_s = ParagraphStyle('B', parent=styles['Normal'],
                            fontSize=10, spaceAfter=3, leading=14)
    story = []

    def add_section(text, label):
        story.append(Paragraph(label, title_s))
        story.append(Spacer(1, 0.3*cm))
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                story.append(Spacer(1, 0.15*cm))
            elif line.startswith('##') or (line.isupper() and 3 < len(line) < 40):
                clean = line.replace('#', '').strip()
                story.append(Paragraph(clean, head_s))
            else:
                clean = line.replace('**', '').replace('*', '').replace('#', '')
                if clean:
                    story.append(Paragraph(clean, body_s))

    if lv_text:
        add_section(lv_text, "LEBENSLAUF")
    if as_text:
        story.append(Spacer(1, 0.8*cm))
        add_section(as_text, "ANSCHREIBEN")

    doc.build(story)
    buf.seek(0)
    return buf


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start — начать заново.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    states = {s: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)]
              for s in range(NAME, CONFIRM)}
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
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
