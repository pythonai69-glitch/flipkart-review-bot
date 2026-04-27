import concurrent.futures
from curl_cffi import requests
from scraper import resolve_url, get_review_url, extract_reviews_from_html
import time

def test_fetch(product_url, target_name, max_pages=500):
    start = time.time()
    all_matched = []
    target_name_normalized = " ".join(target_name.lower().split())
    
    product_url = resolve_url(product_url)
    
    def fetch_page(page):
        url = get_review_url(product_url, 'MOST_RECENT', page)
        try:
            response = requests.get(url, impersonate="chrome119", timeout=10)
            if response.status_code == 200:
                return extract_reviews_from_html(response.text)
        except Exception as e:
            pass
        return []

    chunk_size = 50
    for chunk_start in range(1, max_pages + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size, max_pages + 1)
        print(f"Fetching pages {chunk_start} to {chunk_end-1}...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_page = {executor.submit(fetch_page, page): page for page in range(chunk_start, chunk_end)}
            for future in concurrent.futures.as_completed(future_to_page):
                page_reviews = future.result()
                for r in page_reviews:
                    author_normalized = " ".join(r.get('author', '').lower().split())
                    if target_name_normalized in author_normalized:
                        all_matched.append(r)
                        
        if all_matched:
            break
            
    print(f"Time taken: {time.time() - start:.2f}s")
    return all_matched

if __name__ == "__main__":
    res = test_fetch("https://dl.flipkart.com/s/_USF2BNNNN", "Savita Joshi")
    print("Found:", len(res))
    for r in res:
        print(r['author'], r['title'])
