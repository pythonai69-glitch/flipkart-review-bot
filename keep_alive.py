import threading
import os
import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from scraper import fetch_reviews, resolve_url

START_TIME = time.time()

def get_user_count():
    count = 0
    if os.path.exists('users.txt'):
        try:
            with open('users.txt', 'r') as f:
                count = len([line.strip() for line in f if line.strip()])
        except Exception:
            pass
    return count

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flipkart Review Bot - Local Server</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0f19;
            --card-bg: rgba(22, 29, 47, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --primary: #2874f0;
            --accent: #fb641b;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --success: #10b981;
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', sans-serif;
        }
        body {
            background-color: var(--bg);
            background-image: 
                radial-gradient(at 0% 0%, rgba(40, 116, 240, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(251, 100, 27, 0.1) 0px, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            padding: 30px 20px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--card-border);
            flex-wrap: wrap;
            gap: 15px;
        }
        .logo-wrap {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .logo-badge {
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, #2874f0, #174ea6);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            box-shadow: 0 8px 16px rgba(40, 116, 240, 0.3);
        }
        h1 {
            font-size: 22px;
            font-weight: 800;
            letter-spacing: -0.5px;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.12);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
            padding: 6px 14px;
            border-radius: 30px;
            font-size: 13px;
            font-weight: 600;
        }
        .pulse-dot {
            width: 8px;
            height: 8px;
            background-color: #34d399;
            border-radius: 50%;
            box-shadow: 0 0 10px #34d399;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.8; }
            50% { transform: scale(1.3); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.8; }
        }
        .grid-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 25px;
        }
        .stat-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 20px;
            border-radius: 16px;
            backdrop-filter: blur(12px);
        }
        .stat-label {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-sub);
            margin-bottom: 6px;
        }
        .stat-val {
            font-size: 22px;
            font-weight: 700;
            color: #fff;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 24px;
            backdrop-filter: blur(16px);
            margin-bottom: 25px;
        }
        .card-title {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .input-group {
            margin-bottom: 16px;
        }
        .input-label {
            display: block;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 6px;
            color: var(--text-sub);
        }
        input, select {
            width: 100%;
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: #fff;
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 14px;
            outline: none;
            transition: all 0.2s;
        }
        input:focus, select:focus {
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(40, 116, 240, 0.2);
        }
        .form-row {
            display: flex;
            gap: 12px;
        }
        .btn-submit {
            background: linear-gradient(135deg, var(--primary), #1a56db);
            color: #fff;
            border: none;
            padding: 14px 24px;
            border-radius: 12px;
            font-weight: 700;
            font-size: 15px;
            cursor: pointer;
            width: 100%;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        .btn-submit:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 20px rgba(40, 116, 240, 0.4);
        }
        .btn-submit:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        #reviews-output {
            margin-top: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .review-item {
            background: rgba(15, 23, 42, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 14px;
            padding: 16px;
            transition: all 0.2s;
        }
        .review-item:hover {
            border-color: rgba(40, 116, 240, 0.4);
        }
        .review-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .rating-chip {
            background: #388e3c;
            color: #fff;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .review-title {
            font-weight: 700;
            font-size: 15px;
            margin-bottom: 6px;
        }
        .review-body {
            font-size: 13px;
            color: #cbd5e1;
            line-height: 1.5;
            margin-bottom: 10px;
        }
        .review-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            color: var(--text-sub);
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding-top: 8px;
        }
        .review-link {
            color: #60a5fa;
            text-decoration: none;
            font-weight: 600;
        }
        .review-link:hover {
            text-decoration: underline;
        }
        .empty-hint {
            text-align: center;
            color: var(--text-sub);
            padding: 30px;
            font-size: 14px;
        }
        .telegram-btn {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(36, 161, 222, 0.15);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.3);
            padding: 8px 16px;
            border-radius: 10px;
            text-decoration: none;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s;
        }
        .telegram-btn:hover {
            background: rgba(36, 161, 222, 0.25);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-wrap">
                <div class="logo-badge">🛍️</div>
                <div>
                    <h1>Flipkart Review Bot</h1>
                    <p style="font-size: 13px; color: var(--text-sub);">Local Server & REST API</p>
                </div>
            </div>
            <div style="display: flex; gap: 10px; align-items: center;">
                <a href="https://t.me/Fkreviewlink_bot" target="_blank" class="telegram-btn">
                    ✈️ Open @Fkreviewlink_bot
                </a>
                <div class="status-badge">
                    <span class="pulse-dot"></span>
                    <span>ONLINE (Port 8080)</span>
                </div>
            </div>
        </header>

        <div class="grid-stats">
            <div class="stat-card">
                <div class="stat-label">Bot Polling Status</div>
                <div class="stat-val" style="color: #34d399;">Active 🟢</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Users</div>
                <div class="stat-val" id="users-count">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Server Uptime</div>
                <div class="stat-val" id="uptime-val">--</div>
            </div>
        </div>

        <div class="card">
            <div class="card-title">⚡ Live Scraper Test Tool</div>
            <p style="font-size: 13px; color: var(--text-sub); margin-bottom: 16px;">
                Enter any Flipkart product link, mobile app share text, or short link (e.g. <code>dl.flipkart.com/s/...</code>) to extract reviews instantly.
            </p>
            <div class="input-group">
                <label class="input-label">Flipkart Link or Share Message:</label>
                <input type="text" id="product-url" placeholder="Paste link or share text here (e.g. https://dl.flipkart.com/s/...)" value="https://dl.flipkart.com/s/SCkCOAuuuN">
            </div>
            <div class="form-row">
                <div class="input-group" style="flex: 1;">
                    <label class="input-label">Sort Order:</label>
                    <select id="sort-order">
                        <option value="recent">Most Recent</option>
                        <option value="positive">Positive First</option>
                        <option value="negative">Negative First</option>
                        <option value="helpful">Most Helpful</option>
                    </select>
                </div>
                <div class="input-group" style="flex: 1;">
                    <label class="input-label">Count:</label>
                    <select id="review-count">
                        <option value="5">5 Reviews</option>
                        <option value="10" selected>10 Reviews</option>
                        <option value="20">20 Reviews</option>
                    </select>
                </div>
            </div>
            <button class="btn-submit" id="btn-scrape" onclick="runScraper()">
                <span>🚀 Extract Reviews</span>
            </button>

            <div id="reviews-output">
                <div class="empty-hint">Click "Extract Reviews" to test the live scraper directly.</div>
            </div>
        </div>
    </div>

    <script>
        async function updateStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                document.getElementById('users-count').innerText = data.users;
                document.getElementById('uptime-val').innerText = data.uptime_str;
            } catch(e) {}
        }
        setInterval(updateStatus, 5000);
        updateStatus();

        async function runScraper() {
            const urlInput = document.getElementById('product-url').value.trim();
            const sort = document.getElementById('sort-order').value;
            const count = document.getElementById('review-count').value;
            const btn = document.getElementById('btn-scrape');
            const output = document.getElementById('reviews-output');

            if (!urlInput) {
                alert('Please enter a Flipkart link.');
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '<span>⏳ Extracting Reviews... Please wait</span>';
            output.innerHTML = '<div class="empty-hint">Fetching and extracting reviews from Flipkart...</div>';

            try {
                const apiUrl = `/api/scrape?url=${encodeURIComponent(urlInput)}&sort=${sort}&count=${count}`;
                const res = await fetch(apiUrl);
                const data = await res.json();

                if (!data.success || !data.reviews || data.reviews.length === 0) {
                    output.innerHTML = `<div class="empty-hint" style="color: #f87171;">❌ ${data.message || 'No reviews found for this link.'}</div>`;
                    return;
                }

                let html = `<div style="font-weight: 700; margin-bottom: 8px; color: #34d399;">✅ Found ${data.reviews.length} Review(s):</div>`;
                data.reviews.forEach((r, idx) => {
                    const stars = '★'.repeat(parseInt(r.rating) || 5);
                    html += `
                        <div class="review-item">
                            <div class="review-header">
                                <span class="rating-chip">${r.rating} ★</span>
                                <span style="font-size: 12px; color: var(--text-sub);">${r.created || ''}</span>
                            </div>
                            <div class="review-title">${idx + 1}. ${r.title || 'Review'}</div>
                            <div class="review-body">${r.body || ''}</div>
                            <div class="review-footer">
                                <span>👤 <b>${r.author || 'Flipkart Customer'}</b> ${r.certified ? '• <span style="color: #34d399;">Certified Buyer</span>' : ''} ${r.location ? `• ${r.location}` : ''}</span>
                                ${r.url ? `<a href="${r.url}" target="_blank" class="review-link">🔗 View Review</a>` : ''}
                            </div>
                        </div>
                    `;
                });
                output.innerHTML = html;
            } catch(e) {
                output.innerHTML = `<div class="empty-hint" style="color: #f87171;">Error: ${e.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>🚀 Extract Reviews</span>';
            }
        }
    </script>
</body>
</html>
"""

class RequestHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/api/status':
            uptime_sec = int(time.time() - START_TIME)
            hours = uptime_sec // 3600
            mins = (uptime_sec % 3600) // 60
            secs = uptime_sec % 60
            uptime_str = f"{hours}h {mins}m {secs}s" if hours > 0 else f"{mins}m {secs}s"

            data = {
                "status": "online",
                "uptime_seconds": uptime_sec,
                "uptime_str": uptime_str,
                "users": get_user_count()
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return

        if path == '/api/scrape':
            query = urllib.parse.parse_qs(parsed.query)
            target_url = query.get('url', [''])[0]
            sort_order = query.get('sort', ['recent'])[0]
            try:
                count = int(query.get('count', ['10'])[0])
            except ValueError:
                count = 10

            if not target_url:
                resp = {"success": False, "message": "Missing 'url' parameter"}
            else:
                try:
                    resolved = resolve_url(target_url)
                    reviews = fetch_reviews(resolved, sort_order=sort_order, total_required=count)
                    resp = {
                        "success": True,
                        "resolved_url": resolved,
                        "count": len(reviews),
                        "reviews": reviews
                    }
                except Exception as e:
                    resp = {"success": False, "message": str(e)}

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        # Serve Dashboard
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode('utf-8'))

    def log_message(self, format, *args):
        # Mute http access logs to keep terminal output clean
        pass

def run():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), RequestHandler)
    server.serve_forever()

def keep_alive():
    t = threading.Thread(target=run)
    t.daemon = True
    t.start()
