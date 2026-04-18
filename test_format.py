import html
import asyncio
from telegram import Bot
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID").split(",")[0]  # Get first admin ID

reviews = [{'rating': '5', 'created': '16 days ago', 'title': 'Highly recommended', 'body': 'Good futures good looking very useful watch', 'author': 'Flipkart Customer', 'url': 'http://example.com'}]

import sys
sys.stdout.reconfigure(encoding='utf-8')

async def test_send():
    b = Bot(token=TOKEN)
    chunk = ""
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
        
        review_text = (
            f"<blockquote><b>{idx+1}. {title}</b>  ❞\n"
            f"{stars} {r.get('rating', '')}/5 • 🗓️ {date_str}\n"
            f"{body}\n"
            f"- <b>{author}</b>"
        )
        if r.get('url'):
            review_text += f"\n🔗 <a href=\"{r['url']}\">View Review</a>"
        review_text += "</blockquote>\n\n"
        chunk += review_text
        
    await b.send_message(chat_id=int(ADMIN_ID), text=chunk, parse_mode='HTML')

asyncio.run(test_send())
