import os
import re
import json
import time
import logging
import urllib.parse
import urllib.request as std_urllib
import concurrent.futures
from curl_cffi import requests
from bs4 import BeautifulSoup
import pymongo

logger = logging.getLogger(__name__)

class FlipkartError(Exception):
    """Base exception for Flipkart scraper errors."""
    pass

class FlipkartBlockedError(FlipkartError):
    """Raised when Flipkart blocks requests."""
    pass

class FlipkartResolutionError(FlipkartError):
    """Raised when short links or deep links cannot be resolved."""
    pass

_SESSION = None
_MONGO_CLIENT = None

def log_diagnostic(event, payload=None):
    """Logs diagnostic info to MongoDB debug_logs collection for live server troubleshooting."""
    global _MONGO_CLIENT
    try:
        mongo_uri = os.getenv("MONGO_URI")
        if not mongo_uri:
            return
        if _MONGO_CLIENT is None:
            _MONGO_CLIENT = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        db = _MONGO_CLIENT["flipkart_bot"]
        doc = {
            "event": event,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": payload or {}
        }
        db["debug_logs"].insert_one(doc)
    except Exception as e:
        logger.warning(f"Could not write diagnostic log: {e}")

def get_session():
    """Returns a persistent curl_cffi Session with Chrome 120 TLS fingerprint and proxy support."""
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
        
        try:
            r = session.get('https://www.flipkart.com/', timeout=10)
            log_diagnostic("session_warmup", {
                "status": r.status_code, 
                "cookies": list(session.cookies.keys())
            })
        except Exception as e:
            logger.warning(f"Warmup homepage fetch failed: {e}")
            log_diagnostic("session_warmup_error", {"error": str(e)})
            
        _SESSION = session
    return _SESSION

def clean_flipkart_url(raw_url):
    if not raw_url:
        return raw_url
    idx = raw_url.find('http', 4)
    if idx != -1:
        raw_url = raw_url[idx:]
        
    if 'translate.goog' in raw_url:
        raw_url = re.sub(r'https?://[a-zA-Z0-9\-]+\.translate\.goog', 'https://www.flipkart.com', raw_url)
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        for k in list(qs.keys()):
            if k.startswith('_x_tr_'):
                del qs[k]
        raw_url = urllib.parse.urlunparse((parsed.scheme, 'www.flipkart.com', parsed.path, parsed.params, urllib.parse.urlencode(qs, doseq=True), ''))
        
    parsed = urllib.parse.urlparse(raw_url)
    netloc = 'www.flipkart.com'
    path = parsed.path
    if path.startswith('/dl/'):
        path = path[3:]
    return urllib.parse.urlunparse(('https', netloc, path, parsed.params, parsed.query, ''))

def to_google_mirror_url(flipkart_url):
    """Converts a Flipkart URL to a Google Translate Edge proxy URL.
    Bypasses cloud datacenter IP blocks (HTTP 529) permanently without paid proxies."""
    parsed = urllib.parse.urlparse(flipkart_url)
    domain = parsed.netloc.replace('.', '-')
    goog_netloc = f"{domain}.translate.goog"
    qs = urllib.parse.parse_qs(parsed.query)
    qs['_x_tr_sl'] = ['auto']
    qs['_x_tr_tl'] = ['en']
    qs['_x_tr_hl'] = ['en']
    new_query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(('https', goog_netloc, parsed.path, parsed.params, new_query, ''))

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
    if not html:
        return reviews
        
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
            rating_tags = soup.find_all(lambda tag: tag.name in ('div', 'span') and tag.string and re.match(r'^[1-5]\s*★?$', tag.string.strip()))
            for r_tag in rating_tags:
                r_val = r_tag.string.strip().replace('★', '').strip()
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

    # Remove duplicates
    unique_reviews = []
    seen = set()
    for r in reviews:
        k = r['rating'] + r['title'] + r['body'][:50]
        if k not in seen:
            seen.add(k)
            unique_reviews.append(r)
            
    return unique_reviews

def resolve_url(url):
    """Resolves short links (dl.flipkart.com/s/..., fktr.in/...) to full Flipkart product URLs."""
    if not url:
        return url
        
    match = re.search(r'https?://[^\s]+', url)
    if match:
        url = match.group(0)
        
    if 'dl.flipkart.com/s/' not in url and 'fktr.in' not in url and '/s/' not in urllib.parse.urlparse(url).path:
        cleaned = clean_flipkart_url(url)
        if '/p/' in cleaned or '/product-reviews/' in cleaned:
            return cleaned

    session = get_session()

    # 1. Check 301/302 Location header or JSON body without redirect
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
                logger.info(f"Resolved shortlink: {url} -> {cleaned}")
                log_diagnostic("resolved_shortlink", {"raw": url, "resolved": cleaned, "method": "location_or_json"})
                return cleaned
    except Exception as e:
        logger.warning(f"Error resolving short link (no-redirect): {e}")

    # 2. Check allow_redirects
    try:
        resp = session.get(url, allow_redirects=True, timeout=12)
        if resp.url:
            cleaned = clean_flipkart_url(resp.url)
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                logger.info(f"Resolved shortlink via redirects: {url} -> {cleaned}")
                log_diagnostic("resolved_shortlink", {"raw": url, "resolved": cleaned, "method": "redirects"})
                return cleaned
    except Exception as e:
        logger.warning(f"Error resolving short link (with-redirect): {e}")

    # 3. Fallback: Google Edge Proxy Mirror
    try:
        g_url = to_google_mirror_url(url)
        resp_g = session.get(g_url, timeout=12)
        if resp_g.url and ('/p/' in resp_g.url or '/product-reviews/' in resp_g.url):
            cleaned = clean_flipkart_url(resp_g.url)
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                log_diagnostic("resolved_shortlink", {"raw": url, "resolved": cleaned, "method": "google_edge_proxy"})
                return cleaned
        base_match = re.search(r'<base\s+href=[\'"](https?://[^\'"]+)[\'"]', resp_g.text)
        if base_match:
            cleaned = clean_flipkart_url(base_match.group(1))
            if '/p/' in cleaned or '/product-reviews/' in cleaned:
                log_diagnostic("resolved_shortlink", {"raw": url, "resolved": cleaned, "method": "google_edge_base"})
                return cleaned
    except Exception as e:
        logger.warning(f"Error resolving short link via Google Edge Proxy: {e}")

    # 4. Fallback: urllib.request
    try:
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
                        log_diagnostic("resolved_shortlink", {"raw": url, "resolved": cleaned, "method": "urllib"})
                        return cleaned
    except Exception as e:
        logger.warning(f"Error in urllib fallback: {e}")

    cleaned_fallback = clean_flipkart_url(url)
    if '/p/' not in cleaned_fallback and '/product-reviews/' not in cleaned_fallback:
        log_diagnostic("resolution_failed", {"raw": url, "cleaned": cleaned_fallback})
        raise FlipkartResolutionError(f"Could not resolve shortlink '{url}' to a valid product page.")
    return cleaned_fallback

_DIRECT_BLOCKED_UNTIL = 0

def fetch_page_html(url, session=None):
    """Fetches HTML with direct curl_cffi and automatic Google Translate Edge Proxy bypass for Cloud Datacenter 529 blocks."""
    global _DIRECT_BLOCKED_UNTIL
    s = session or get_session()
    now = time.time()
    
    # 1. Attempt Direct with curl_cffi (if not currently cached as blocked)
    if now > _DIRECT_BLOCKED_UNTIL:
        headers = {
            'Referer': 'https://www.flipkart.com/',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Dest': 'document'
        }
        try:
            response = s.get(url, headers=headers, timeout=8)
            log_diagnostic("attempt_direct_curl", {
                "url": url, 
                "status": response.status_code, 
                "final_url": response.url, 
                "html_len": len(response.text)
            })
            
            # If Flipkart returns 200 and stays on product/review page
            if response.status_code == 200 and ('/product-reviews/' in response.url or '/p/' in response.url):
                if '__INITIAL_STATE__' in response.text:
                    return response.text
                    
            if response.status_code in (529, 403, 429):
                logger.warning(f"Direct request received status {response.status_code}. Caching direct block for 15 minutes and using Google Edge Proxy.")
                _DIRECT_BLOCKED_UNTIL = now + 900
            else:
                logger.warning(f"Direct request received status {response.status_code} (URL: {response.url}). Switching to Google Edge Proxy...")
        except Exception as e:
            logger.warning(f"Direct curl request failed: {e}. Switching to Google Edge Proxy...")
            log_diagnostic("direct_curl_exception", {"url": url, "error": str(e)})

    # 2. Google Translate Edge Proxy Bypass (Always works worldwide, bypasses Akamai 529 completely)
    try:
        g_url = to_google_mirror_url(url)
        resp_g = s.get(g_url, timeout=15)
        log_diagnostic("google_edge_proxy_attempt", {
            "url": url,
            "g_url": g_url,
            "status": resp_g.status_code,
            "html_len": len(resp_g.text)
        })
        if resp_g.status_code == 200 and len(resp_g.text) > 1000:
            return resp_g.text
        else:
            log_diagnostic("google_edge_proxy_status_error", {"url": url, "status": resp_g.status_code})
    except Exception as e:
        logger.error(f"Google Edge Proxy bypass failed: {e}")
        log_diagnostic("google_edge_proxy_error", {"url": url, "error": str(e)})

    return ""

def fetch_reviews(product_url, sort_order='MOST_RECENT', total_required=10):
    all_reviews = []
    page = 1
    sort_map = {'recent': 'MOST_RECENT', 'positive': 'POSITIVE_FIRST', 'negative': 'NEGATIVE_FIRST', 'helpful': 'MOST_HELPFUL'}
    flipkart_sort = sort_map.get(sort_order, 'MOST_RECENT')
    
    product_url = resolve_url(product_url)
    session = get_session()
    
    while len(all_reviews) < total_required:
        url = get_review_url(product_url, flipkart_sort, page)
        html_content = fetch_page_html(url, session=session)
        page_reviews = extract_reviews_from_html(html_content) if html_content else []

        if not page_reviews:
            if page == 1:
                log_diagnostic("zero_reviews_found", {"product_url": product_url, "url": url})
            break
            
        all_reviews.extend(page_reviews)
        page += 1
        
        if page > (total_required // 10) + 2:
            break
            
    log_diagnostic("fetch_reviews_completed", {"product_url": product_url, "total_found": len(all_reviews)})
    return all_reviews[:total_required]

def fetch_reviews_by_name(product_url, target_name, max_pages=500):
    all_matched = []
    target_name_normalized = " ".join(target_name.lower().split())
    
    product_url = resolve_url(product_url)
    session = get_session()
    
    def fetch_page(page):
        url = get_review_url(product_url, 'MOST_RECENT', page)
        html_content = fetch_page_html(url, session=session)
        if html_content:
            return extract_reviews_from_html(html_content)
        return []

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
                except Exception:
                    pass
        
        if all_matched:
            break
            
    return all_matched
