import re
import urllib.parse
from curl_cffi import requests
from bs4 import BeautifulSoup

def get_review_url(base_url, sort_order, page):
    parsed = urllib.parse.urlparse(base_url)
    
    new_path = parsed.path
    if '/p/' in new_path:
        new_path = new_path.replace('/p/', '/product-reviews/')
        
    query_params = urllib.parse.parse_qs(parsed.query)
    pid = query_params.get('pid', [''])[0]
    
    new_params = {'sortOrder': sort_order, 'page': str(page)}
    if pid:
        new_params['pid'] = pid
        
    new_query = urllib.parse.urlencode(new_params)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, new_query, parsed.fragment))

import json

def extract_reviews_from_html(html):
    reviews = []
    
    # Try finding __INITIAL_STATE__
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', html)
    if not match:
        return []
        
    try:
        data = json.loads(match.group(1))
        # Finding deeply nested reviews in Flipkart's INITIAL_STATE
        # It's an enormous object, we can just dump it to string and use regex to find Review objects
        # or recursively search the dict for "type": "ProductReviewValue"
        
        def find_reviews(d):
            found = []
            if isinstance(d, dict):
                if d.get("type") == "ProductReviewValue" and "rating" in d and "text" in d and "title" in d:
                    author = d.get('author', 'Unknown')
                    created = d.get('created', '')
                    certified = d.get('certifiedBuyer', False)
                    loc_dict = d.get('location', {})
                    if loc_dict:
                        city = loc_dict.get('city', '')
                        state = loc_dict.get('state', '')
                        location = f"{city}, {state}".strip(", ")
                    else:
                        location = ""
                        
                    review_url_path = d.get('url', '')
                    full_review_url = f"https://www.flipkart.com{review_url_path}" if review_url_path else ""
                        
                    found.append({
                        'rating': str(d.get('rating')),
                        'title': d.get('title', ''),
                        'body': d.get('text', ''),
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
    if 'dl.flipkart.com' not in url and 'fktr.in' not in url:
        return url
        
    try:
        # First try with curl_cffi
        response = requests.get(url, impersonate="chrome119", allow_redirects=True, timeout=10)
        if response.url and 'dl.flipkart.com' not in response.url and 'fktr.in' not in response.url:
            return response.url
    except Exception:
        pass
        
    # Fallback to standard requests with mobile user agent to get the redirect
    try:
        import requests as std_requests
        headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36'}
        resp = std_requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        if resp.url and 'dl.flipkart.com' not in resp.url and 'fktr.in' not in resp.url:
            return resp.url
    except Exception:
        pass

    # Last resort fallback: unshorten.me API
    try:
        import requests as std_requests
        unshorten_url = f"https://unshorten.me/s/{url}"
        unshortened = std_requests.get(unshorten_url, timeout=10).text.strip()
        if unshortened and unshortened.startswith('http'):
            return unshortened
    except Exception:
        pass
        
    return url

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
            break
            
        if response.status_code != 200:
            break
            
        page_reviews = extract_reviews_from_html(response.text)
        if not page_reviews:
            break
            
        all_reviews.extend(page_reviews)
        page += 1
        
        if page > (total_required // 10) + 2:
            break
            
    return all_reviews[:total_required]


