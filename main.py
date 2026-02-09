import os, io, asyncio, logging, re, random
import pandas as pd
import chardet
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pdfminer.high_level import extract_text

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ===== إعدادات =====
TOKEN = os.environ["TOKEN"]          # حطه في Variables في Railway
DELAY_BETWEEN = 2                   # ثواني بين كل سؤال

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
    except Exception:
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
    text = (text or "").replace("\r", "")

    for m in QUESTION_BLOCK.finditer(text):
        qid = m.group(1)
        q, A, B, C, D = [m.group(i).strip() for i in range(2, 7)]
        corr = (m.group(7) or "").strip().upper()

        if not q:
            errors.append(f"❌ Question {qid}: نص السؤال ناقص")
            continue
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
        errors.append("❌ لم يتم العثور على أسئلة بصيغة صحيحة.\n"
                      "الصيغة:\nQ1) ...\nA) ...\nB) ...\nC) ...\nD) ...\nCorrect: A")

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

        q = str(r["question"]).strip() if pd.notna(r["question"]) else ""
        A = str(r["A"]).strip() if pd.notna(r["A"]) else ""
        B = str(r["B"]).strip() if pd.notna(r["B"]) else ""
        C = str(r["C"]).strip() if pd.notna(r["C"]) else ""
        D = str(r["D"]).strip() if pd.notna(r["D"]) else ""
        corr = str(r["correct"]).strip().upper() if pd.notna(r["correct"]) else ""

        if not q: errors.append(f"❌ Row {row}: question فارغة")
        if not A: errors.append(f"❌ Row {row}: الخيار A فارغ")
        if not B: errors.append(f"❌ Row {row}: الخيار B فارغ")
        if not C: errors.append(f"❌ Row {row}: الخيار C فارغ")
        if not D: errors.append(f"❌ Row {row}: الخيار D فارغ")
        if corr not in mapping:
            errors.append(f"❌ Row {row}: correct لازم A/B/C/D (القيمة الحالية: '{corr or 'فارغ'}')")

        # لا نضيف السؤال إذا فيه مشاكل في نفس الصف
        if errors:
            continue

        items.append({
            "id": int(r["id"]),
            "q": q,
            "opts": [A, B, C, D],
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
        "✅ البوت ينشر Quiz مع خلط الخيارات عشوائيًا.\n"
        "⚠️ لو في خطأ بالملف ما راح ينشر شيء."
    )

async def private_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    doc = update.message.document
    if not doc:
        return

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
        await update.message.reply_text("استخدم الأمر في الخاص.")
        return

    if not context.args:
        await update.message.reply_text("الاستخدام: /loadmine <CHAT_ID>")
        return

    try:
        chat_id = int(context.args[0])
    except:
        await update.message.reply_text("CHAT_ID غير صحيح.")
        return

    file_entry = LAST_FILE_BY_USER.get(update.effective_user.id)
    if not file_entry:
        await update.message.reply_text("أرسل ملف أولاً.")
        return

    try:
        if file_entry["kind"] == "csv":
            items = parse_csv_strict(file_entry["data"])
        else:
            items = parse_pdf_strict(file_entry["data"])
    except Exception as e:
        await update.message.reply_text(str(e))
        return

    PACK_BY_CHAT[chat_id] = {"items": items}
    await update.message.reply_text(f"تم تحميل {len(items)} سؤال.")

async def postall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("استخدم الأمر في الخاص.")
        return

    if not context.args:
        await update.message.reply_text("الاستخدام: /postall <CHAT_ID>")
        return

    try:
        chat_id = int(context.args[0])
    except:
        await update.message.reply_text("CHAT_ID غير صحيح.")
        return

    pack = PACK_BY_CHAT.get(chat_id)
    if not pack:
        await update.message.reply_text("ما في أسئلة محمّلة. استخدم /loadmine أولاً.")
        return

    items = pack["items"]
    await update.message.reply_text(f"جاري نشر {len(items)} سؤالاً كـ Quiz ...")

    for i, item in enumerate(items, 1):
        q_text = f"Q{i}) {item['q']}"

        # ===== خلط الخيارات مع الحفاظ على الإجابة الصحيحة =====
        paired = list(enumerate(item["opts"]))  # [(0,opt0),(1,opt1),(2,opt2),(3,opt3)]
        random.shuffle(paired)

        new_opts = [text for old_i, text in paired]  # نص فقط بدون A/B/C/D
        new_correct_idx = [old_i for old_i, _ in paired].index(item["correct_idx"])

        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=q_text,
                options=new_opts,
                type="quiz",
                correct_option_id=new_correct_idx,
                is_anonymous=True
            )
        except Exception as e:
            await context.bot.send_message(chat_id, f"تعذر إرسال السؤال رقم {i}. السبب: {e}")

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
