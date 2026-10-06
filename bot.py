import asyncio
import io
import os
import sqlite3
from datetime import datetime

import qrcode
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8269119870"))
CARD_NUMBER = os.getenv("CARD_NUMBER", "شماره کارت را در Secrets وارد کنید")
CARD_NAME = os.getenv("CARD_NAME", "نام صاحب کارت را در Secrets وارد کنید")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "doop_6")
LOW_STOCK = int(os.getenv("LOW_STOCK", "3"))
DB = "rez_bot.db"

PLANS = {
    10: 95, 20: 130, 30: 155, 40: 175,
    50: 190, 100: 330, 200: 550, 500: 1350,
}


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, volume INTEGER NOT NULL, config TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'available', sold_to INTEGER, sold_at TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, volume INTEGER NOT NULL,
        price INTEGER NOT NULL, service_name TEXT NOT NULL, discount_code TEXT,
        discount_amount INTEGER DEFAULT 0, receipt_file_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
        config_id INTEGER, created_at TEXT, decided_at TEXT
    );
    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, order_id INTEGER,
        name TEXT NOT NULL, volume INTEGER NOT NULL, config TEXT NOT NULL, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS discounts (
        code TEXT PRIMARY KEY, kind TEXT NOT NULL, value INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    """)
    con.commit(); con.close()


def save_user(message: Message):
    u = message.from_user
    con = db()
    con.execute("INSERT OR REPLACE INTO users(user_id,username,first_name,created_at) VALUES(?,?,?,COALESCE((SELECT created_at FROM users WHERE user_id=?),?))",
                (u.id, u.username, u.first_name, u.id, datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()


def main_kb(user_id: int):
    rows = [
        [InlineKeyboardButton(text="🛒 خرید سرویس", callback_data="buy")],
        [InlineKeyboardButton(text="📦 سرویس‌های من", callback_data="services"), InlineKeyboardButton(text="💳 پیگیری پرداخت", callback_data="orders")],
        [InlineKeyboardButton(text="🎁 کد تخفیف", callback_data="discount"), InlineKeyboardButton(text="🎧 پشتیبانی", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
    ]
    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="⚙️ پنل مدیریت", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_kb():
    buttons=[]
    for v,p in PLANS.items():
        buttons.append(InlineKeyboardButton(text=f"{v}GB — {p} تومان", callback_data=f"plan:{v}"))
    rows=[buttons[i:i+2] for i in range(0,len(buttons),2)]
    rows.append([InlineKeyboardButton(text="🔙 برگشت", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن کانفیگ", callback_data="a_add"), InlineKeyboardButton(text="📦 موجودی", callback_data="a_stock")],
        [InlineKeyboardButton(text="🛒 سفارش‌ها", callback_data="a_orders"), InlineKeyboardButton(text="👥 کاربران", callback_data="a_users")],
        [InlineKeyboardButton(text="📊 آمار فروش", callback_data="a_stats"), InlineKeyboardButton(text="🎁 تخفیف‌ها", callback_data="a_discounts")],
        [InlineKeyboardButton(text="📢 پیام همگانی", callback_data="a_broadcast")],
        [InlineKeyboardButton(text="🔙 منوی اصلی", callback_data="home")],
    ])


def available_count(volume):
    con=db(); n=con.execute("SELECT COUNT(*) FROM configs WHERE volume=? AND status='available'",(volume,)).fetchone()[0]; con.close(); return n


def fmt_price(n): return f"{n:,}"

class BuyState(StatesGroup):
    service_name=State(); discount=State(); receipt=State()
class AddConfigState(StatesGroup):
    volume=State(); configs=State()
class DiscountState(StatesGroup):
    code=State(); kind=State(); value=State()
class BroadcastState(StatesGroup):
    text=State()

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(Command("start"))
async def start(message: Message):
    save_user(message)
    await message.answer("<b>فروش کانفیگ | REZ</b>\n\nانتخاب کنید:", reply_markup=main_kb(message.from_user.id))

@dp.callback_query(F.data=="home")
async def home(c: CallbackQuery):
    await c.message.edit_text("<b>فروش کانفیگ | REZ</b>\n\nانتخاب کنید:", reply_markup=main_kb(c.from_user.id)); await c.answer()

@dp.callback_query(F.data=="buy")
async def buy(c: CallbackQuery):
    await c.message.edit_text("🛒 <b>انتخاب حجم سرویس</b>", reply_markup=plans_kb()); await c.answer()

@dp.callback_query(F.data.startswith("plan:"))
async def choose_plan(c: CallbackQuery, state: FSMContext):
    v=int(c.data.split(":")[1]); stock=available_count(v)
    if stock < 1:
        await c.answer("این حجم فعلاً موجود نیست.", show_alert=True); return
    await state.update_data(volume=v, price=PLANS[v], discount_code=None, discount_amount=0)
    await state.set_state(BuyState.service_name)
    await c.message.answer(f"حجم: <b>{v}GB</b>\nقیمت: <b>{fmt_price(PLANS[v])} تومان</b>\n\nیک نام برای سرویس وارد کنید؛ مثلاً «گوشی من».")
    await c.answer()

@dp.message(BuyState.service_name)
async def service_name(message: Message, state: FSMContext):
    name=message.text.strip()
    if not name or len(name)>60:
        await message.answer("نام سرویس باید بین 1 تا 60 کاراکتر باشد."); return
    await state.update_data(service_name=name)
    await state.set_state(BuyState.discount)
    await message.answer("اگر کد تخفیف دارید ارسال کنید؛ در غیر این صورت <b>ندارم</b> را بفرستید.")

@dp.message(BuyState.discount)
async def discount(message: Message, state: FSMContext):
    data=await state.get_data(); code=message.text.strip()
    amount=0
    if code.lower() not in ("ندارم","ندارم.","none","no"):
        con=db(); row=con.execute("SELECT * FROM discounts WHERE code=? AND active=1",(code.upper(),)).fetchone(); con.close()
        if not row:
            await message.answer("کد تخفیف معتبر نیست. دوباره وارد کنید یا «ندارم» بفرستید."); return
        if row["kind"]=="percent": amount=min(data["price"]*row["value"]//100,data["price"])
        else: amount=min(row["value"],data["price"])
        await state.update_data(discount_code=code.upper(),discount_amount=amount)
    total=data["price"]-amount
    await state.update_data(total=total)
    await state.set_state(BuyState.receipt)
    await message.answer(f"💳 <b>مبلغ نهایی: {fmt_price(total)} تومان</b>\n\nشماره کارت:\n<code>{CARD_NUMBER}</code>\nبه نام: <b>{CARD_NAME}</b>\n\nپس از واریز، <b>عکس رسید</b> را همین‌جا ارسال کنید.")

@dp.message(BuyState.receipt, F.photo)
async def receipt(message: Message, state: FSMContext):
    data=await state.get_data()
    con=db(); cur=con.execute("INSERT INTO orders(user_id,volume,price,service_name,discount_code,discount_amount,receipt_file_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (message.from_user.id,data['volume'],data['price'],data['service_name'],data.get('discount_code'),data.get('discount_amount',0),message.photo[-1].file_id,'pending',datetime.now().isoformat(timespec='seconds'))); oid=cur.lastrowid; con.commit(); con.close()
    await state.clear()
    await message.answer(f"✅ رسید سفارش <b>#{oid}</b> ثبت شد.\nپس از بررسی پرداخت، سرویس به‌صورت خودکار تحویل داده می‌شود.", reply_markup=main_kb(message.from_user.id))
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تأیید و تحویل",callback_data=f"approve:{oid}"),InlineKeyboardButton(text="❌ رد",callback_data=f"reject:{oid}")]])
    await bot.send_photo(ADMIN_ID,message.photo[-1].file_id,caption=f"🛒 <b>سفارش #{oid}</b>\nکاربر: @{message.from_user.username or '-'}\nID: <code>{message.from_user.id}</code>\nحجم: {data['volume']}GB\nقیمت پایه: {fmt_price(data['price'])}\nتخفیف: {fmt_price(data.get('discount_amount',0))}\nمبلغ نهایی: {fmt_price(data['total'])}\nنام سرویس: {data['service_name']}",reply_markup=kb)

@dp.message(BuyState.receipt)
async def receipt_not_photo(message: Message): await message.answer("لطفاً <b>عکس رسید</b> را ارسال کنید.")

@dp.callback_query(F.data.startswith("approve:"))
async def approve(c: CallbackQuery):
    if c.from_user.id!=ADMIN_ID: return await c.answer("دسترسی ندارید.",show_alert=True)
    oid=int(c.data.split(":")[1]); con=db()
    try:
        con.execute("BEGIN IMMEDIATE")
        order=con.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not order or order["status"]!="pending": con.rollback(); await c.answer("این سفارش قبلاً بررسی شده.",show_alert=True); return
        cfg=con.execute("SELECT * FROM configs WHERE volume=? AND status='available' ORDER BY id LIMIT 1",(order['volume'],)).fetchone()
        if not cfg: con.rollback(); await c.answer("موجودی این حجم تمام شده است.",show_alert=True); return
        now=datetime.now().isoformat(timespec='seconds')
        con.execute("UPDATE configs SET status='sold',sold_to=?,sold_at=? WHERE id=?",(order['user_id'],now,cfg['id']))
        con.execute("UPDATE orders SET status='approved',config_id=?,decided_at=? WHERE id=?",(cfg['id'],now,oid))
        con.execute("INSERT INTO services(user_id,order_id,name,volume,config,created_at) VALUES(?,?,?,?,?,?)",(order['user_id'],oid,order['service_name'],order['volume'],cfg['config'],now))
        if order['discount_code']: con.execute("UPDATE discounts SET uses=uses+1 WHERE code=?",(order['discount_code'],))
        con.commit()
    except Exception:
        con.rollback(); con.close(); raise
    con.close()
    qr=qrcode.make(cfg['config']); bio=io.BytesIO(); qr.save(bio,format='PNG'); bio.seek(0)
    await bot.send_message(order['user_id'],f"🎉 <b>پرداخت سفارش #{oid} تأیید شد.</b>\n\nسرویس: <b>{order['service_name']}</b>\nحجم: {order['volume']}GB\n\nکانفیگ:\n<code>{cfg['config']}</code>")
    await bot.send_photo(order['user_id'],BufferedInputFile(bio.getvalue(),filename='rez-qr.png'),caption=f"📱 QR Code سرویس «{order['service_name']}»")
    await c.message.edit_reply_markup(reply_markup=None); await c.answer("تأیید شد و تحویل انجام شد.")

@dp.callback_query(F.data.startswith("reject:"))
async def reject(c: CallbackQuery):
    if c.from_user.id!=ADMIN_ID: return await c.answer("دسترسی ندارید.",show_alert=True)
    oid=int(c.data.split(":")[1]); con=db(); row=con.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if row and row['status']=='pending': con.execute("UPDATE orders SET status='rejected',decided_at=? WHERE id=?",(datetime.now().isoformat(timespec='seconds'),oid)); con.commit(); await bot.send_message(row['user_id'],f"❌ سفارش #{oid} رد شد. برای پیگیری با پشتیبانی تماس بگیرید.")
    con.close(); await c.message.edit_reply_markup(reply_markup=None); await c.answer("رد شد.")

@dp.callback_query(F.data=="services")
async def services(c: CallbackQuery):
    con=db(); rows=con.execute("SELECT * FROM services WHERE user_id=? ORDER BY id DESC",(c.from_user.id,)).fetchall(); con.close()
    if not rows: return await c.message.edit_text("📦 هنوز سرویسی ندارید.",reply_markup=main_kb(c.from_user.id))
    text="📦 <b>سرویس‌های من</b>\n\n"; kb=[]
    for r in rows: text+=f"#{r['id']} — {r['name']} — {r['volume']}GB\n"; kb.append([InlineKeyboardButton(text=f"📄 {r['name']}",callback_data=f"svc:{r['id']}")])
    kb.append([InlineKeyboardButton(text="🔙 برگشت",callback_data="home")]); await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await c.answer()

@dp.callback_query(F.data.startswith("svc:"))
async def service_detail(c: CallbackQuery):
    sid=int(c.data.split(":")[1]); con=db(); r=con.execute("SELECT * FROM services WHERE id=? AND user_id=?",(sid,c.from_user.id)).fetchone(); con.close()
    if not r: return await c.answer("یافت نشد.",show_alert=True)
    qr=qrcode.make(r['config']); bio=io.BytesIO(); qr.save(bio,format='PNG'); bio.seek(0)
    await c.message.answer(f"<b>{r['name']}</b>\nحجم: {r['volume']}GB\n\n<code>{r['config']}</code>")
    await c.message.answer_photo(BufferedInputFile(bio.getvalue(),filename='qr.png'),caption="📱 QR Code")
    await c.answer()

@dp.callback_query(F.data=="orders")
async def orders(c: CallbackQuery):
    con=db(); rows=con.execute("SELECT id,volume,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(c.from_user.id,)).fetchall(); con.close()
    if not rows: text="💳 سفارشی ثبت نشده است."
    else:
        labels={'pending':'⏳ در انتظار بررسی','approved':'✅ تأیید شده','rejected':'❌ رد شده'}
        text="💳 <b>سفارش‌های اخیر</b>\n\n"+"\n".join(f"#{r['id']} — {r['volume']}GB — {labels.get(r['status'],r['status'])}" for r in rows)
    await c.message.edit_text(text,reply_markup=main_kb(c.from_user.id)); await c.answer()

@dp.callback_query(F.data=="discount")
async def discount_help(c: CallbackQuery): await c.message.answer("🎁 کد تخفیف خود را هنگام خرید وارد کنید."); await c.answer()

@dp.callback_query(F.data=="admin")
async def admin(c: CallbackQuery):
    if c.from_user.id!=ADMIN_ID: return await c.answer("دسترسی ندارید.",show_alert=True)
    await c.message.edit_text("⚙️ <b>پنل مدیریت REZ</b>",reply_markup=admin_kb()); await c.answer()

@dp.callback_query(F.data=="a_stock")
async def stock(c: CallbackQuery):
    if c.from_user.id!=ADMIN_ID:return
    text="📦 <b>موجودی</b>\n\n"; 
    for v in PLANS: text+=f"{v}GB: <b>{available_count(v)}</b>" + (" ⚠️" if available_count(v)<=LOW_STOCK else "") + "\n"
    await c.message.answer(text); await c.answer()

@dp.callback_query(F.data=="a_add")
async def add_start(c: CallbackQuery,state:FSMContext):
    if c.from_user.id!=ADMIN_ID:return
    await state.set_state(AddConfigState.volume); await c.message.answer("حجم را بفرستید: 10، 20، 30، 40، 50، 100، 200 یا 500"); await c.answer()

@dp.message(AddConfigState.volume)
async def add_volume(m:Message,state:FSMContext):
    try:v=int(m.text.strip())
    except: return await m.answer("حجم نامعتبر است.")
    if v not in PLANS:return await m.answer("یکی از حجم‌های موجود را وارد کنید.")
    await state.update_data(volume=v); await state.set_state(AddConfigState.configs); await m.answer("حالا کانفیگ‌ها را بفرستید، هر کانفیگ در یک خط. برای پایان <b>/done</b> بفرستید.")

@dp.message(AddConfigState.configs)
async def add_configs(m:Message,state:FSMContext):
    if m.text.strip()=="/done":
        d=await state.get_data(); await state.clear(); await m.answer("✅ افزودن کانفیگ‌ها تمام شد.",reply_markup=admin_kb()); return
    lines=[x.strip() for x in m.text.splitlines() if x.strip()]
    lines=[x for x in lines if x.startswith("vless://")]
    if not lines:return await m.answer("فقط خطوطی که با vless:// شروع می‌شوند ثبت می‌شوند.")
    d=await state.get_data(); con=db(); con.executemany("INSERT INTO configs(volume,config) VALUES(?,?)",[(d['volume'],x) for x in lines]); con.commit(); con.close(); await m.answer(f"✅ {len(lines)} کانفیگ برای {d['volume']}GB اضافه شد. برای ادامه کانفیگ بفرستید یا /done")

@dp.callback_query(F.data=="a_users")
async def a_users(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID:return
    con=db(); n=con.execute("SELECT COUNT(*) FROM users").fetchone()[0]; con.close(); await c.message.answer(f"👥 تعداد کاربران ثبت‌شده: <b>{n}</b>"); await c.answer()

@dp.callback_query(F.data=="a_orders")
async def a_orders(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID:return
    con=db(); rows=con.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 15").fetchall(); con.close();
    if not rows:return await c.message.answer("سفارشی نیست.")
    labels={'pending':'⏳','approved':'✅','rejected':'❌'}
    await c.message.answer("🛒 <b>آخرین سفارش‌ها</b>\n\n"+"\n".join(f"#{r['id']} | {r['volume']}GB | {labels.get(r['status'],r['status'])} | {r['service_name']}" for r in rows)); await c.answer()

@dp.callback_query(F.data=="a_stats")
async def a_stats(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID:return
    con=db(); sales=con.execute("SELECT COUNT(*) n,COALESCE(SUM(price-discount_amount),0) total FROM orders WHERE status='approved'").fetchone(); inv=con.execute("SELECT COUNT(*) FROM configs WHERE status='available'").fetchone()[0]; con.close()
    await c.message.answer(f"📊 <b>آمار</b>\nفروش موفق: {sales['n']}\nدرآمد ثبت‌شده: {fmt_price(sales['total'])} تومان\nموجودی کل: {inv}"); await c.answer()

@dp.callback_query(F.data=="a_discounts")
async def a_discounts(c:CallbackQuery,state:FSMContext):
    if c.from_user.id!=ADMIN_ID:return
    await state.set_state(DiscountState.code); await c.message.answer("کد تخفیف را بفرستید (مثلاً REZ10):"); await c.answer()

@dp.message(DiscountState.code)
async def d_code(m:Message,state:FSMContext): await state.update_data(code=m.text.strip().upper()); await state.set_state(DiscountState.kind); await m.answer("نوع را بفرستید: percent یا fixed")
@dp.message(DiscountState.kind)
async def d_kind(m:Message,state:FSMContext):
    k=m.text.strip().lower()
    if k not in ('percent','fixed'):return await m.answer("فقط percent یا fixed")
    await state.update_data(kind=k); await state.set_state(DiscountState.value); await m.answer("مقدار تخفیف را عددی بفرستید.")
@dp.message(DiscountState.value)
async def d_value(m:Message,state:FSMContext):
    try:v=int(m.text.strip())
    except:return await m.answer("عدد نامعتبر است.")
    d=await state.get_data(); con=db(); con.execute("INSERT OR REPLACE INTO discounts(code,kind,value,active) VALUES(?,?,?,1)",(d['code'],d['kind'],v)); con.commit(); con.close(); await state.clear(); await m.answer(f"✅ کد {d['code']} ساخته شد.",reply_markup=admin_kb())

@dp.callback_query(F.data=="a_broadcast")
async def a_broadcast(c:CallbackQuery,state:FSMContext):
    if c.from_user.id!=ADMIN_ID:return
    await state.set_state(BroadcastState.text); await c.message.answer("متن پیام همگانی را بفرستید:"); await c.answer()

@dp.message(BroadcastState.text)
async def broadcast(m:Message,state:FSMContext):
    if m.from_user.id!=ADMIN_ID:return
    await state.clear(); con=db(); ids=[r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]; con.close(); sent=0
    for uid in ids:
        try: await bot.send_message(uid,m.text); sent+=1
        except: pass
        await asyncio.sleep(.04)
    await m.answer(f"📢 پیام برای {sent} کاربر ارسال شد.",reply_markup=admin_kb())

async def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    init_db(); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
