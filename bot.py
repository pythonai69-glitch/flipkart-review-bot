import os
import asyncio
from dotenv import load_dotenv
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from scraper import get_review_url, extract_reviews_from_html, fetch_reviews, fetch_reviews_by_name, resolve_url
import pymongo
import logging

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

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
        logger.error(f"Failed to connect to MongoDB: {e}")

def save_user(chat_id):
    try:
        chat_id_str = str(chat_id)
        mongo_success = False
        if users_collection is not None:
            try:
                users_collection.update_one({"chat_id": chat_id_str}, {"$set": {"chat_id": chat_id_str}}, upsert=True)
                mongo_success = True
            except Exception as e:
                logger.error(f"MongoDB Error: {e}")
                
        if not mongo_success:
            users = set()
            if os.path.exists('users.txt'):
                with open('users.txt', 'r') as f:
                    for line in f:
                        users.add(line.strip())
            if chat_id_str not in users:
                with open('users.txt', 'a') as f:
                    f.write(chat_id_str + '\n')
    except Exception as e:
        logger.error(f"Error in save_user: {e}")
# States for conversation
WAITING_FOR_LINK = 1
WAITING_FOR_SORT = 2
WAITING_FOR_COUNT = 3
WAITING_FOR_NAME = 4

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_chat.id)
    await update.message.reply_text("Hello! Please send me a Flipkart product link to fetch its reviews.")
    return WAITING_FOR_LINK

async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_chat.id)
    text = update.message.text or ""
    
    # Extract URL from message (supports sharing directly from Flipkart app with product title)
    match = re.search(r'https?://[^\s]+', text)
    if not match:
        await update.message.reply_text("❌ Please provide a valid Flipkart link.")
        return WAITING_FOR_LINK
        
    raw_url = match.group(0)
    if 'flipkart.com' not in raw_url and 'fktr.in' not in raw_url:
        await update.message.reply_text("❌ Please provide a valid Flipkart link.")
        return WAITING_FOR_LINK
        
    # Resolve short links (dl.flipkart.com/s/..., fktr.in/...) immediately
    resolved_url = resolve_url(raw_url)
    logger.info(f"User {update.effective_chat.id} sent link: {raw_url} -> Resolved: {resolved_url}")
    
    context.user_data['url'] = resolved_url
    
    keyboard = [
        [InlineKeyboardButton("Most Recent", callback_data='recent')],
        [InlineKeyboardButton("Positive First", callback_data='positive')],
        [InlineKeyboardButton("Negative First", callback_data='negative')],
        [InlineKeyboardButton("Most Helpful", callback_data='helpful')],
        [InlineKeyboardButton("🔍 Find Specific Review by Name", callback_data='find_by_name')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Which reviews do you want to see?", reply_markup=reply_markup)
    return WAITING_FOR_SORT

async def select_sort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'find_by_name':
        await query.edit_message_text("Please type the exact Flipkart account name you used for the review:")
        return WAITING_FOR_NAME
        
    context.user_data['sort'] = query.data
    
    keyboard = [
        [InlineKeyboardButton("10", callback_data='10'), InlineKeyboardButton("30", callback_data='30')],
        [InlineKeyboardButton("60", callback_data='60'), InlineKeyboardButton("100", callback_data='100')],
        [InlineKeyboardButton("200", callback_data='200'), InlineKeyboardButton("All Reviews", callback_data='all')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text=f"Selected: {query.data}\nHow many reviews do you want?", reply_markup=reply_markup)
    return WAITING_FOR_COUNT

async def select_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    count_data = query.data
    if count_data == 'all':
        count = 10000  # large number to fetch all available
        display_count = "all"
    else:
        count = int(count_data)
        display_count = str(count)
        
    context.user_data['count'] = count
    url = context.user_data['url']
    sort_order = context.user_data['sort']
    
    await query.edit_message_text(text=f"Fetching {display_count} {sort_order} reviews... Please wait!")
    
    # Run the synchronous scraper in an executor to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    try:
        reviews = await loop.run_in_executor(None, fetch_reviews, url, sort_order, count)
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Error scraping reviews: {e}")
        return ConversationHandler.END
        
    if not reviews:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text="❌ Is product par koi reviews nahi mile (No reviews found).\n\nKripya check karein:\n1. Kya is product par pehle se reviews available hain?\n2. Link sahi aur active product ka hai."
        )
        return ConversationHandler.END
        
    import html
    from telegram import CopyTextButton
    chat_id_val = update.effective_chat.id

    # Send each review as individual message with native copy button
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

        review_html = (
            f"<blockquote><b>{idx+1}. {title}</b>\n"
            f"{stars} {r.get('rating', '')}/5 • 🗓️ {date_str}\n"
            f"{body}\n"
            f"- <b>{author}</b></blockquote>"
        )

        # Build buttons
        buttons = []
        if review_link:
            row = [
                InlineKeyboardButton("📋 Copy Link", copy_text=CopyTextButton(text=review_link)),
                InlineKeyboardButton("🔗 View Review", url=review_link)
            ]
            buttons.append(row)

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

        await context.bot.send_message(
            chat_id=chat_id_val,
            text=review_html,
            parse_mode='HTML',
            reply_markup=reply_markup
        )

    await context.bot.send_message(chat_id=chat_id_val, text="✅ All done! Send another link to start again.")
    return WAITING_FOR_LINK

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    url = context.user_data['url']
    
    msg = await update.message.reply_text(f"Searching for reviews by '{name}'... This might take a few seconds!")
    
    # Run the synchronous scraper in an executor
    loop = asyncio.get_event_loop()
    try:
        reviews = await loop.run_in_executor(None, fetch_reviews_by_name, url, name)
    except Exception as e:
        await msg.edit_text(f"Error scraping reviews: {e}")
        return ConversationHandler.END
        
    if not reviews:
        await msg.edit_text(f"No reviews found for name '{name}'.")
        return ConversationHandler.END
        
    import html
    from telegram import CopyTextButton
    chat_id_val = update.effective_chat.id
    
    await msg.edit_text(f"Found {len(reviews)} review(s) for '{name}':")
    
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

        review_html = (
            f"<blockquote><b>{idx+1}. {title}</b>\n"
            f"{stars} {r.get('rating', '')}/5 • 🗓️ {date_str}\n"
            f"{body}\n"
            f"- <b>{author}</b></blockquote>"
        )

        buttons = []
        if review_link:
            row = [
                InlineKeyboardButton("📋 Copy Link", copy_text=CopyTextButton(text=review_link)),
                InlineKeyboardButton("🔗 View Review", url=review_link)
            ]
            buttons.append(row)

        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

        await context.bot.send_message(
            chat_id=chat_id_val,
            text=review_html,
            parse_mode='HTML',
            reply_markup=reply_markup
        )

    await context.bot.send_message(chat_id=chat_id_val, text="✅ Search complete! Send another link to start again.")
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
            WAITING_FOR_COUNT: [CallbackQueryHandler(select_count)],
            WAITING_FOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('broadcast', broadcast))
    logger.info("Bot is polling...")
    application.run_polling()
