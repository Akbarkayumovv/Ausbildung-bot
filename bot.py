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
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# States
(NAME, BIRTH_DATE, BIRTH_PLACE, ADDRESS, PHONE, EMAIL,
 BERUF, UNTERNEHMEN, BILDUNG, SPRACHEN, ERFAHRUNG, STAERKEN, CONFIRM) = range(13)

QUESTIONS = {
    NAME:        "👤 Wie heißt du? (Vor- und Nachname)\n\nПиши на немецком, например: *Ali Mustermann*",
    BIRTH_DATE:  "📅 Geburtsdatum?\n\nФормат: *01.01.2000*",
    BIRTH_PLACE: "📍 Geburtsort?\n\nНапример: *Dushanbe, Tadschikistan*",
    ADDRESS:     "🏠 Aktuelle Adresse in Deutschland?\n\nНапример: *Musterstraße 1, 97318 Kitzingen*",
    PHONE:       "📞 Telefonnummer?\n\nНапример: *+49 151 12345678*",
    EMAIL:       "📧 E-Mail-Adresse?\n\nНапример: *ali@gmail.com*",
    BERUF:       "💼 Welche Ausbildung möchtest du machen?\n\nНапример: *Koch, Kfz-Mechatroniker, Krankenpfleger*",
    UNTERNEHMEN: "🏢 Bei welchem Unternehmen bewirbst du dich? (или напиши *пропустить*)\n\nНапример: *BMW, BRK Kitzingen*",
    BILDUNG:     "🎓 Welchen Schulabschluss hast du?\n\nНапример: *Mittlere Reife, Abitur, Berufsausbildung als Hotelfachmann*",
    SPRACHEN:    "🌍 Welche Sprachen sprichst du?\n\nНапример: *Deutsch B2, Russisch Muttersprache, Englisch A2*",
    ERFAHRUNG:   "💪 Hast du Berufserfahrung? Erzähl kurz.\n\nНапример: *2 Jahre als Kellner im Restaurant in Kitzingen*\nЕсли нет — напиши *нет*",
    STAERKEN:    "⭐ Stärken und Hobbys? (или напиши *пропустить*)\n\nНапример: *teamfähig, zuverlässig, Fußball, Lesen*",
}

FIELD_NAMES = {
    NAME: "Name",
    BIRTH_DATE: "Geburtsdatum",
    BIRTH_PLACE: "Geburtsort",
    ADDRESS: "Adresse",
    PHONE: "Telefon",
    EMAIL: "E-Mail",
    BERUF: "Ausbildung",
    UNTERNEHMEN: "Unternehmen",
    BILDUNG: "Bildung",
    SPRACHEN: "Sprachen",
    ERFAHRUNG: "Erfahrung",
    STAERKEN: "Stärken",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🇩🇪 *Willkommen beim Ausbildung Agent!*\n\n"
        "Я помогу тебе создать профессиональное немецкое резюме и сопроводительное письмо для Ausbildung.\n\n"
        "Отвечай на вопросы — в конце получишь готовый PDF документ.\n\n"
        "Начнём! 👇",
        parse_mode="Markdown"
    )
    await update.message.reply_text(QUESTIONS[NAME], parse_mode="Markdown")
    return NAME


async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state", NAME)
    answer = update.message.text.strip()

    # Map current state to field key
    field_map = {
        NAME: "name", BIRTH_DATE: "birth_date", BIRTH_PLACE: "birth_place",
        ADDRESS: "address", PHONE: "phone", EMAIL: "email",
        BERUF: "beruf", UNTERNEHMEN: "unternehmen", BILDUNG: "bildung",
        SPRACHEN: "sprachen", ERFAHRUNG: "erfahrung", STAERKEN: "staerken"
    }

    key = field_map.get(state)
    if key:
        skip_val = answer if answer.lower() not in ["пропустить", "нет", "skip"] else ""
        context.user_data[key] = skip_val

    next_state = state + 1

    if next_state <= STAERKEN:
        context.user_data["state"] = next_state
        await update.message.reply_text(QUESTIONS[next_state], parse_mode="Markdown")
        return next_state
    else:
        # Show summary
        d = context.user_data
        summary = (
            "✅ *Данные собраны! Проверь:*\n\n"
            f"👤 Имя: {d.get('name', '—')}\n"
            f"📅 Дата рождения: {d.get('birth_date', '—')}\n"
            f"📍 Место рождения: {d.get('birth_place', '—')}\n"
            f"🏠 Адрес: {d.get('address', '—')}\n"
            f"📞 Телефон: {d.get('phone', '—')}\n"
            f"📧 Email: {d.get('email', '—')}\n"
            f"💼 Ausbildung: {d.get('beruf', '—')}\n"
            f"🏢 Компания: {d.get('unternehmen', '—') or 'не указана'}\n"
            f"🎓 Образование: {d.get('bildung', '—')}\n"
            f"🌍 Языки: {d.get('sprachen', '—')}\n"
            f"💪 Опыт: {d.get('erfahrung', '—') or 'нет'}\n"
            f"⭐ Сильные стороны: {d.get('staerken', '—') or 'не указаны'}\n\n"
            "Всё верно? Генерировать документы?"
        )
        keyboard = [["✅ Да, создать PDF!", "🔄 Начать заново"]]
        await update.message.reply_text(
            summary,
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        context.user_data["state"] = CONFIRM
        return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if "заново" in text.lower() or "начать" in text.lower():
        return await start(update, context)

    await update.message.reply_text(
        "⏳ Генерирую документы... Это займёт ~15 секунд.",
        reply_markup=ReplyKeyboardRemove()
    )

    d = context.user_data

    prompt = f"""Du bist ein professioneller deutscher Bewerbungsassistent. 
Erstelle für folgenden Kandidaten professionelle Bewerbungsunterlagen auf Deutsch.

Kandidatendaten:
- Name: {d.get('name', '')}
- Geburtsdatum: {d.get('birth_date', '')}
- Geburtsort: {d.get('birth_place', '')}
- Adresse: {d.get('address', '')}
- Telefon: {d.get('phone', '')}
- E-Mail: {d.get('email', '')}
- Angestrebte Ausbildung: {d.get('beruf', '')}
- Unternehmen: {d.get('unternehmen', '') or 'allgemein'}
- Schulabschluss/Bildung: {d.get('bildung', '')}
- Sprachkenntnisse: {d.get('sprachen', '')}
- Berufserfahrung: {d.get('erfahrung', '') or 'keine'}
- Stärken/Hobbys: {d.get('staerken', '') or 'nicht angegeben'}

Erstelle:
1. Einen professionellen tabellarischen LEBENSLAUF
2. Ein überzeugendes ANSCHREIBEN für die Ausbildungsstelle

Trenne die Dokumente mit: ===LEBENSLAUF=== und ===ANSCHREIBEN===
Schreibe nur die Dokumente, keine Erklärungen. Fehlende Informationen sinnvoll ergänzen."""

    try:
        response = model.generate_content(prompt)
        text_result = response.text

        lv_text = ""
        as_text = ""

        if "===LEBENSLAUF===" in text_result:
            start_lv = text_result.index("===LEBENSLAUF===") + len("===LEBENSLAUF===")
            end_lv = text_result.index("===ANSCHREIBEN===") if "===ANSCHREIBEN===" in text_result else len(text_result)
            lv_text = text_result[start_lv:end_lv].strip()

        if "===ANSCHREIBEN===" in text_result:
            start_as = text_result.index("===ANSCHREIBEN===") + len("===ANSCHREIBEN===")
            as_text = text_result[start_as:].strip()

        if not lv_text and not as_text:
            lv_text = text_result.strip()

        # Generate PDF
        pdf_buffer = generate_pdf(lv_text, as_text, d.get('name', 'Kandidat'))

        await update.message.reply_document(
            document=pdf_buffer,
            filename=f"Bewerbung_{d.get('name', 'Kandidat').replace(' ', '_')}.pdf",
            caption=(
                f"✅ *Bewerbungsunterlagen für {d.get('name', '')}*\n\n"
                f"📄 Lebenslauf + Anschreiben für *{d.get('beruf', '')}*\n\n"
                "Viel Erfolg bei deiner Bewerbung! 🍀\n\n"
                "Для нового резюме напиши /start"
            ),
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(
            "❌ Ошибка при генерации. Попробуй ещё раз — напиши /start"
        )

    return ConversationHandler.END


def generate_pdf(lv_text: str, as_text: str, name: str) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'Title', parent=styles['Heading1'],
        fontSize=16, spaceAfter=12, alignment=TA_CENTER
    )
    section_style = ParagraphStyle(
        'Section', parent=styles['Heading2'],
        fontSize=12, spaceAfter=8, spaceBefore=12,
        textColor='#1a1a2e'
    )
    body_style = ParagraphStyle(
        'Body', parent=styles['Normal'],
        fontSize=10, spaceAfter=4, leading=14
    )

    story = []

    if lv_text:
        story.append(Paragraph("LEBENSLAUF", title_style))
        story.append(Spacer(1, 0.3*cm))
        for line in lv_text.split('\n'):
            line = line.strip()
            if not line:
                story.append(Spacer(1, 0.2*cm))
            elif line.startswith('##') or line.isupper() and len(line) > 3:
                clean = line.replace('#', '').strip()
                story.append(Paragraph(clean, section_style))
            else:
                clean = line.replace('**', '<b>').replace('**', '</b>')
                clean = clean.replace('*', '')
                story.append(Paragraph(clean, body_style))

    if as_text:
        story.append(Spacer(1, 1*cm))
        story.append(Paragraph("ANSCHREIBEN", title_style))
        story.append(Spacer(1, 0.3*cm))
        for line in as_text.split('\n'):
            line = line.strip()
            if not line:
                story.append(Spacer(1, 0.2*cm))
            else:
                clean = line.replace('**', '').replace('*', '')
                story.append(Paragraph(clean, body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отменено. Напиши /start чтобы начать заново.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            BIRTH_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            BIRTH_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            BERUF: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            UNTERNEHMEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            BILDUNG: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            SPRACHEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            ERFAHRUNG: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            STAERKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
