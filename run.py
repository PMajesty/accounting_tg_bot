import logging
import pg8000
import csv
import io
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

TOKEN = "8430205853:AAEAOq6HHPu7JzWhUDNAfB8XmEYD2iPkosw"
ALLOWED_USER = "Artyom_dio"

DB_USER = "telegram_bot"
DB_PASSWORD = "AppStr0ngPass!"
DB_HOST = "localhost"
DB_NAME = "telegram_ai_bot"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def get_db_connection():
    return pg8000.connect(
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        database=DB_NAME
    )

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                date TEXT,
                amount REAL,
                comment TEXT
            )
        """)
        cursor.close()
        conn.commit()

async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username
    if user != ALLOWED_USER:
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    keyboard = [
        [InlineKeyboardButton("Итого", callback_data="total")],
        [InlineKeyboardButton("История", callback_data="history_0")],
        [InlineKeyboardButton("Экспорт CSV", callback_data="export")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Бот бухгалтерии готов.", reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    text = update.message.text.strip()
    
    menu_markup = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])

    try:
        parts = text.split(maxsplit=1)
        amount_str = parts[0]
        comment = parts[1] if len(parts) > 1 else ""
        
        if amount_str.startswith("+"):
            amount = float(amount_str[1:])
        else:
            amount = -float(amount_str)
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO transactions (date, amount, comment) VALUES (%s, %s, %s)", 
                            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), amount, comment))
            cursor.close()
            conn.commit()
            
        await update.message.reply_text(f"Записано: {amount} ({comment})", reply_markup=menu_markup)
    except ValueError:
        await update.message.reply_text("Неверный формат. Используйте: [сумма] [комментарий] или +[сумма] [комментарий]", reply_markup=menu_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    menu_markup = InlineKeyboardMarkup([[InlineKeyboardButton("В меню", callback_data="menu")]])

    if query.data == "total":
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(amount) FROM transactions")
            total = cursor.fetchone()[0] or 0
            cursor.close()
        await query.edit_message_text(text=f"Общий баланс: {total:.2f}", reply_markup=menu_markup)
        
    elif query.data == "export":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Date", "Amount", "Comment"])
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transactions")
            writer.writerows(cursor.fetchall())
            cursor.close()
            
        output.seek(0)
        await context.bot.send_document(chat_id=update.effective_chat.id, document=io.BytesIO(output.getvalue().encode()), filename="accounting.csv")
        await query.edit_message_text("Экспорт завершен.", reply_markup=menu_markup)
        
    elif query.data.startswith("history_"):
        page = int(query.data.split("_")[1])
        limit = 10
        offset = page * limit
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT date, amount, comment FROM transactions ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM transactions")
            total_count = cursor.fetchone()[0]
            cursor.close()
            
        text = f"История (Страница {page + 1}):\n\n"
        for row in rows:
            text += f"{row[0]} | {row[1]} | {row[2]}\n"
            
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton("<< Назад", callback_data=f"history_{page-1}"))
        if offset + limit < total_count:
            buttons.append(InlineKeyboardButton("Вперед >>", callback_data=f"history_{page+1}"))
        
        buttons.append(InlineKeyboardButton("В меню", callback_data="menu"))
        reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None
        
        await query.edit_message_text(text=text, reply_markup=reply_markup)

    elif query.data == "menu":
        keyboard = [
            [InlineKeyboardButton("Итого", callback_data="total")],
            [InlineKeyboardButton("История", callback_data="history_0")],
            [InlineKeyboardButton("Экспорт CSV", callback_data="export")]
        ]
        await query.edit_message_text("Бот бухгалтерии готов.", reply_markup=InlineKeyboardMarkup(keyboard))

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.run_polling()