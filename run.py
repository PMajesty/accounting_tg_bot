import logging
import pg8000
import csv
import io
import os
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

load_dotenv()

TOKEN = os.getenv("TOKEN")
ALLOWED_USER = os.getenv("ALLOWED_USER")

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

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
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount) FROM transactions")
        total = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT amount, comment FROM transactions ORDER BY id DESC LIMIT 5")
        last_ops = cursor.fetchall()
        cursor.close()

    ops_text = "\n".join([f"*{row[0]:+.2f}* ({row[1]})" for row in last_ops])
    
    keyboard = [
        [InlineKeyboardButton("Итого", callback_data="total")],
        [InlineKeyboardButton("История", callback_data="history_0")],
        [InlineKeyboardButton("Экспорт CSV", callback_data="export")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"Бот бухгалтерии готов.\nБаланс: *{total:.2f}*\n\nПоследние операции:\n{ops_text}", reply_markup=reply_markup, parse_mode='Markdown')

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
            
        await update.message.reply_text(f"Записано: *{amount}* ({comment})", reply_markup=menu_markup, parse_mode='Markdown')
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
        
        await query.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Общий баланс: *{total:.2f}*", reply_markup=menu_markup, parse_mode='Markdown')
        
    elif query.data == "export":
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(["ID", "Date", "Amount", "Comment", "Sum"])
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, date, amount, comment, SUM(amount) OVER (ORDER BY id ASC) 
                FROM transactions
            """)
            writer.writerows(cursor.fetchall())
            cursor.close()
            
        output.seek(0)
        await context.bot.send_document(chat_id=update.effective_chat.id, document=io.BytesIO(output.getvalue().encode("utf-8-sig")), filename="accounting.csv")
        
        await query.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Экспорт завершен.", reply_markup=menu_markup)
        
    elif query.data.startswith("history_"):
        page = int(query.data.split("_")[1])
        limit = 10
        offset = page * limit
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                WITH data AS (
                    SELECT amount, comment, SUM(amount) OVER (ORDER BY id ASC) as rt, id 
                    FROM transactions
                )
                SELECT rt, amount, comment FROM data ORDER BY id DESC LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM transactions")
            total_count = cursor.fetchone()[0]
            cursor.close()
            
        text = f"История (Страница {page + 1}):\n\n"
        for row in rows:
            amount_fmt = f"+{row[1]}" if row[1] > 0 else f"{row[1]}"
            text += f"*{row[0]:.2f}* | *{amount_fmt}* | {row[2]}\n"
            
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton("<< Назад", callback_data=f"history_{page-1}"))
        if offset + limit < total_count:
            buttons.append(InlineKeyboardButton("Вперед >>", callback_data=f"history_{page+1}"))
        
        buttons.append(InlineKeyboardButton("В меню", callback_data="menu"))
        reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None
        
        await query.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode='Markdown')

    elif query.data == "menu":
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(amount) FROM transactions")
            total = cursor.fetchone()[0] or 0
            
            cursor.execute("SELECT amount, comment FROM transactions ORDER BY id DESC LIMIT 5")
            last_ops = cursor.fetchall()
            cursor.close()

        ops_text = "\n".join([f"*{row[0]:+.2f}* ({row[1]})" for row in last_ops])

        keyboard = [
            [InlineKeyboardButton("Итого", callback_data="total")],
            [InlineKeyboardButton("История", callback_data="history_0")],
            [InlineKeyboardButton("Экспорт CSV", callback_data="export")]
        ]
        
        await query.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Бот бухгалтерии готов.\nБаланс: *{total:.2f}*\n\nПоследние операции:\n{ops_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

if __name__ == '__main__':
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.run_polling()