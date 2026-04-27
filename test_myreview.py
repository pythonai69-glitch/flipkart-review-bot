import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot
from scraper import fetch_reviews, fetch_reviews_by_name

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TEST_CHAT_ID = 5245051865

async def test():
    bot = Bot(token=TOKEN)
    
    # Send a starting message
    await bot.send_message(chat_id=TEST_CHAT_ID, text="🤖 Testing the 'Find My Review' feature locally...")
    
    # Let's test with a known product url, e.g. a popular mobile phone
    product_url = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4?pid=MOBGTAGPTB3VS24W"
    
    await bot.send_message(chat_id=TEST_CHAT_ID, text="Fetching first page to find a random reviewer name...")
    
    # Get standard reviews to find a name
    reviews = fetch_reviews(product_url, 'MOST_RECENT', 10)
    if not reviews:
        await bot.send_message(chat_id=TEST_CHAT_ID, text="Failed to fetch initial reviews. Stopping test.")
        return
        
    test_name = reviews[0].get('author', '')
    if not test_name:
        await bot.send_message(chat_id=TEST_CHAT_ID, text="No author found in the first review. Stopping test.")
        return
        
    await bot.send_message(chat_id=TEST_CHAT_ID, text=f"Found a reviewer named '{test_name}'. Now using `fetch_reviews_by_name` to search for this name...")
    
    # Now use the new function
    matched_reviews = fetch_reviews_by_name(product_url, test_name, max_pages=10)
    
    if not matched_reviews:
        await bot.send_message(chat_id=TEST_CHAT_ID, text=f"Test Failed: Could not find reviews for '{test_name}' using the search function.")
        return
        
    import html
    await bot.send_message(chat_id=TEST_CHAT_ID, text=f"✅ Test Passed! Found {len(matched_reviews)} review(s) for '{test_name}':")
    
    for idx, r in enumerate(matched_reviews):
        try:
            rating_num = int(float(r.get('rating', 0)))
        except ValueError:
            rating_num = 0
            
        stars = "⭐" * rating_num
        date_str = html.escape(r.get('created', ''))
        title = html.escape(r.get('title', ''))
        body = html.escape(r.get('body', ''))
        author = html.escape(r.get('author', 'Unknown'))
        
        review_html = (
            f"<blockquote><b>{idx+1}. {title}</b>\n"
            f"{stars} {r.get('rating', '')}/5 • 🗓️ {date_str}\n"
            f"{body}\n"
            f"- <b>{author}</b></blockquote>"
        )
        
        await bot.send_message(chat_id=TEST_CHAT_ID, text=review_html, parse_mode='HTML')
        
    await bot.send_message(chat_id=TEST_CHAT_ID, text="Test script finished successfully. 🎉")

if __name__ == "__main__":
    asyncio.run(test())
