import re
import urllib.parse
from curl_cffi import requests
from bs4 import BeautifulSoup

def clean_flipkart_url(raw_url):
    if not raw_url:
        return raw_url
    # If Flipkart returned an embedded url like /dlhttp://m.flipkart.com/...
    idx = raw_url.find('http', 4)
    if idx != -1:
        raw_url = raw_url[idx:]
        
    parsed = urllib.parse.urlparse(raw_url)
    netloc = 'www.flipkart.com'
    path = parsed.path
    if path.startswith('/dl/'):
        path = path[3:]
    return urllib.parse.urlunparse(('https', netloc, path, parsed.params, parsed.query, ''))

def get_review_url(base_url, sort_order, page):
    cleaned_url = clean_flipkart_url(base_url)
    parsed = urllib.parse.urlparse(cleaned_url)
    
    new_path = parsed.path
    if '/product-reviews/' not in new_path:
        new_path = re.sub(r'/p/(itm[a-zA-Z0-9]+)', r'/product-reviews/\1', new_path)
        if '/product-reviews/' not in new_path and '/p/' in new_path:
            new_path = new_path.replace('/p/', '/product-reviews/')
        
    query_params = urllib.parse.parse_qs(parsed.query)
    pid = query_params.get('pid', [''])[0]
    
    new_params = {'sortOrder': sort_order, 'page': str(page)}
    if pid:
        new_params['pid'] = pid
        
    new_query = urllib.parse.urlencode(new_params)
    return urllib.parse.urlunparse(('https', 'www.flipkart.com', new_path, parsed.params, new_query, ''))

import json

def extract_reviews_from_html(html):
    reviews = []
    
    # Try finding __INITIAL_STATE__ with DOTALL to support multiline JSON
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});(?:</script>|\n)', html, re.DOTALL)
    if not match:
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)
    if not match:
        return []
        
    try:
        data = json.loads(match.group(1))
        
        def find_reviews(d):
            found = []
            if isinstance(d, dict):
                if d.get("type") == "ProductReviewValue" and ("rating" in d or "text" in d):
                    author = d.get('author', 'Unknown')
                    created = d.get('created', '')
                    certified = d.get('certifiedBuyer', False)
                    loc_dict = d.get('location', {})
                    if loc_dict and isinstance(loc_dict, dict):
                        city = loc_dict.get('city', '')
                        state = loc_dict.get('state', '')
                        location = f"{city}, {state}".strip(", ")
                    else:
                        location = ""
                        
                    review_url_path = d.get('url', '')
                    full_review_url = f"https://www.flipkart.com{review_url_path}" if review_url_path else ""
                    
                    rating_val = str(d.get('rating', ''))
                    title_val = d.get('title', '')
                    body_val = d.get('text', '')
                        
                    if rating_val or body_val:
                        found.append({
                            'rating': rating_val,
                            'title': title_val,
                            'body': body_val,
                            'author': author,
                            'created': created,
                            'certified': certified,
                            'location': location,
                            'url': full_review_url
                        })
                for k, v in d.items():
                    found.extend(find_reviews(v))
            elif isinstance(d, list):
                for item in d:
                    found.extend(find_reviews(item))
            return found
            
        reviews = find_reviews(data)
        
    except Exception as e:
        print(f"Error parsing JSON: {e}")
            
    # Remove duplicates if any
    unique_reviews = []
    seen = set()
    for r in reviews:
        k = r['rating'] + r['title'] + r['body'][:50]
        if k not in seen:
            seen.add(k)
            unique_reviews.append(r)
            
    return unique_reviews

def resolve_url(url):
    if not url:
        return url
        
    # Extract url if wrapped inside share text
    match = re.search(r'https?://[^\s]+', url)
    if match:
        url = match.group(0)
        
    # If not a short link or deep link, just clean and return
    if 'dl.flipkart.com' not in url and 'fktr.in' not in url:
        return clean_flipkart_url(url)
        
    # 1. Try checking 301/302 Location header without following redirect (avoids /dlhttp:// 404 issue)
    try:
        resp = requests.get(url, impersonate="chrome119", allow_redirects=False, timeout=10)
        loc = resp.headers.get('Location') or resp.headers.get('location')
        if loc:
            cleaned = clean_flipkart_url(loc)
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                return cleaned
    except Exception as e:
        print(f"Error resolving short link (no-redirect): {e}")

    # 2. Try following redirect with curl_cffi
    try:
        resp = requests.get(url, impersonate="chrome119", allow_redirects=True, timeout=10)
        if resp.url:
            cleaned = clean_flipkart_url(resp.url)
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                return cleaned
    except Exception as e:
        print(f"Error resolving short link (with-redirect): {e}")

    # 3. Fallback: urllib.request (standard library)
    try:
        import urllib.request as std_urllib
        class NoRedirectHandler(std_urllib.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = std_urllib.build_opener(NoRedirectHandler)
        req = std_urllib.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        try:
            opener.open(req, timeout=10)
        except std_urllib.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get('Location')
                if loc:
                    return clean_flipkart_url(loc)
    except Exception as e:
        print(f"Error in urllib fallback: {e}")

    return clean_flipkart_url(url)

def fetch_reviews(product_url, sort_order='MOST_RECENT', total_required=10):
    all_reviews = []
    page = 1
    sort_map = {'recent': 'MOST_RECENT', 'positive': 'POSITIVE_FIRST', 'negative': 'NEGATIVE_FIRST', 'helpful': 'MOST_HELPFUL'}
    flipkart_sort = sort_map.get(sort_order, 'MOST_RECENT')
    
    # Resolve short links before fetching
    product_url = resolve_url(product_url)
    
    while len(all_reviews) < total_required:
        url = get_review_url(product_url, flipkart_sort, page)
        try:
            response = requests.get(url, impersonate="chrome119", timeout=10)
        except Exception as e:
            print(f"Scraper request error: {e}")
            break
            
        if response.status_code != 200:
            print(f"Scraper returned status {response.status_code} for {url}")
            break
            
        page_reviews = extract_reviews_from_html(response.text)
        if not page_reviews:
            break
            
        all_reviews.extend(page_reviews)
        page += 1
        
        if page > (total_required // 10) + 2:
            break
            
    return all_reviews[:total_required]

import concurrent.futures

def fetch_reviews_by_name(product_url, target_name, max_pages=500):
    all_matched = []
    target_name_normalized = " ".join(target_name.lower().split())
    
    product_url = resolve_url(product_url)
    
    def fetch_page(page):
        url = get_review_url(product_url, 'MOST_RECENT', page)
        try:
            response = requests.get(url, impersonate="chrome119", timeout=10)
            if response.status_code == 200:
                page_reviews = extract_reviews_from_html(response.text)
                return page_reviews
        except Exception:
            pass
        return []

    # Fetch pages concurrently in chunks of 50
    chunk_size = 50
    for chunk_start in range(1, max_pages + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size, max_pages + 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_page = {executor.submit(fetch_page, page): page for page in range(chunk_start, chunk_end)}
            for future in concurrent.futures.as_completed(future_to_page):
                page_reviews = future.result()
                for r in page_reviews:
                    author_normalized = " ".join(r.get('author', '').lower().split())
                    if target_name_normalized in author_normalized:
                        all_matched.append(r)
        
        # If found in this chunk, return early
        if all_matched:
            break
            
    return all_matched


