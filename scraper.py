import os
import re
import json
import logging
import urllib.parse
import concurrent.futures
from curl_cffi import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class FlipkartError(Exception):
    """Base exception for Flipkart scraper errors."""
    pass

class FlipkartBlockedError(FlipkartError):
    """Raised when Flipkart blocks requests (HTTP 403 / 429 / Akamai Bot Protection)."""
    pass

class FlipkartResolutionError(FlipkartError):
    """Raised when short links or deep links cannot be resolved."""
    pass

_SESSION = None

def get_session():
    """Returns a persistent curl_cffi Session with Chrome 120 TLS fingerprint, 
    proxy support, and warm Akamai Bot Manager cookies."""
    global _SESSION
    if _SESSION is None:
        proxy = os.getenv("PROXY_URL") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
        proxies = {"http": proxy, "https": proxy} if proxy else None
        
        session = requests.Session(impersonate="chrome120")
        if proxies:
            session.proxies = proxies
            masked_proxy = proxy.split('@')[-1] if '@' in proxy else proxy
            logger.info(f"Scraper configured with proxy: {masked_proxy}")
            
        session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-IN,en;q=0.9,hi;q=0.8',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1'
        })
        
        # Warmup session on Flipkart homepage to acquire ak_bmsc and session tokens
        try:
            r = session.get('https://www.flipkart.com/', timeout=12)
            if r.status_code == 200:
                logger.info(f"Warmup successful. Acquired {len(session.cookies)} Flipkart cookies.")
        except Exception as e:
            logger.warning(f"Warmup homepage fetch failed (continuing anyway): {e}")
            
        _SESSION = session
    return _SESSION

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

def extract_reviews_from_html(html):
    reviews = []
    
    # 1. Try finding __INITIAL_STATE__ with DOTALL to support multiline JSON
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});(?:</script>|\n)', html, re.DOTALL)
    if not match:
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)
        
    if match:
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
            logger.warning(f"Error parsing __INITIAL_STATE__ JSON: {e}")

    # 2. Fallback: Parse DOM elements if __INITIAL_STATE__ is absent
    if not reviews and html:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            # Look for rating tags (e.g. '5★', '4★')
            rating_tags = soup.find_all(lambda tag: tag.name in ('div', 'span') and tag.string and re.match(r'^[1-5]\s*★?$', tag.string.strip()))
            for r_tag in rating_tags:
                r_val = r_tag.string.strip().replace('★', '').strip()
                # Find review container
                curr = r_tag
                container = None
                for _ in range(5):
                    if curr.parent:
                        curr = curr.parent
                        t = curr.get_text()
                        if ('Flipkart Customer' in t or 'Buyer' in t or 'ago' in t or 'Today' in t) and len(t) > 30:
                            container = curr
                            break
                if container:
                    full_text = container.get_text(separator=' | ')
                    reviews.append({
                        'rating': r_val,
                        'title': '',
                        'body': full_text[:200],
                        'author': 'Flipkart Customer',
                        'created': '',
                        'certified': True,
                        'location': '',
                        'url': ''
                    })
        except Exception as e:
            logger.warning(f"DOM fallback parse error: {e}")

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
    """Resolves short links (dl.flipkart.com/s/..., fktr.in/...) and deep links to full Flipkart product URLs."""
    if not url:
        return url
        
    # Extract url if wrapped inside share text
    match = re.search(r'https?://[^\s]+', url)
    if match:
        url = match.group(0)
        
    # If already a full product or review url, clean and return
    if 'dl.flipkart.com/s/' not in url and 'fktr.in' not in url and '/s/' not in urllib.parse.urlparse(url).path:
        cleaned = clean_flipkart_url(url)
        if '/p/' in cleaned or '/product-reviews/' in cleaned:
            return cleaned

    session = get_session()

    # 1. Try checking 301/302 Location header or JSON body without following redirect
    try:
        resp = session.get(url, allow_redirects=False, timeout=12)
        loc = resp.headers.get('Location') or resp.headers.get('location')
        if not loc:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    loc = data.get('RESPONSE', {}).get('redirectUrl')
            except Exception:
                pass
        if not loc and resp.text:
            body_match = re.search(r'\"redirectUrl\"\s*:\s*\"([^\"]+)\"', resp.text)
            if body_match:
                loc = body_match.group(1)
                
        if loc:
            cleaned = clean_flipkart_url(loc)
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                logger.info(f"Resolved shortlink via Location/JSON: {url} -> {cleaned}")
                return cleaned
    except Exception as e:
        logger.warning(f"Error resolving short link (no-redirect): {e}")

    # 2. Try following redirect with session
    try:
        resp = session.get(url, allow_redirects=True, timeout=12)
        if resp.url:
            cleaned = clean_flipkart_url(resp.url)
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                logger.info(f"Resolved shortlink via redirects: {url} -> {cleaned}")
                return cleaned
    except Exception as e:
        logger.warning(f"Error resolving short link (with-redirect): {e}")

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
                    cleaned = clean_flipkart_url(loc)
                    if '/p/' in cleaned or '/product-reviews/' in cleaned:
                        logger.info(f"Resolved shortlink via urllib: {url} -> {cleaned}")
                        return cleaned
    except Exception as e:
        logger.warning(f"Error in urllib fallback: {e}")

    cleaned_fallback = clean_flipkart_url(url)
    if '/p/' not in cleaned_fallback and '/product-reviews/' not in cleaned_fallback:
        raise FlipkartResolutionError(f"Could not resolve shortlink '{url}' to a valid product page.")
    return cleaned_fallback

def fetch_reviews(product_url, sort_order='MOST_RECENT', total_required=10):
    all_reviews = []
    page = 1
    sort_map = {'recent': 'MOST_RECENT', 'positive': 'POSITIVE_FIRST', 'negative': 'NEGATIVE_FIRST', 'helpful': 'MOST_HELPFUL'}
    flipkart_sort = sort_map.get(sort_order, 'MOST_RECENT')
    
    # Resolve short links before fetching
    product_url = resolve_url(product_url)
    
    session = get_session()
    headers = {
        'Referer': 'https://www.flipkart.com/',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Dest': 'document'
    }
    
    while len(all_reviews) < total_required:
        url = get_review_url(product_url, flipkart_sort, page)
        try:
            response = session.get(url, headers=headers, timeout=15)
        except Exception as e:
            logger.error(f"Scraper request error for {url}: {e}")
            raise FlipkartError(f"Connection error to Flipkart: {e}")
            
        if response.status_code in (403, 429):
            logger.error(f"Scraper blocked with HTTP {response.status_code} for {url}")
            raise FlipkartBlockedError(f"Flipkart blocked request with HTTP {response.status_code} (Cloud IP Block).")
            
        if response.status_code != 200:
            logger.warning(f"Scraper returned status {response.status_code} for {url}")
            break
            
        page_reviews = extract_reviews_from_html(response.text)
        if not page_reviews:
            break
            
        all_reviews.extend(page_reviews)
        page += 1
        
        if page > (total_required // 10) + 2:
            break
            
    return all_reviews[:total_required]

def fetch_reviews_by_name(product_url, target_name, max_pages=500):
    all_matched = []
    target_name_normalized = " ".join(target_name.lower().split())
    
    product_url = resolve_url(product_url)
    session = get_session()
    headers = {
        'Referer': 'https://www.flipkart.com/',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Dest': 'document'
    }
    
    def fetch_page(page):
        url = get_review_url(product_url, 'MOST_RECENT', page)
        try:
            response = session.get(url, headers=headers, timeout=15)
            if response.status_code in (403, 429):
                logger.error(f"Blocked with HTTP {response.status_code} on page {page}")
                raise FlipkartBlockedError(f"Flipkart blocked request (HTTP {response.status_code})")
            if response.status_code == 200:
                page_reviews = extract_reviews_from_html(response.text)
                return page_reviews
        except FlipkartBlockedError:
            raise
        except Exception:
            pass
        return []

    # Fetch pages concurrently in chunks of 25 to avoid overwhelming network
    chunk_size = 25
    for chunk_start in range(1, max_pages + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size, max_pages + 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_page = {executor.submit(fetch_page, page): page for page in range(chunk_start, chunk_end)}
            for future in concurrent.futures.as_completed(future_to_page):
                try:
                    page_reviews = future.result()
                    for r in page_reviews:
                        author_normalized = " ".join(r.get('author', '').lower().split())
                        if target_name_normalized in author_normalized:
                            all_matched.append(r)
                except FlipkartBlockedError:
                    raise
                except Exception:
                    pass
        
        # If found in this chunk, return early
        if all_matched:
            break
            
    return all_matched
