import os
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from scraper import get_review_url, extract_reviews_from_html, fetch_reviews
import pymongo

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
MONGO_URI = os.getenv("MONGO_URI")

db_client = None
db = None
users_collection = None

if MONGO_URI:
    try:
        db_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = db_client["flipkart_bot"]
        users_collection = db["users"]
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")

def save_user(chat_id):
    chat_id_str = str(chat_id)
    mongo_success = False
    if users_collection is not None:
        try:
            users_collection.update_one({"chat_id": chat_id_str}, {"$set": {"chat_id": chat_id_str}}, upsert=True)
            mongo_success = True
        except Exception as e:
            print(f"MongoDB Error: {e}")
            
    if not mongo_success:
        users = set()
        if os.path.exists('users.txt'):
            with open('users.txt', 'r') as f:
                for line in f:
                    users.add(line.strip())
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
        
    import html
    chat_id_val = update.effective_chat.id
    
    # Send each review as an individual message with a Copy button
    for idx, r in enumerate(reviews):
        try:
            rating_num = int(float(r.get('rating', 0)))
        except ValueError:
            rating_num = 0
            
        stars = "⭐" * rating_num
        date_str = html.escape(r.get('created', ''))
        title = html.escape(r.get('title', ''))
        body = html.escape(r.get('body', ''))
        author = html.escape(r.get('author', 'Unknown'))
        review_link = r.get('url', '')
        
        # HTML formatted message
        review_html = (
            f"<blockquote><b>{idx+1}. {title}</b>\n"
            f"{stars} {r.get('rating', '')}/5 • 🗓️ {date_str}\n"
            f"{body}\n"
            f"- <b>{author}</b>"
        )
        if review_link:
            escaped_url = html.escape(review_link)
            review_html += f"\n🔗 <a href=\"{escaped_url}\">View Review</a>"
        review_html += "</blockquote>"
        
        # Plain text for copy (no HTML tags)
        plain_text = (
            f"{idx+1}. {r.get('title', '')}\n"
            f"{stars} {r.get('rating', '')}/5 | {r.get('created', '')}\n"
            f"{r.get('body', '')}\n"
            f"- {r.get('author', 'Unknown')}"
        )
        if review_link:
            plain_text += f"\n🔗 {review_link}"
        
        # Store plain text in bot_data with a unique key
        copy_key = f"copy_{chat_id_val}_{idx}"
        context.bot_data[copy_key] = plain_text
        
        # Build inline keyboard
        buttons = [[InlineKeyboardButton("📋 Copy Text", callback_data=f"copyreview:{copy_key}")]]
        if review_link:
            buttons[0].append(InlineKeyboardButton("🔗 View Review", url=review_link))
        reply_markup = InlineKeyboardMarkup(buttons)
        
        await context.bot.send_message(
            chat_id=chat_id_val,
            text=review_html,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
    await context.bot.send_message(chat_id=chat_id_val, text="✅ All done! Send another link to start again.")
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
    fallback = False
    
    if users_collection is not None:
        try:
            for user_doc in users_collection.find({}):
                uid = user_doc.get("chat_id")
                if uid and uid != chat_id:
                    try:
                        await context.bot.send_message(chat_id=int(uid), text=message)
                        success += 1
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        failed += 1
        except Exception as e:
            print(f"MongoDB Error: {e}")
            fallback = True
    else:
        fallback = True
        
    if fallback:
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

async def copy_review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 📋 Copy Text button — sends plain text so user can easily copy."""
    query = update.callback_query
    await query.answer("📋 Review text sent below!", show_alert=False)
    
    copy_key = query.data.replace("copyreview:", "", 1)
    plain_text = context.bot_data.get(copy_key)
    
    if plain_text:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📋 <b>Copy this review:</b>\n\n<code>{plain_text}</code>",
            parse_mode='HTML'
        )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⚠️ Review text not found. Please fetch reviews again."
        )

from keep_alive import keep_alive

if __name__ == '__main__':
    import asyncio
    # Fix for Python 3.12+ / 3.14 where no default event loop is created
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

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
    application.add_handler(CallbackQueryHandler(copy_review_handler, pattern='^copyreview:'))
    print("Bot is polling...")
    application.run_polling()
