import os
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from scraper import get_review_url, extract_reviews_from_html, fetch_reviews

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

def save_user(chat_id):
    users = set()
    if os.path.exists('users.txt'):
        with open('users.txt', 'r') as f:
            for line in f:
                users.add(line.strip())
    chat_id_str = str(chat_id)
    if chat_id_str not in users:
        with open('users.txt', 'a') as f:
            f.write(chat_id_str + '\n')
# States for conversation
WAITING_FOR_LINK = 1
WAITING_FOR_SORT = 2
WAITING_FOR_COUNT = 3

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_chat.id)
    await update.message.reply_text("Hello! Please send me a Flipkart product link to fetch its reviews.")
    return WAITING_FOR_LINK

async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_chat.id)
    url = update.message.text
    if 'flipkart.com' not in url:
        await update.message.reply_text("Please provide a valid Flipkart link.")
        return WAITING_FOR_LINK
        
    context.user_data['url'] = url
    
    keyboard = [
        [InlineKeyboardButton("Most Recent", callback_data='recent')],
        [InlineKeyboardButton("Positive First", callback_data='positive')],
        [InlineKeyboardButton("Negative First", callback_data='negative')],
        [InlineKeyboardButton("Most Helpful", callback_data='helpful')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Which reviews do you want to see?", reply_markup=reply_markup)
    return WAITING_FOR_SORT

async def select_sort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['sort'] = query.data
    
    keyboard = [
        [InlineKeyboardButton("5", callback_data='5'), InlineKeyboardButton("10", callback_data='10')],
        [InlineKeyboardButton("20", callback_data='20'), InlineKeyboardButton("30", callback_data='30')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text=f"Selected: {query.data}\nHow many reviews do you want?", reply_markup=reply_markup)
    return WAITING_FOR_COUNT

async def select_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    count = int(query.data)
    context.user_data['count'] = count
    url = context.user_data['url']
    sort_order = context.user_data['sort']
    
    await query.edit_message_text(text=f"Fetching {count} {sort_order} reviews... Please wait!")
    
    # Run the synchronous scraper in an executor to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    try:
        reviews = await loop.run_in_executor(None, fetch_reviews, url, sort_order, count)
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Error scraping reviews: {e}")
        return ConversationHandler.END
        
    if not reviews:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="No reviews could be extracted. The product might have no reviews or the bot was blocked.")
        return ConversationHandler.END
        
    # Send reviews in chunks to avoid hitting Telegram's message length limits
    chunk = ""
    for idx, r in enumerate(reviews):
        # Format the review beautifully mimicking a card
        certified_badge = "✅ Certified Buyer" if r.get('certified') else ""
        loc_str = f"📍 {r['location']}  •  " if r.get('location') else ""
        date_str = f"🕒 {r['created']}  " if r.get('created') else ""
        meta_line = f"{loc_str}{date_str}{certified_badge}".strip()
        link_line = f"\n🔗 [Original Flipkart Review]({r.get('url')})" if r.get('url') else ""
        
        review_text = (
            f"⭐️ {r['rating']}  |  👤 {r.get('author', 'Unknown')}\n"
            f"*{r['title']}*\n"
            f"{r['body']}\n\n"
            f"_{meta_line}_{link_line}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        # Telegram max length is 4096, if chunk gets too big, send it and clear
        if len(chunk) + len(review_text) > 4000:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=chunk, parse_mode='Markdown')
            chunk = ""
            
        chunk += review_text
        
    # Send the remainder
    if chunk:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=chunk, parse_mode='Markdown')
        
    await context.bot.send_message(chat_id=update.effective_chat.id, text="All done! Send another link to start again.")
    return WAITING_FOR_LINK

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Cancelled.')
    return ConversationHandler.END

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    admin_ids = [id.strip() for id in (ADMIN_ID or "").split(",") if id.strip()]
    if chat_id not in admin_ids:
        await update.message.reply_text("You are not authorized to use this command.")
        return
        
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Please provide a message to broadcast. Usage: /broadcast Hello everyone!")
        return
        
    await update.message.reply_text("Starting broadcast...")
    success = 0
    failed = 0
    
    if os.path.exists('users.txt'):
        with open('users.txt', 'r') as f:
            for line in f:
                uid = line.strip()
                if uid and uid != chat_id:
                    try:
                        await context.bot.send_message(chat_id=int(uid), text=message)
                        success += 1
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        failed += 1
                        
    await update.message.reply_text(f"Broadcast complete.\nSuccessful: {success}\nFailed: {failed}")

from keep_alive import keep_alive

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link)],
        states={
            WAITING_FOR_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link)],
            WAITING_FOR_SORT: [CallbackQueryHandler(select_sort)],
            WAITING_FOR_COUNT: [CallbackQueryHandler(select_count)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('broadcast', broadcast))
    print("Bot is polling...")
    application.run_polling()
