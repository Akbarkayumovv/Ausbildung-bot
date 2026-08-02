import os
import io
import json
import random
import time
from datetime import date
import logging
import asyncio
from aiohttp import web
from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, PicklePersistence
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
(NAME, BIRTH_DATE, BIRTH_PLACE, ADDRESS, PHONE, EMAIL,
 BERUF, UNTERNEHMEN, START_DATE,
 SCHULE, WEITERBILDUNG, ERFAHRUNG, PRAKTIKA,
 SPRACHEN, FACHKENNTNISSE, INTERESSEN, MOTIVATION,
 PHOTO, CONFIRM) = range(19)

# Состояния повторной заявки. Нумерация с 100, чтобы не попасть
# в диапазон обычного опроса — там состояние наращивается на +1.
NEU_BERUF, NEU_UNTERNEHMEN, NEU_START = 100, 101, 102

# Состояние точечной правки одного поля со сводки
EDITING = 200

# Данные кандидата хранятся ограниченное время — требование DSGVO
DATA_RETENTION_DAYS = 30

# Короткие подписи для меню правки
EDIT_LABELS = {
    NAME: "👤 Имя",
    BIRTH_DATE: "📅 Дата рожд.",
    BIRTH_PLACE: "📍 Место рожд.",
    ADDRESS: "🏠 Адрес",
    PHONE: "📞 Телефон",
    EMAIL: "📧 E-Mail",
    BERUF: "💼 Профессия",
    UNTERNEHMEN: "🏢 Фирма",
    START_DATE: "🗓 Начало",
    SCHULE: "🎓 Школа",
    WEITERBILDUNG: "📜 Курсы",
    ERFAHRUNG: "💪 Опыт",
    PRAKTIKA: "🔧 Практика",
    SPRACHEN: "🌍 Языки",
    FACHKENNTNISSE: "🛠 Навыки",
    INTERESSEN: "🎯 Интересы",
    MOTIVATION: "🔥 Мотивация",
    PHOTO: "📸 Фото",
}

# Поля, из которых собирается сохранённый профиль кандидата
PROFILE_FIELDS = [
    "name", "birth_date", "birth_place", "address", "phone", "email",
    "beruf", "unternehmen", "start_date", "schule", "weiterbildung",
    "erfahrung", "praktika", "sprachen", "fachkenntnisse",
    "interessen", "motivation",
]

SKIP = "\n\n_Нечего указать — напиши_ *пропустить*"

# Шаблоны вопросов. {ex} подставляется случайно, чтобы примеры не приедались.
TEMPLATES = {
    NAME:        "👤 *Vor- und Nachname*\n\nНапример: {ex}",
    BIRTH_DATE:  "📅 *Geburtsdatum*\n\nФормат: {ex}",
    BIRTH_PLACE: "📍 *Geburtsort*\n\nНапример: {ex}",
    ADDRESS:     "🏠 *Adresse*\n\nТекущий адрес — в Германии или за границей.\nНапример: {ex}",
    PHONE:       "📞 *Telefonnummer*\n\nНапример: {ex}",
    EMAIL:       "📧 *E-Mail*\n\nНапример: {ex}",

    BERUF:       "💼 *Angestrebte Ausbildung / Stelle*\n\n"
                 "Точное название профессии по-немецки.\nНапример: {ex}",
    UNTERNEHMEN: "🏢 *Unternehmen*\n\nКуда подаёшься — название и город.\nНапример: {ex}" + SKIP,
    START_DATE:  "🗓 *Verfügbar ab*\n\nКогда можешь начать.\nНапример: {ex}" + SKIP,

    SCHULE:      "🎓 *Schulbildung*\n\nГоды, что окончил, где.\nНапример: {ex}",
    WEITERBILDUNG: "📜 *Kurse, Weiterbildung, Studium*\n\nНапример: {ex}" + SKIP,

    ERFAHRUNG:   "💪 *Berufserfahrung*\n\n"
                 "Каждое место работы с новой строки:\n"
                 "`период — должность — компания — чем занимался`\n\n"
                 "Например:\n{ex}" + SKIP,
    PRAKTIKA:    "🔧 *Praktika*\n\nСтажировки и практика.\nНапример: {ex}" + SKIP,

    SPRACHEN:    "🌍 *Sprachkenntnisse*\n\nЯзык и уровень через запятую.\nНапример: {ex}",
    FACHKENNTNISSE: "🛠 *Fachkenntnisse*\n\n"
                 "Программы, техника, сертификаты, профессиональные умения.\n"
                 "Личные качества писать не нужно — я подберу их сам.\n\nНапример: {ex}" + SKIP,

    INTERESSEN:  "🎯 *Interessen & Engagement*\n\nХобби, спорт, волонтёрство.\nНапример: {ex}" + SKIP,
    MOTIVATION:  "🔥 *Почему именно эта профессия?*\n\n"
                 "Пару предложений своими словами — можно по-русски, переведу.\n"
                 "Это основа Anschreiben, отнесись серьёзно.\n\nНапример: {ex}",

    PHOTO:       "📸 *Bewerbungsfoto*\n\n"
                 "Пришли фото — оно встанет в правый верхний угол резюме.\n"
                 "Лучше деловое, на светлом фоне.\n\n_Или напиши_ *пропустить*",
}

# Пулы примеров: разные города, разные отрасли, мужские и женские имена
EXAMPLES = {
    NAME: ["`Rustam Ismoilov`", "`Aziza Karimova`", "`Dilshod Nazarov`", "`Malika Yusupova`"],
    BIRTH_DATE: ["`05.01.2002`", "`17.09.1999`", "`23.03.2005`"],
    BIRTH_PLACE: ["`Chudschand, Tadschikistan`", "`Samarkand, Usbekistan`",
                  "`Bischkek, Kirgisistan`", "`Almaty, Kasachstan`"],
    ADDRESS: ["`Bahnhofstraße 24, 04109 Leipzig`", "`Hauptstraße 11, 97318 Kitzingen`",
              "`ul. Rudaki 45, 734000 Duschanbe, Tadschikistan`",
              "`ul. Navoi 12, 100011 Taschkent, Usbekistan`",
              "`Lindenweg 7, 28195 Bremen`"],
    PHONE: ["`+49 151 12345678`", "`+49 160 9876543`", "`+49 176 4455667`"],
    EMAIL: ["`rustam.ismoilov@gmail.com`", "`a.karimova@web.de`", "`d.nazarov02@gmail.com`"],

    BERUF: ["`Fachkraft für Lagerlogistik`, `Pflegefachmann`, `Elektroniker für Betriebstechnik`",
            "`Kfz-Mechatroniker`, `Verkäuferin`, `Anlagenmechaniker SHK`",
            "`Hotelfachfrau`, `Fachinformatiker`, `Zerspanungsmechaniker`",
            "`Bäcker`, `Medizinische Fachangestellte`, `Maler und Lackierer`"],
    UNTERNEHMEN: ["`Autohaus Krieger, Nürnberg`", "`Seniorenzentrum St. Anna, Leipzig`",
                  "`Bäckerei Hofmann, Würzburg`", "`Elektro Schneider GmbH, Dresden`"],
    START_DATE: ["`01.08.2027`", "`sofort`", "`nach Absprache`", "`ab Februar 2027`"],

    SCHULE: ["`2017–2021, Schulabschluss (11 Klassen), Schule Nr. 8, Chudschand`",
             "`2016–2020, Mittlere Reife, Gesamtschule Bremen-Nord`",
             "`2015–2020, Abitur, Lyzeum Nr. 3, Samarkand`"],
    WEITERBILDUNG: ["`2024, Integrationskurs B1, VHS Dresden`",
                    "`2023, Schweißkurs MAG, Bildungszentrum Köln`",
                    "`2022–2024, Studium Ökonomie (2 Jahre), Universität Duschanbe`",
                    "`2025, Gabelstaplerschein, TÜV Nürnberg`"],

    ERFAHRUNG: [
        "`2022–2024 — Lagerhelfer — Möbelhaus Ritter, Nürnberg — Kommissionierung, Wareneingang`\n"
        "`2020–2022 — Fahrer — Kurierdienst Sattler, Erfurt — Auslieferung, Tourenplanung`",

        "`2021–2024 — Küchenhilfe — Restaurant Delphi, Hamburg — Speisenvorbereitung, Lager`\n"
        "`2019–2021 — Verkäufer — Bäckerei Ulm, Augsburg — Kasse, Kundenberatung`",

        "`2023–2025 — Pflegehelfer — Seniorenheim St. Josef, Köln — Grundpflege, Dokumentation`\n"
        "`2021–2023 — Reinigungskraft — Klinikum Kassel — Stationsreinigung`",

        "`2022–2025 — Monteur — Metallbau Berger, Stuttgart — Montage, Qualitätskontrolle`",
    ],
    PRAKTIKA: ["`03/2025, 4 Wochen Praktikum, Autohaus Behr, Stuttgart`",
               "`09/2024, 3 Wochen Praktikum im Kindergarten, Leipzig`",
               "`06/2025, 2 Wochen Praktikum im Rettungsdienst, BRK Würzburg`"],

    SPRACHEN: ["`Tadschikisch – Muttersprache, Russisch – C2, Deutsch – B2, Englisch – A2`",
               "`Usbekisch – Muttersprache, Russisch – C1, Deutsch – B1`",
               "`Kirgisisch – Muttersprache, Russisch – C2, Deutsch – A2, Türkisch – B1`"],
    FACHKENNTNISSE: ["`MS Office – gut, Gabelstaplerschein, Kassensysteme`",
                     "`Schweißen MAG/WIG, technische Zeichnungen lesen, Führerschein Klasse B`",
                     "`Pflegedokumentation, Blutdruckmessung, MS Office – Grundkenntnisse`",
                     "`Windows/Linux, Netzwerktechnik Grundlagen, HTML`"],

    INTERESSEN: ["`Fußball im Verein, Fahrradreparatur, Kochen`",
                 "`Schach, Freiwillige Feuerwehr, Fitness`",
                 "`Schwimmen, Fotografie, ehrenamtliche Übersetzungshilfe`"],
    MOTIVATION: ["`Хочу работать руками, разбираюсь в технике, чинил машины с отцом`",
                 "`Нравится помогать людям, ухаживал за бабушкой, хочу в медицину`",
                 "`Работал на складе, нравится порядок и система, хочу расти до логиста`",
                 "`Люблю общаться с людьми, был продавцом, хочу в гостиничное дело`"],
}


def question_text(state: int) -> str:
    """Собирает вопрос, подставляя случайный пример из пула."""
    tpl = TEMPLATES.get(state)
    if tpl is None:
        return ""
    pool = EXAMPLES.get(state)
    return tpl.format(ex=random.choice(pool)) if pool else tpl


FIELD_MAP = {
    NAME: "name", BIRTH_DATE: "birth_date", BIRTH_PLACE: "birth_place",
    ADDRESS: "address", PHONE: "phone", EMAIL: "email",
    BERUF: "beruf", UNTERNEHMEN: "unternehmen", START_DATE: "start_date",
    SCHULE: "schule", WEITERBILDUNG: "weiterbildung",
    ERFAHRUNG: "erfahrung", PRAKTIKA: "praktika",
    SPRACHEN: "sprachen", FACHKENNTNISSE: "fachkenntnisse",
    INTERESSEN: "interessen", MOTIVATION: "motivation",
}

SKIP_WORDS = ["пропустить", "нет", "skip", "-", "keine", "нету"]

# Подпись поля по его ключу — нужна, когда правится отправленное сообщение
LABEL_BY_KEY = {key: EDIT_LABELS.get(st, key) for st, key in FIELD_MAP.items()}


# ================= ДИАЛОГ =================



def purge_if_expired(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Удаляет профиль, если он пролежал дольше срока хранения."""
    profile = context.user_data.get("profile")
    if not profile:
        return False
    saved_at = profile.get("saved_at")
    if not saved_at:
        return False
    if time.time() - saved_at > DATA_RETENTION_DAYS * 86400:
        context.user_data.clear()
        logger.info("Профиль удалён по истечении срока хранения")
        return True
    return False


def days_left(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    profile = context.user_data.get("profile")
    if not profile or not profile.get("saved_at"):
        return None
    elapsed = (time.time() - profile["saved_at"]) / 86400
    return max(0, int(DATA_RETENTION_DAYS - elapsed))


def save_profile(context: ContextTypes.DEFAULT_TYPE):
    """Запоминает ответы кандидата, чтобы следующая заявка заняла полминуты."""
    d = context.user_data
    profile = {k: d.get(k, "") for k in PROFILE_FIELDS}
    if d.get("photo"):
        profile["photo"] = d["photo"]
    profile["saved_at"] = time.time()
    d["profile"] = profile


def load_profile(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Разворачивает сохранённый профиль обратно в рабочие поля."""
    profile = context.user_data.get("profile")
    if not profile:
        return False
    for k, v in profile.items():
        if k != "saved_at":
            context.user_data[k] = v
    return True


def reset_session(context: ContextTypes.DEFAULT_TYPE):
    """Чистит текущий диалог, но сохранённый профиль не трогает."""
    profile = context.user_data.get("profile")
    context.user_data.clear()
    if profile:
        context.user_data["profile"] = profile


async def ask(message, text: str):
    """Отправляет вопрос. Принимает message, чтобы работать и из кнопок тоже.

    Если Telegram не принял Markdown — шлём обычным текстом, иначе бот
    молча замолкает на середине диалога.
    """
    try:
        await message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Markdown отклонён, отправляю без разметки: {e}")
        plain = text.replace("*", "").replace("`", "").replace("_", "")
        await message.reply_text(plain)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Ничего не проглатываем молча: пишем в лог и предупреждаем пользователя."""
    logger.error("Ошибка в обработчике", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Что-то пошло не так. Напиши /start, чтобы начать заново."
            )
    except Exception:
        pass



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    purge_if_expired(context)
    reset_session(context)
    context.user_data["state"] = NAME
    await update.message.reply_text(
        "🇩🇪 *Ausbildung Agent*\n\n"
        "Составлю профессиональный немецкий Lebenslauf и Anschreiben.\n\n"
        "Будет 17 вопросов — часть можно пропустить. "
        "Чем подробнее ответишь, тем сильнее получатся документы.\n\n"
        "_Заполняешь один раз. Дальше заявка в любую другую фирму — "
        "команда_ */neu* _и полминуты._\n\n"
        f"🔒 _Данные хранятся {DATA_RETENTION_DAYS} дней, потом удаляются. "
        "Подробнее —_ */datenschutz*\n\n"
        "Поехали 👇",
        parse_mode="Markdown"
    )
    await ask(update.message, question_text(NAME))
    return NAME


async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get("state", NAME)
    answer = update.message.text.strip()

    key = FIELD_MAP.get(current)
    if key:
        context.user_data[key] = "" if answer.lower() in SKIP_WORDS else answer
        # Запоминаем связь сообщение → поле, чтобы правка текста в Telegram
        # меняла именно тот ответ, а не сбивала диалог
        context.user_data.setdefault("msg_map", {})[update.message.message_id] = key

    nxt = current + 1
    question = question_text(nxt)
    if not question:
        # Раньше здесь был молчаливый переход к сводке — из-за него бот
        # пропускал вопросы. Теперь честно сообщаем о сбое.
        logger.error(f"Нет текста вопроса для состояния {nxt}. Диалог прерван.")
        await update.message.reply_text(
            "⚠️ Внутренний сбой на этом шаге. Напиши /start, чтобы начать заново."
        )
        reset_session(context)
        return

    context.user_data["state"] = nxt
    await ask(update.message, question)
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
    return await show_summary(update.message, context)


def summary_text(d: dict) -> str:
    def val(k, default="—"):
        return d.get(k) or default

    return (
        "✅ *Проверь данные:*\n\n"
        f"👤 {val('name')}\n"
        f"📅 {val('birth_date')} · {val('birth_place')}\n"
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
        f"🎯 {val('interessen', 'нет')}\n"
        f"🔥 {val('motivation', 'нет')}\n"
        f"📸 {'фото есть' if d.get('photo') else 'без фото'}"
    )


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать PDF", callback_data="gen")],
        [InlineKeyboardButton("✏️ Исправить", callback_data="editmenu")],
        [InlineKeyboardButton("🔄 Заполнить заново", callback_data="restart")],
    ])


def edit_menu_keyboard() -> InlineKeyboardMarkup:
    """Поля в два столбца, чтобы влезли на экран телефона."""
    items = list(EDIT_LABELS.items())
    rows, i = [], 0
    while i < len(items):
        pair = items[i:i + 2]
        rows.append([InlineKeyboardButton(lbl, callback_data=f"edit:{st}")
                     for st, lbl in pair])
        i += 2
    rows.append([InlineKeyboardButton("⬅️ Назад к сводке", callback_data="back")])
    return InlineKeyboardMarkup(rows)


async def show_summary(message, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = CONFIRM
    await message.reply_text(
        summary_text(context.user_data),
        parse_mode="Markdown",
        reply_markup=summary_keyboard()
    )
    return CONFIRM


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки под сводкой."""
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "del_yes":
        context.user_data.clear()
        await query.edit_message_text(
            "🗑 Все данные удалены.\n\n/start — начать заново."
        )
        return

    if action == "del_no":
        await query.edit_message_text("Отменено, данные на месте.")
        return

    if action == "editmenu":
        await query.edit_message_text(
            "✏️ *Что исправить?*",
            parse_mode="Markdown",
            reply_markup=edit_menu_keyboard()
        )
        return

    if action == "back":
        await query.edit_message_text(
            summary_text(context.user_data),
            parse_mode="Markdown",
            reply_markup=summary_keyboard()
        )
        context.user_data["state"] = CONFIRM
        return

    if action == "restart":
        await query.edit_message_text("Начинаем заново.")
        reset_session(context)
        context.user_data["state"] = NAME
        await ask(query.message, question_text(NAME))
        return

    if action.startswith("edit:"):
        field_state = int(action.split(":")[1])
        context.user_data["state"] = EDITING
        context.user_data["editing_field"] = field_state

        label = EDIT_LABELS.get(field_state, "поле")
        current = context.user_data.get(FIELD_MAP.get(field_state, ""), "")
        note = f"\n\nСейчас: `{current}`" if current else ""

        await query.edit_message_text(
            f"✏️ *{label}*{note}\n\nНапиши новое значение:",
            parse_mode="Markdown"
        )
        return

    if action == "gen":
        await query.edit_message_text(summary_text(context.user_data),
                                      parse_mode="Markdown")
        await generate_documents(query.message, context)
        return


async def apply_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет исправленное значение и возвращает к сводке."""
    field_state = context.user_data.get("editing_field")

    if field_state == PHOTO:
        if update.message.photo:
            try:
                tg_file = await update.message.photo[-1].get_file()
                context.user_data["photo"] = bytes(await tg_file.download_as_bytearray())
            except Exception as e:
                logger.error(f"Ошибка фото: {e}")
                await update.message.reply_text("⚠️ Фото не загрузилось.")
        elif update.message.text and update.message.text.lower() in SKIP_WORDS:
            context.user_data.pop("photo", None)
    else:
        key = FIELD_MAP.get(field_state)
        if key and update.message.text:
            answer = update.message.text.strip()
            context.user_data[key] = "" if answer.lower() in SKIP_WORDS else answer
            context.user_data.setdefault("msg_map", {})[update.message.message_id] = key

    context.user_data.pop("editing_field", None)
    await update.message.reply_text("✅ Исправлено.")
    return await show_summary(update.message, context)


# ================= ГЕНЕРАЦИЯ =================

PROMPT_TEMPLATE = """Du bist ein erfahrener deutscher Bewerbungsberater und erstellst Unterlagen
für Bewerber aus Zentralasien, die eine Ausbildung oder Stelle in Deutschland suchen.

KANDIDATENDATEN (Rohangaben, teils auf Russisch):
- Name: {name}
- Geburtsdatum: {birth_date}
- Geburtsort: {birth_place}
- Adresse: {address}
- Telefon: {phone}
- E-Mail: {email}
- Angestrebter Beruf: {beruf}
- Wunschunternehmen: {unternehmen}
- Verfügbar ab: {start_date}
- Schulbildung: {schule}
- Kurse/Weiterbildung: {weiterbildung}
- Berufserfahrung: {erfahrung}
- Praktika: {praktika}
- Sprachen: {sprachen}
- Fachkenntnisse: {fachkenntnisse}
- Interessen: {interessen}
- Motivation (eigene Worte, ggf. Russisch): {motivation}

GRUNDREGELN:
1. Antworte AUSSCHLIESSLICH mit gültigem JSON. Kein Markdown, keine ```-Blöcke, kein Text davor oder danach.
2. Alles auf Deutsch. Russische Eingaben sinngemäß übertragen, nicht wörtlich übersetzen.
3. NIEMALS Platzhalter wie [Datum einfügen] oder [Name der Schule]. Fehlt eine Angabe, lasse den Eintrag weg.
4. Erfinde keine Arbeitgeber, Schulen, Zeiträume, Zertifikate oder Noten, die nicht in den Daten stehen.
5. Zeiträume im Format "2022 – 2024", "seit 2024", "03/2025".

BERUFSSPEZIFISCHE ANPASSUNG (wichtigster Punkt):
6. Der angestrebte Beruf kann aus JEDER Branche stammen — Handwerk, Pflege, Logistik, Gastronomie,
   Handel, Industrie, IT, Verwaltung, Bau, Landwirtschaft und so weiter.
   Analysiere zuerst, welche Anforderungen in Deutschland typisch für genau diesen Beruf sind,
   und richte das gesamte Dokument daran aus.
7. Verwende die branchenübliche deutsche Fachterminologie dieses Berufs.
   Beschreibe frühere Tätigkeiten so, dass ihr Bezug zum Zielberuf sichtbar wird.
   Beispiel: Lagerarbeit → "Kommissionierung", "Wareneingangskontrolle";
   Pflegehilfe → "Grundpflege", "Dokumentation"; Küche → "Mise en place", "HACCP".
8. Leite 4–6 persönliche Stärken SELBST ab. Sie müssen doppelt passen:
   zum Zielberuf UND belegbar durch die genannte Erfahrung des Kandidaten.
   Pflege: z.B. Empathie, Belastbarkeit, Verantwortungsbewusstsein.
   Logistik: z.B. Sorgfalt, körperliche Belastbarkeit, Termintreue.
   Handwerk: z.B. handwerkliches Geschick, Genauigkeit, Sicherheitsbewusstsein.
   Keine austauschbaren Listen, die auf jeden Beruf passen würden.
9. Führerscheine, Zertifikate und Scheine nur nennen, wenn sie in den Angaben vorkommen.

BERUFSERFAHRUNG:
10. Pro Station 2–3 konkrete Tätigkeits-Stichpunkte, jeweils mit einem Substantiv oder Verb beginnend
    (z.B. "Betreuung von bis zu 40 Gästen pro Schicht"). Keine Floskeln, keine Selbstlob-Sätze.

ANSCHREIBEN — der wichtigste Teil. Sie-Form, sachlich, präzise.

11. GESAMTLÄNGE: 280–360 Wörter, verteilt auf 4 Absätze.
    Ein zu kurzes Anschreiben wirkt lieblos und wird aussortiert.
    Richtwerte: Absatz 1 = 45–65 Wörter, Absatz 2 = 90–120 Wörter,
    Absatz 3 = 80–110 Wörter, Absatz 4 = 35–55 Wörter.

12. GRUNDPRINZIP: Beweis statt Behauptung.
    Jede Eigenschaft muss durch eine konkrete Tatsache aus den Kandidatendaten
    belegt werden. Niemals eine Eigenschaft nennen, ohne sie zu begründen.
    FALSCH: "Ich bin belastbar und teamfähig."
    RICHTIG: "Bei bis zu 40 Gästen pro Schicht habe ich gelernt, auch unter
    Zeitdruck den Überblick zu behalten und mich eng mit der Küche abzustimmen."

13. KONKRETHEIT: Verwende, wo vorhanden, echte Zahlen und Fakten aus den Daten —
    Dauer der Tätigkeit, Menge, Arbeitgeber, Sprachniveau, Abschluss.
    Nenne außerdem 2–3 typische Aufgaben des ANGESTREBTEN Berufs beim Namen
    und stelle den Bezug zur bisherigen Erfahrung her.
    Erfinde dabei keine Zahlen, die nicht ableitbar sind.

14. VERBOTEN sind diese Floskeln und alle ähnlichen Formulierungen:
    "Hiermit bewerbe ich mich", "Ich bin motiviert und lernbereit",
    "Ihr Unternehmen ist sehr bekannt", "Ich bin ein Teamplayer",
    "Ich bringe alle nötigen Voraussetzungen mit",
    "Ich würde mich sehr über eine Rückmeldung freuen" als einziger Schlusssatz.

15. AUFBAU DER ABSÄTZE:
    Absatz 1 — Einstieg: Warum genau dieser Beruf und dieser Betrieb.
      Beginne mit dem Interesse am Beruf oder mit einem Bezug zum Unternehmen,
      nicht mit der eigenen Person. Nenne den Beruf ausdrücklich.
    Absatz 2 — Erfahrung: Was der Kandidat bisher gemacht hat, mit Zahlen und
      Aufgaben, und was davon für den Zielberuf unmittelbar nützlich ist.
      Übertrage die Tätigkeiten in die Fachsprache des Zielberufs.
    Absatz 3 — Person und Sprache: Motivation in eigenen Worten des Kandidaten,
      Sprachkenntnisse mit Niveau, ein bis zwei belegte Stärken.
      Bei Bewerbern aus dem Ausland: Bereitschaft zum Umzug erwähnen,
      falls die Adresse nicht in Deutschland liegt.
    Absatz 4 — Abschluss: Verfügbarkeit ab dem genannten Datum, Hinweis auf
      beigefügte Unterlagen, aktive Bitte um ein persönliches Gespräch.

16. BETREFF: aussagekräftig und vollständig, ohne das Wort "Betreff".
    Muster: "Bewerbung um einen Ausbildungsplatz als [Beruf] ab [Datum]".

JSON-STRUKTUR (genau einhalten):
{{
  "personal": {{
    "name": "", "address": "", "phone": "", "email": "",
    "birth_date": "", "birth_place": ""
  }},
  "job_title": "Angestrebte Ausbildung als ...",
  "profile": "2-3 Sätze Kurzprofil, klar auf den Zielberuf ausgerichtet",
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
  "skills": [
    {{"label": "Fachliche Kenntnisse", "value": "..."}},
    {{"label": "Persönliche Stärken", "value": "vier bis sechs Stärken, durch Komma getrennt"}}
  ],
  "interests": [{{"label": "Sport", "value": ""}}],
  "letter": {{
    "recipient": "Firmenname\\nStraße\\nPLZ Ort",
    "city": "Wohnort des Kandidaten, gefolgt von ', {today}' — z.B. 'Leipzig, {today}'",
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


async def generate_documents(message, context: ContextTypes.DEFAULT_TYPE):
    """Собирает документы из текущих данных и сохраняет профиль на будущее.

    Принимает message, а не update: вызывается и из обычного сообщения,
    и из нажатия инлайн-кнопки.
    """
    await message.reply_text(
        "⏳ Генерирую документы... ~25 секунд.",
        reply_markup=ReplyKeyboardRemove()
    )

    d = context.user_data
    fields = {
        k: (d.get(k) or "nicht angegeben") for k in [
            "name", "birth_date", "birth_place", "address", "phone",
            "email", "beruf", "unternehmen", "start_date", "schule", "weiterbildung",
            "erfahrung", "praktika", "sprachen", "fachkenntnisse",
            "interessen", "motivation"]
    }
    # Дату ставим сами — модель иначе выдумывает произвольную
    fields["today"] = date.today().strftime("%d.%m.%Y")
    prompt = PROMPT_TEMPLATE.format(**fields)

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        data = parse_json_response(response.text)

        # Личные данные берём из ответов пользователя, а не из фантазии модели
        personal = data.setdefault("personal", {})
        for k in ("name", "address", "phone", "email",
                  "birth_date", "birth_place"):
            if d.get(k):
                personal[k] = d[k]

        pdf_buf = build_pdf(data, d.get("photo"))
        name_clean = (d.get("name") or "Kandidat").replace(" ", "_")
        firma = (d.get("unternehmen") or "").split(",")[0].strip().replace(" ", "_")
        filename = f"Bewerbung_{name_clean}"
        if firma:
            filename += f"_{firma}"

        # Профиль сохраняем только после успешной генерации
        save_profile(context)

        await message.reply_document(
            document=pdf_buf,
            filename=f"{filename}.pdf",
            caption=(
                f"✅ *Bewerbungsunterlagen für {d.get('name','')}*\n\n"
                f"📄 {d.get('beruf','')}"
                + (f" — {d.get('unternehmen')}" if d.get("unternehmen") else "")
                + "\n\nViel Erfolg! 🍀\n\n"
                "━━━━━━━━━━━━━━\n"
                "📌 Данные сохранены.\n"
                "*/neu* — заявка в другую фирму за 30 секунд\n"
                "*/profil* — посмотреть сохранённое\n"
                "*/start* — заполнить всё заново"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}", exc_info=True)
        await message.reply_text(
            f"❌ Ошибка: {str(e)}\n\nПопробуй снова: /start"
        )

    reset_session(context)


# ================= ПОВТОРНАЯ ЗАЯВКА =================

async def neu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Новая заявка на основе сохранённого профиля — без 17 вопросов."""
    if purge_if_expired(context):
        await update.message.reply_text(
            f"Данные хранились {DATA_RETENTION_DAYS} дней и были удалены "
            "автоматически.\n\nЗаполни анкету заново: /start"
        )
        return

    if not context.user_data.get("profile"):
        await update.message.reply_text(
            "У меня пока нет твоих данных.\n\n"
            "Заполни анкету один раз через /start — дальше каждая "
            "следующая заявка будет занимать полминуты."
        )
        return

    reset_session(context)
    load_profile(context)
    context.user_data["state"] = NEU_BERUF

    beruf = context.user_data.get("beruf", "")
    keyboard = [[f"✅ {beruf}"]] if beruf else None
    await update.message.reply_text(
        "🔁 *Новая заявка*\n\n"
        "Личные данные, опыт и образование я помню.\n"
        "Нужно только уточнить, куда подаёшься.\n\n"
        "💼 *Профессия*\n\n"
        + (f"Прошлый раз было: `{beruf}`\nНажми кнопку, чтобы оставить, или напиши другую."
           if beruf else "Напиши название профессии по-немецки."),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True,
                                         one_time_keyboard=True) if keyboard else None
    )


async def neu_beruf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip().lstrip("✅").strip()
    if answer:
        context.user_data["beruf"] = answer

    context.user_data["state"] = NEU_UNTERNEHMEN
    await update.message.reply_text(
        "🏢 *Куда подаёшься?*\n\n"
        "Название фирмы и город.\n"
        f"Например: {random.choice(EXAMPLES[UNTERNEHMEN])}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )


async def neu_unternehmen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip()
    context.user_data["unternehmen"] = "" if answer.lower() in SKIP_WORDS else answer

    context.user_data["state"] = NEU_START
    alt = context.user_data.get("start_date") or "nach Absprache"
    await update.message.reply_text(
        "🗓 *Когда можешь начать?*\n\n"
        f"Прошлый раз: `{alt}`\n"
        "Нажми кнопку, чтобы оставить, или напиши другую дату.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[f"✅ {alt}"]], resize_keyboard=True,
                                         one_time_keyboard=True)
    )


async def neu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip().lstrip("✅").strip()
    if answer and answer.lower() not in SKIP_WORDS:
        context.user_data["start_date"] = answer

    await generate_documents(update.message, context)


async def profil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает, что бот помнит о кандидате."""
    profile = context.user_data.get("profile")
    if not profile:
        await update.message.reply_text(
            "Пока ничего не сохранено. Пройди анкету через /start."
        )
        return

    def v(k, default="—"):
        return profile.get(k) or default

    await update.message.reply_text(
        "📇 *Сохранённые данные*\n\n"
        f"👤 {v('name')}\n"
        f"📅 {v('birth_date')} · {v('birth_place')}\n"
        f"🏠 {v('address')}\n"
        f"📞 {v('phone')} · 📧 {v('email')}\n\n"
        f"🎓 {v('schule')}\n"
        f"💪 {v('erfahrung', 'нет')}\n"
        f"🌍 {v('sprachen')}\n"
        f"📸 {'фото есть' if profile.get('photo') else 'без фото'}\n\n"
        f"🔒 Хранятся ещё {days_left(context)} дн.\n\n"
        "━━━━━━━━━━━━━━\n"
        "*/neu* — заявка в новую фирму\n"
        "*/start* — перезаполнить анкету\n"
        "*/loeschen* — удалить данные",
        parse_mode="Markdown"
    )



async def datenschutz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация об обработке персональных данных (DSGVO)."""
    left = days_left(context)
    status = (f"\n\n📌 Твои данные сохранены, будут удалены через *{left} дн.*"
              if left is not None else "")

    await update.message.reply_text(
        "🔒 *Обработка персональных данных*\n\n"
        "*Что собирается:* имя, дата и место рождения, адрес, телефон, "
        "e-mail, сведения об образовании и опыте, фото — то, что ты вводишь сам.\n\n"
        "*Зачем:* только для составления твоего Lebenslauf и Anschreiben.\n\n"
        "*Кому передаётся:* текст анкеты уходит в Google Gemini для генерации "
        "документов. Фото никуда не передаётся — оно вставляется в PDF локально.\n\n"
        f"*Сколько хранится:* {DATA_RETENTION_DAYS} дней с последней заявки, "
        "затем удаляется автоматически.\n\n"
        "*Твои права:* можешь в любой момент посмотреть данные (*/profil*) "
        "или удалить их полностью (*/loeschen*). Удаление необратимо и "
        "происходит сразу."
        + status,
        parse_mode="Markdown"
    )


async def loeschen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление данных — с подтверждением, потому что откатить нельзя."""
    if not context.user_data:
        await update.message.reply_text("Нечего удалять — сохранённых данных нет.")
        return

    await update.message.reply_text(
        "🗑 *Удалить все данные?*\n\n"
        "Будут стёрты анкета, фото и сохранённый профиль. "
        "Восстановить не получится.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, удалить", callback_data="del_yes"),
            InlineKeyboardButton("Отмена", callback_data="del_no"),
        ]])
    )


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

        # Anschreiben обязан уместиться на одну страницу — это норма деловой
        # переписки в Германии. Чем длиннее текст, тем плотнее интервалы.
        words = sum(len(str(x).split()) for x in letter.get("paragraphs") or [])
        if words > 320:
            gap_top, gap_recip, gap_city, gap_sign = 14, 12, 12, 20
            body = ParagraphStyle("letter_tight", parent=s["letter"],
                                  leading=13.2, spaceAfter=6)
        elif words > 250:
            gap_top, gap_recip, gap_city, gap_sign = 18, 15, 14, 24
            body = ParagraphStyle("letter_mid", parent=s["letter"],
                                  leading=14, spaceAfter=7)
        else:
            gap_top, gap_recip, gap_city, gap_sign = 26, 20, 18, 30
            body = s["letter"]

        story.append(Paragraph(f"<b>{p.get('name','')}</b>", s["letterline"]))
        for bit in (p.get("address"), p.get("phone"), p.get("email")):
            if bit:
                story.append(Paragraph(bit, s["letterline"]))
        story.append(Spacer(1, gap_top))

        for line in str(letter.get("recipient", "")).split("\n"):
            if line.strip():
                story.append(Paragraph(line.strip(), s["letterline"]))
        story.append(Spacer(1, gap_recip))

        if letter.get("city"):
            story.append(Paragraph(letter["city"], s["right"]))
        story.append(Spacer(1, gap_city))

        if letter.get("subject"):
            story.append(Paragraph(letter["subject"], s["subject"]))
        if letter.get("salutation"):
            story.append(Paragraph(letter["salutation"], body))
        for para in letter.get("paragraphs") or []:
            story.append(Paragraph(para, body))

        story.append(Spacer(1, 6))
        story.append(Paragraph(letter.get("closing", "Mit freundlichen Grüßen"),
                               s["letterline"]))
        story.append(Spacer(1, gap_sign))
        story.append(Paragraph(p.get("name", ""), s["letterline"]))

    doc.build(story)
    buf.seek(0)
    return buf


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_session(context)
    await update.message.reply_text("Отменено. /start — начать заново.",
                                    reply_markup=ReplyKeyboardRemove())



async def on_edited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь исправил уже отправленное сообщение.

    Telegram присылает это отдельным событием, где update.message пустой.
    Раньше бот на этом падал и предлагал начать заново. Теперь находим,
    на какой вопрос отвечало это сообщение, и обновляем именно то поле.
    """
    msg = update.edited_message
    if not msg or not msg.text:
        return

    key = (context.user_data.get("msg_map") or {}).get(msg.message_id)

    if not key:
        # Сообщение не из текущей анкеты — например, правка очень старого текста
        await msg.reply_text(
            "Это сообщение не относится к текущей анкете.\n\n"
            "Чтобы что-то исправить, дойди до сводки и нажми *«✏️ Исправить»*.",
            parse_mode="Markdown"
        )
        return

    answer = msg.text.strip()
    context.user_data[key] = "" if answer.lower() in SKIP_WORDS else answer
    label = LABEL_BY_KEY.get(key, key)

    await msg.reply_text(
        f"✅ Обновил *{label}*: `{answer}`",
        parse_mode="Markdown"
    )

    # Если человек уже на сводке — сразу показываем её обновлённой
    if context.user_data.get("state") == CONFIRM:
        await show_summary(msg, context)


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единая точка входа. Состояние живёт только в user_data — рассинхрону взяться неоткуда."""
    if not update.message:
        return

    state = context.user_data.get("state")

    if state is None:
        if context.user_data.get("profile"):
            await update.message.reply_text(
                "Диалог не активен.\n\n"
                "*/neu* — быстрая заявка по сохранённым данным\n"
                "*/profil* — что я помню\n"
                "*/start* — заполнить анкету заново",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "Диалог не начат.\n\nНапиши /start, чтобы создать документы."
            )
        return

    if state == EDITING:
        return await apply_edit(update, context)

    # Быстрая заявка по сохранённому профилю
    if state == NEU_BERUF:
        return await neu_beruf(update, context)
    if state == NEU_UNTERNEHMEN:
        return await neu_unternehmen(update, context)
    if state == NEU_START:
        return await neu_start(update, context)

    if state == PHOTO:
        return await collect_photo(update, context)
    if state == CONFIRM:
        await update.message.reply_text(
            "Воспользуйся кнопками под сводкой выше 👆",
            reply_markup=summary_keyboard()
        )
        return

    if not update.message.text:
        await ask(update.message, question_text(state))
        return
    return await collect(update, context)


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
    # Самопроверка на старте: лучше увидеть проблему в логах Render,
    # чем ловить её посреди диалога с живым кандидатом.
    missing = [st for st in range(NAME, PHOTO + 1) if not TEMPLATES.get(st)]
    if missing:
        logger.critical(f"НЕТ ТЕКСТА ВОПРОСА для состояний: {missing}. Диалог будет рваться!")
    else:
        logger.info(f"Самопроверка пройдена: {len(TEMPLATES)} вопросов на месте")

    await run_web_server()

    # Состояние переживает перезапуск процесса — Render на бесплатном плане
    # регулярно поднимает контейнер заново, и без этого диалог обрывался.
    persistence = PicklePersistence(filepath="bot_state.pickle")

    app = (Application.builder()
           .token(TELEGRAM_TOKEN)
           .persistence(persistence)
           .build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neu", neu))
    app.add_handler(CommandHandler("profil", profil))
    app.add_handler(CommandHandler("datenschutz", datenschutz))
    app.add_handler(CommandHandler("loeschen", loeschen))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(on_button))

    # Правка отправленного сообщения — отдельное событие, ловим до основного роутера
    app.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.TEXT, on_edited))

    app.add_handler(MessageHandler(
        filters.UpdateType.MESSAGE & (filters.TEXT | filters.PHOTO)
        & ~filters.COMMAND, router))
    app.add_error_handler(on_error)

    await app.initialize()
    await app.bot.set_my_commands([
        ("start", "Заполнить анкету с нуля"),
        ("neu", "Заявка в новую фирму (30 секунд)"),
        ("profil", "Мои сохранённые данные"),
        ("datenschutz", "Как обрабатываются данные"),
        ("loeschen", "Удалить мои данные"),
        ("cancel", "Прервать диалог"),
    ])
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Бот запущен и слушает сообщения")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
