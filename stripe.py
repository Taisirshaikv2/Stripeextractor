import sys
import subprocess

# ==========================================
# 1. AUTO-INSTALLER (Detects missing libs)
# ==========================================
REQUIRED_PACKAGES = [
    'flask', 'selenium', 'webdriver-manager', 'urllib3'
]

def install_packages():
    """Automatically install missing packages"""
    packages_to_install = []
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package.split('-')[0].split('_')[0]) # Handle name differences like 'webdriver-manager'
        except ImportError:
            packages_to_install.append(package)
    
    if packages_to_install:
        print(f"[Installer] Missing libraries detected: {packages_to_install}")
        print(f"[Installer] Installing now...")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + packages_to_install)
        print("[Installer] Installation complete.")
    else:
        print("[Installer] All dependencies are already installed.")

install_packages()

# ==========================================
# 2. IMPORTS & APP SETUP
# ==========================================
from flask import Flask, request, jsonify, render_template_string
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException
import re
import time
import os
import urllib.parse
import traceback

app = Flask(__name__)

# ==========================================
# 3. HTML TEMPLATE (Frontend Tester)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stripe Key Extractor</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #e2e8f0;
            --accent-color: #3b82f6;
            --success-color: #22c55e;
            --error-color: #ef4444;
            --border-color: #334155;
        }
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            background-color: var(--card-bg);
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            width: 100%;
            max-width: 600px;
            padding: 30px;
            border: 1px solid var(--border-color);
        }
        h1 { margin-top: 0; text-align: center; color: var(--accent-color); }
        .input-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 600; }
        input[type="text"] {
            width: 100%;
            padding: 12px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            background-color: var(--bg-color);
            color: white;
            font-size: 14px;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 12px;
            background-color: var(--accent-color);
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
            transition: background 0.2s;
        }
        button:hover { background-color: #2563eb; }
        button:disabled { background-color: var(--border-color); cursor: not-allowed; }
        #result {
            margin-top: 25px;
            padding: 15px;
            border-radius: 6px;
            background-color: rgba(0,0,0,0.2);
            border: 1px solid var(--border-color);
            display: none;
            white-space: pre-wrap;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            max-height: 300px;
            overflow-y: auto;
        }
        .status-success { color: var(--success-color); font-weight: bold; }
        .status-error { color: var(--error-color); font-weight: bold; }
        .loading { text-align: center; color: #94a3b8; display: none; }
    </style>
</head>
<body>

    <div class="container">
        <h1>Stripe Extractor</h1>
        <div class="input-group">
            <label for="checkoutLink">Enter Checkout Link</label>
            <input type="text" id="checkoutLink" placeholder="https://checkout.stripe.com/..." autocomplete="off">
        </div>
        <button id="extractBtn" onclick="extractKeys()">Extract Keys</button>
        <div id="loading" class="loading">Processing... This may take up to 30s.</div>
        <div id="result"></div>
    </div>

    <script>
        async function extractKeys() {
            const urlInput = document.getElementById('checkoutLink').value.trim();
            const btn = document.getElementById('extractBtn');
            const resultDiv = document.getElementById('result');
            const loadingDiv = document.getElementById('loading');

            if (!urlInput) {
                alert("Please enter a URL");
                return;
            }

            // Reset UI
            btn.disabled = true;
            resultDiv.style.display = 'none';
            loadingDiv.style.display = 'block';

            try {
                // Call the internal API
                const response = await fetch('/extract?checkout=' + encodeURIComponent(urlInput));
                const data = await response.json();

                loadingDiv.style.display = 'none';
                resultDiv.style.display = 'block';

                // Format Output
                let output = '';
                if (data.status === 'success') {
                    output += `<div class='status-success'>SUCCESS</div><br>`;
                    output += `<strong>PK_LIVE:</strong> ${data.pk_live || 'Not Found'}<br><br>`;
                    output += `<strong>CS_LIVE:</strong> ${data.cs_live || 'Not Found'}`;
                } else {
                    output += `<div class='status-error'>ERROR (${data.status_code || 'Unknown'})</div><br>`;
                    output += `<strong>Message:</strong> ${data.message}`;
                    if (data.sample_content) {
                        output += `<br><br><strong>Sample HTML:</strong> ${data.sample_content.substring(0, 200)}...`;
                    }
                }
                resultDiv.innerHTML = output;

            } catch (err) {
                loadingDiv.style.display = 'none';
                resultDiv.style.display = 'block';
                resultDiv.innerHTML = `<div class='status-error'>Connection Error</div><br>${err.message}`;
            } finally {
                btn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""

# ==========================================
# 4. LOGIC & ROUTES
# ==========================================

def setup_chrome_driver():
    """Setup Chrome driver for cloud environments (Render/Railway)"""
    chrome_options = Options()
    
    # Standard Cloud/Headless settings
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Anti-Detection
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-notifications")
    
    # Stability optimizations
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    return chrome_options

def extract_keys_from_html(html_content):
    """Enhanced extraction with multiple regex patterns"""
    try:
        pk_live_key = None
        cs_live_key = None
        
        # --- Expanded PK_LIVE Patterns ---
        # 1. Standard apiKey param
        # 2. JSON keys (quoted)
        # 3. Mixed content variable assignments
        pk_patterns = [
            r'apiKey\s*[:=]\s*["\']?(pk_live_[a-zA-Z0-9_]+)["\']?',
            r'"key"\s*:\s*"(pk_live_[a-zA-Z0-9_]+)"',
            r'pk_live_[a-zA-Z0-9_]{30,}', # Live keys are usually long
            r'data-key\s*=\s*["\']?(pk_live_[a-zA-Z0-9_]+)',
            r'publishableKey\s*[:=]\s*["\']?(pk_live_[a-zA-Z0-9_]+)'
        ]
        
        # --- Expanded CS_LIVE Patterns ---
        # 1. Session ID in data attributes
        # 2. JSON fields
        # 3. URL parameters
        cs_patterns = [
            r'data-session-id\s*=\s*["\']?(cs_live_[a-zA-Z0-9]+)["\']?',
            r'"sessionId"\s*:\s*"(cs_live_[a-zA-Z0-9]+)"',
            r'session_id["\']?\s*:\s*["\']?(cs_live_[a-zA-Z0-9]+)',
            r'/pay/(cs_live_[a-zA-Z0-9]+)', # URL path based
            r'cs_live_[a-zA-Z0-9]{30,}' # Live keys are usually long
        ]
        
        # Search PK
        for pattern in pk_patterns:
            match = re.search(pattern, html_content)
            if match:
                # Extract just the key part
                clean_match = re.search(r'pk_live_[a-zA-Z0-9_]+', match.group(0))
                if clean_match:
                    pk_live_key = clean_match.group(0)
                    break
        
        # Search CS
        for pattern in cs_patterns:
            match = re.search(pattern, html_content)
            if match:
                clean_match = re.search(r'cs_live_[a-zA-Z0-9]+', match.group(0))
                if clean_match:
                    cs_live_key = clean_match.group(0)
                    break
        
        return pk_live_key, cs_live_key
        
    except Exception as e:
        print(f"[Extraction Error] {str(e)}")
        return None, None

@app.route('/', methods=['GET'])
def home():
    """Serve the HTML Tester UI"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/extract', methods=['GET'])
def extract_keys():
    """API Endpoint"""
    driver = None
    url_processed = ""
    
    try:
        # 1. Validate Input
        checkout_url = request.args.get('checkout', '').strip()
        if not checkout_url:
            return jsonify({
                "status": "error", "message": "Missing 'checkout' parameter",
                "pk_live": None, "cs_live": None, "status_code": 400
            }), 400
        
        # 2. Clean URL
        try:
            if '%' in checkout_url:
                checkout_url = urllib.parse.unquote(checkout_url)
            if not checkout_url.startswith(("http://", "https://")):
                checkout_url = "https://" + checkout_url
        except Exception as e:
             return jsonify({
                "status": "error", "message": f"Invalid URL format: {str(e)}",
                "pk_live": None, "cs_live": None, "status_code": 400
            }), 400

        url_processed = checkout_url[:100] + "..." if len(checkout_url) > 100 else checkout_url
        print(f"[Processing] {url_processed}")

        # 3. Initialize Driver
        chrome_options = setup_chrome_driver()
        
        # Using Service object helps prevent some crashes
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(40) # Increased timeout slightly for render's cold starts

        # 4. Load Page
        driver.get(checkout_url)
        
        # Wait for dynamic JS to render Stripe elements
        time.sleep(5) 
        
        page_source = driver.page_source
        
        if len(page_source) < 500:
             raise Exception("Page content too short. Possibly blocked or invalid URL.")

        # 5. Extract
        pk_live, cs_live = extract_keys_from_html(page_source)

        response_data = {
            "status": "success",
            "pk_live": pk_live,
            "cs_live": cs_live,
            "status_code": 200,
            "url_processed": url_processed
        }

        if not pk_live and not cs_live:
            response_data["status"] = "partial"
            response_data["message"] = "Page loaded, but no keys found with current patterns."
            response_data["sample_content"] = page_source[:300]

        return jsonify(response_data), 200

    except TimeoutException:
        return jsonify({
            "status": "error", "message": "Page Load Timeout (Server took too long to respond)",
            "pk_live": None, "cs_live": None, "status_code": 408
        }), 408

    except WebDriverException as e:
        # Handle specific Selenium errors (e.g. Chrome not reachable)
        err_msg = str(e)
        if "chrome not reachable" in err_msg:
            err_msg = "Browser crashed. This is common on free tiers. Retrying might work."
        return jsonify({
            "status": "error", "message": f"Driver Error: {err_msg}",
            "pk_live": None, "cs_live": None, "status_code": 500
        }), 500

    except Exception as e:
        print(f"[Critical Error] {traceback.format_exc()}")
        return jsonify({
            "status": "error", "message": f"Server Error: {str(e)}",
            "pk_live": None, "cs_live": None, "status_code": 500
        }), 500

    finally:
        # ALWAYS CLEANUP
        if driver:
            try:
                driver.quit()
            except:
                pass

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "stripe-extractor"}), 200

if __name__ == '__main__':
    # Render uses PORT env variable
    port = int(os.environ.get("PORT", 5000))
    # debug=False is crucial for production/stable deployment
    app.run(host='0.0.0.0', port=port, debug=False)
