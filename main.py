import os, io, asyncio, logging, re
import pandas as pd
import chardet
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pdfminer.high_level import extract_text

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ===== إعدادات =====
TOKEN = os.environ["TOKEN"]
DELAY_BETWEEN = 2  # ثواني بين كل سؤال

logging.basicConfig(level=logging.INFO)

LAST_FILE_BY_USER = {}   # { user_id: {"kind": "csv"/"pdf", "data": bytes} }
PACK_BY_CHAT = {}        # { chat_id: {"items": [...] } }

QUESTION_BLOCK = re.compile(
    r"Q\s*(\d+)\)\s*(.*?)\n\s*A\)\s*(.*?)\n\s*B\)\s*(.*?)\n\s*C\)\s*(.*?)\n\s*D\)\s*(.*?)"
    r"(?:\n\s*Correct:\s*([ABCD]))?(?:\n|$)",
    re.IGNORECASE | re.DOTALL
)

# ===== أدوات =====
def _raise_if_errors(errors):
    if errors:
        preview = "\n".join(errors[:10])
        extra = "" if len(errors) <= 10 else f"\n... (+{len(errors)-10} أخطاء أخرى)"
        raise ValueError(preview + extra)

# ===== PDF =====
def parse_pdf_strict(file_bytes: bytes):
    text = ""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        for p in reader.pages:
            text += "\n" + (p.extract_text() or "")
    except PdfReadError:
        pass

    if not text.strip():
        try:
            text = extract_text(io.BytesIO(file_bytes)) or ""
        except Exception as e:
            raise ValueError(f"تعذر قراءة PDF: {e}")

    if len(text.strip()) < 20:
        raise ValueError("PDF يبدو صور فقط. استخدم CSV أو PDF نصي.")

    return parse_text_questions(text)

# ===== TEXT =====
def parse_text_questions(text: str):
    items, errors = [], []
    text = text.replace("\r", "")

    for m in QUESTION_BLOCK.finditer(text):
        qid = m.group(1)
        q, A, B, C, D = [m.group(i).strip() for i in range(2, 7)]
        corr = (m.group(7) or "").strip().upper()

        if corr not in {"A","B","C","D"}:
            errors.append(f"❌ Question {qid}: Correct لازم يكون A/B/C/D")
            continue

        items.append({
            "id": int(qid),
            "q": q,
            "opts": [A, B, C, D],
            "correct_idx": {"A":0,"B":1,"C":2,"D":3}[corr]
        })

    if not items:
        errors.append("❌ لم يتم العثور على أسئلة بصيغة صحيحة.")

    _raise_if_errors(errors)
    return items

# ===== CSV =====
def parse_csv_strict(file_bytes: bytes):
    enc = chardet.detect(file_bytes).get("encoding") or "utf-8"
    df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)

    required = ["id","question","A","B","C","D","correct"]
    errors = []

    for col in required:
        if col not in df.columns:
            errors.append(f"❌ Missing column: {col}")

    _raise_if_errors(errors)

    items = []
    mapping = {"A":0,"B":1,"C":2,"D":3}

    for idx, r in df.iterrows():
        row = idx + 1
        corr = str(r["correct"]).strip().upper()

        if corr not in mapping:
            errors.append(f"❌ Row {row}: correct لازم A/B/C/D")
            continue

        items.append({
            "id": int(r["id"]),
            "q": str(r["question"]).strip(),
            "opts": [
                str(r["A"]).strip(),
                str(r["B"]).strip(),
                str(r["C"]).strip(),
                str(r["D"]).strip()
            ],
            "correct_idx": mapping[corr]
        })

    _raise_if_errors(errors)
    return items

# ===== Handlers =====
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل CSV أو PDF نصي في الخاص.\n"
        "/loadmine <CHAT_ID>\n"
        "/postall <CHAT_ID>\n"
        "⚠️ لو في خطأ بالملف ما راح ينشر شيء."
    )

async def private_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    data = await file.download_as_bytearray()
    name = (doc.file_name or "").lower()

    if name.endswith(".csv"):
        kind = "csv"
    elif name.endswith(".pdf"):
        kind = "pdf"
    else:
        await update.message.reply_text("الملفات المدعومة: CSV أو PDF نصي فقط.")
        return

    LAST_FILE_BY_USER[update.effective_user.id] = {"kind": kind, "data": bytes(data)}
    await update.message.reply_text("تم حفظ الملف. استخدم /loadmine <CHAT_ID>")

async def loadmine_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    chat_id = int(context.args[0])
    file_entry = LAST_FILE_BY_USER.get(update.effective_user.id)

    if not file_entry:
        await update.message.reply_text("أرسل ملف أولاً.")
        return

    try:
        items = parse_csv_strict(file_entry["data"]) if file_entry["kind"] == "csv" else parse_pdf_strict(file_entry["data"])
    except Exception as e:
        await update.message.reply_text(str(e))
        return

    PACK_BY_CHAT[chat_id] = {"items": items}
    await update.message.reply_text(f"تم تحميل {len(items)} سؤال.")

async def postall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = int(context.args[0])
    pack = PACK_BY_CHAT.get(chat_id)

    if not pack:
        await update.message.reply_text("ما في أسئلة محمّلة.")
        return

    for i, item in enumerate(pack["items"], 1):
        await context.bot.send_poll(
            chat_id=chat_id,
            question=f"Q{i}) {item['q']}",
            options=[f"A) {o}" for o in item["opts"]],
            type="quiz",
            correct_option_id=item["correct_idx"],
            is_anonymous=True
        )
        await asyncio.sleep(DELAY_BETWEEN)

# ===== Boot =====
async def _post_init(app: Application):
    await app.bot.delete_webhook(drop_pending_updates=True)

def main():
    app = Application.builder().token(TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, private_doc))
    app.add_handler(CommandHandler("loadmine", loadmine_cmd))
    app.add_handler(CommandHandler("postall", postall_cmd))
    app.run_polling()

if __name__ == "__main__":
    main()
