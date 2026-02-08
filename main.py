# 1
import os, io, asyncio, logging, re
import pandas as pd
import chardet
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pdfminer.high_level import extract_text

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# التوكن من متغيرات البيئة (Railway)
TOKEN = os.environ["TOKEN"]
DELAY_BETWEEN = 2  # ثواني بين كل سؤال وسؤال

logging.basicConfig(level=logging.INFO)

# تخزين مؤقت داخل التشغيل
LAST_FILE_BY_USER = {}   # { user_id: {"kind": "csv"/"pdf", "data": bytes} }
PACK_BY_CHAT    = {}     # { chat_id: {"items": [...]} }

# ريجيكس لاستخراج الأسئلة من ملف نصّي/بي دي اف نصّي
QUESTION_BLOCK = re.compile(
    r"Q\s*(\d+)\)\s*(.*?)\n\s*A\)\s*(.*?)\n\s*B\)\s*(.*?)\n\s*C\)\s*(.*?)\n\s*D\)\s*(.*?)"
    r"(?:\n\s*Correct:\s*([ABCD]))?(?:\n|$)",
    re.IGNORECASE | re.DOTALL
)

# ---------- أدوات مساعدة ----------
def _collect_errors_raise(errors):
    if errors:
        # نعرض أول 10 أخطاء فقط لو كثيرة
        preview = "\n".join(errors[:10])
        more = "" if len(errors) <= 10 else f"\n... (+{len(errors)-10} أخطاء أخرى)"
        raise ValueError(preview + more)

# ---------- تحويل نص بصيغة Q/A إلى عناصر ----------
def _items_from_text_strict(raw_text: str):
    # يحوّل نص بصيغة:
    # Q1) ...
    # A) ...
    # B) ...
    # C) ...
    # D) ...
    # Correct: A
    text = (raw_text or "").replace("\r", "")
    items = []
    errors = []

    for m in QUESTION_BLOCK.finditer(text):
        qid = m.group(1)
        q   = (m.group(2) or "").strip()
        A   = (m.group(3) or "").strip()
        B   = (m.group(4) or "").strip()
        C   = (m.group(5) or "").strip()
        D   = (m.group(6) or "").strip()
        corr= (m.group(7) or "").strip().upper()

        # تحقق صارم
        line_hint = f"Question {qid}"
        if not q: errors.append(f"❌ {line_hint}: النص مفقود")
        if not A: errors.append(f"❌ {line_hint}: الخيار A مفقود")
        if not B: errors.append(f"❌ {line_hint}: الخيار B مفقود")
        if not C: errors.append(f"❌ {line_hint}: الخيار C مفقود")
        if not D: errors.append(f"❌ {line_hint}: الخيار D مفقود")
        if corr not in {"A", "B", "C", "D"}:
            errors.append(f"❌ {line_hint}: 'Correct:' لازم يكون أحد A/B/C/D (القيمة الحالية: '{corr or 'فارغ'}')")

        if errors:
            # لا نبني العنصر إن كان فيه أخطاء؛ سنرمي لاحقًا بعد المسح الكامل
            continue

        item = {
            "id": int(qid),
            "q": q,
            "opts": [A, B, C, D],
            "correct_idx": {"A":0, "B":1, "C":2, "D":3}[corr],
        }
        items.append(item)

    if not items and not errors:
        errors.append("❌ لم أتعرف على أي سؤال. تأكد من الصيغة Q1)/A)/B)/C)/D)/Correct:")

    _collect_errors_raise(errors)
    return items

# ---------- قرّاء الملفات ----------
def parse_pdf_strict(file_bytes: bytes):
    # يقرأ PDF نصّي: يحاول pypdf ثم pdfminer
    text = ""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        for p in reader.pages:
            text += "\n" + (p.extract_text() or "")
    except PdfReadError:
        text = ""
    except Exception:
        text = ""

    if not text.strip():
        try:
            text = extract_text(io.BytesIO(file_bytes)) or ""
        except Exception as e:
            raise ValueError(f"تعذر قراءة PDF: {e}. أعد التصدير كـ PDF نصّي أو استخدم CSV.")

    if len(text.strip()) < 20:
        raise ValueError("يبدو أن الـ PDF صور فقط (بدون نص). استخدم CSV/TXT أو صدّر PDF كنصي.")

    return _items_from_text_strict(text)

def parse_csv_strict(file_bytes: bytes):
    # CSV بالأعمدة: id,question,A,B,C,D,correct (لازم correct موجود وصحيح)
    enc = chardet.detect(file_bytes).get("encoding") or "utf-8"
    df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)

    needed = ["id","question","A","B","C","D","correct"]
    errors = []
    for col in needed:
        if col not in df.columns:
            errors.append(f"❌ Missing column: {col}")
    _collect_errors_raise(errors)

    mapping = {"A":0,"B":1,"C":2,"D":3}
    items = []

Usef, [09/02/2026 02:19]
for idx, r in df.iterrows():
        row_num = idx + 1  # للعرض
        # تحقق من الحقول النصية
        q = str(r["question"]).strip() if pd.notna(r["question"]) else ""
        A = str(r["A"]).strip() if pd.notna(r["A"]) else ""
        B = str(r["B"]).strip() if pd.notna(r["B"]) else ""
        C = str(r["C"]).strip() if pd.notna(r["C"]) else ""
        D = str(r["D"]).strip() if pd.notna(r["D"]) else ""
        corr_raw = str(r["correct"]).strip().upper() if pd.notna(r["correct"]) else ""

        if not q: errors.append(f"❌ Row {row_num}: question فارغة")
        if not A: errors.append(f"❌ Row {row_num}: الخيار A فارغ")
        if not B: errors.append(f"❌ Row {row_num}: الخيار B فارغ")
        if not C: errors.append(f"❌ Row {row_num}: الخيار C فارغ")
        if not D: errors.append(f"❌ Row {row_num}: الخيار D فارغ")
        if corr_raw not in mapping:
            errors.append(f"❌ Row {row_num}: correct يجب أن يكون A/B/C/D (القيمة الحالية: '{corr_raw or 'فارغ'}')")

        if errors:
            continue

        item = {
            "id": r["id"],
            "q": q,
            "opts": [A, B, C, D],
            "correct_idx": mapping[corr_raw],
        }
        items.append(item)

    if not items and not errors:
        errors.append("❌ الملف لا يحتوي أسئلة صالحة.")

    _collect_errors_raise(errors)
    return items

# ---------- Handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل لي CSV أو PDF (نصي) في الخاص.\n"
        "بعدها استخدم:\n"
        "/loadmine <CHAT_ID>\n"
        "/postall <CHAT_ID>\n"
        "سيتم النشر كـ Quiz مخفي فقط، ولو في خطأ راح أوضح موقعه وما أنشر شيء."
    )

async def private_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    d = update.message.document
    if not d:
        return
    f = await context.bot.get_file(d.file_id)
    data = await f.download_as_bytearray()
    name = (d.file_name or "").lower()

    if name.endswith(".csv"):
        kind = "csv"
    elif name.endswith(".pdf"):
        kind = "pdf"
    else:
        await update.message.reply_text(
            "الملفات المدعومة: CSV أو PDF نصي.\n"
            "CSV الأعمدة المطلوبة: id,question,A,B,C,D,correct\n"
            "أو PDF بالنمط:\n"
            "Q1) ...\nA) ...\nB) ...\nC) ...\nD) ...\nCorrect: A"
        )
        return

    LAST_FILE_BY_USER[update.effective_user.id] = {"kind": kind, "data": bytes(data)}
    await update.message.reply_text(f"{kind.upper()} تم حفظه. الآن استخدم /loadmine <CHAT_ID>.")

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

    uid = update.effective_user.id
    file_entry = LAST_FILE_BY_USER.get(uid)
    if not file_entry:
        await update.message.reply_text("ما عندي ملف محفوظ لك. أرسل CSV/PDF أولاً.")
        return

    try:
        if file_entry["kind"] == "csv":
            items = parse_csv_strict(file_entry["data"])
        else:
            items = parse_pdf_strict(file_entry["data"])
    except Exception as e:
        await update.message.reply_text(f"{e}")
        return

    PACK_BY_CHAT[chat_id] = {"items": items}
    await update.message.reply_text(f"تم تحميل {len(items)} سؤالاً بنجاح للقروب/القناة: {chat_id}")

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

Usef, [09/02/2026 02:19]
await update.message.reply_text("CHAT_ID غير صحيح.")
        return

    pack = PACK_BY_CHAT.get(chat_id)
    if not pack:
        await update.message.reply_text("ما في أسئلة محمّلة لهذا الـ CHAT_ID. استخدم /loadmine أولاً.")
        return

    items = pack["items"]
    await update.message.reply_text(f"جاري نشر {len(items)} سؤالاً كـ Quiz مخفي في {chat_id} ...")

    for i, item in enumerate(items, start=1):
        q = f"Q{i}) {item['q']}"
        opts = [f"A) {item['opts'][0]}",
                f"B) {item['opts'][1]}",
                f"C) {item['opts'][2]}",
                f"D) {item['opts'][3]}"]

        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=q,
                options=opts,
                type="quiz",
                correct_option_id=item["correct_idx"],  # لن نصل هنا إلا وهو موجود
                is_anonymous=True
            )
        except Exception as e:
            await context.bot.send_message(chat_id, f"تعذر إرسال هذا السؤال:\n{q}\nسبب الخطأ: {e}")

        await asyncio.sleep(DELAY_BETWEEN)

# ---------- Bootstrapping ----------
async def _post_init(app: Application):
    # إلغاء أي Webhook سابق ثم بدء polling
    await app.bot.delete_webhook(drop_pending_updates=True)

def main():
    app = Application.builder().token(TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, private_doc))
    app.add_handler(CommandHandler("loadmine", loadmine_cmd))
    app.add_handler(CommandHandler("postall", postall_cmd))
    app.run_polling()

if name == "__main__":
    main()
