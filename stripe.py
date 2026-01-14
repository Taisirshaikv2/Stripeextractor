from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import re
import requests
import json
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=5)  # Limit concurrent extractions

class StripeCheckoutExtractor:
    def __init__(self, headless=True):
        """Initialize the extractor with optimized Selenium driver"""
        self.options = Options()
        if headless:
            self.options.add_argument("--headless=new")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-gpu")
        self.options.add_argument("--window-size=1920,1080")
        self.options.add_argument("--disable-extensions")
        self.options.add_argument("--disable-blink-features=AutomationControlled")
        self.options.add_experimental_option("excludeSwitches", ["enable-automation"])
        self.options.add_experimental_option('useAutomationExtension', False)
        self.options.add_argument("--disable-logging")
        self.options.add_argument("--log-level=3")
        
        self.service = Service(ChromeDriverManager().install())
        self.driver = None
        
    def init_driver(self):
        """Initialize Chrome driver with anti-detection"""
        try:
            self.driver = webdriver.Chrome(service=self.service, options=self.options)
            # Execute CDP commands to prevent detection
            self.driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            return True
        except Exception as e:
            print(f"Driver init error: {e}")
            return False
    
    def extract_all_info(self, url):
        """Extract all required information from Stripe checkout"""
        if not self.driver:
            if not self.init_driver():
                return {"error": "Failed to initialize browser"}
        
        try:
            # Load the page
            self.driver.get(url)
            
            # Wait for page to load
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(2)  # Allow JavaScript to execute
            
            page_source = self.driver.page_source
            
            # Extract tokens using regex
            cs_token = None
            pk_token = None
            
            # Extract cs_live_token
            cs_match = re.search(r'data-session-id="(cs_live_[^"]+)"', page_source)
            if not cs_match:
                cs_match = re.search(r'sessionId:\s*["\'](cs_live_[^"\']+)["\']', page_source)
            if cs_match:
                cs_token = cs_match.group(1)
            
            # Extract pk_live_token
            pk_match = re.search(r'apiKey=(pk_live_[^&"\']+)', page_source)
            if not pk_match:
                pk_match = re.search(r'["\']pk_live_[^"\']+["\']', page_source)
            if pk_match:
                pk_token = pk_match.group(1)
            
            # Extract site name from multiple locations
            site_name = None
            site_url = None
            
            # Method 1: From h1 business name
            try:
                business_elem = self.driver.find_element(By.CSS_SELECTOR, 
                    'h1[data-testid="business-name"], .Business-name, [class*="business-name"]')
                site_name = business_elem.text.strip()
            except:
                pass
            
            # Method 2: From business link
            if not site_name:
                try:
                    business_link = self.driver.find_element(By.CSS_SELECTOR,
                        'a[data-testid="business-link"], .BusinessLink, a[aria-label*="Back to"]')
                    site_name = business_link.get_attribute('title') or business_link.get_attribute('aria-label')
                    site_url = business_link.get_attribute('href')
                except:
                    pass
            
            # Method 3: Extract from page title or URL
            if not site_name:
                try:
                    title = self.driver.title
                    # Clean up title
                    site_name = title.replace(' - Checkout', '').replace('Checkout', '').strip()
                    if not site_name or len(site_name) < 2:
                        site_name = "Unknown"
                except:
                    site_name = "Unknown"
            
            # Extract amount
            amount = None
            currency = None
            try:
                amount_elem = self.driver.find_element(By.CSS_SELECTOR,
                    '.CurrencyAmount, [class*="CurrencyAmount"], [data-testid*="amount"]')
                amount_text = amount_elem.text.strip()
                
                # Extract currency and amount
                currency_match = re.search(r'([A-Z]{2,3}[$€£¥]?\s*[\d,.]+)', amount_text)
                if currency_match:
                    amount_text = currency_match.group(1)
                
                # Try to parse amount
                amount_match = re.search(r'(\d+[.,]?\d*)', amount_text)
                if amount_match:
                    amount = amount_match.group(1).replace(',', '.')
                    # Extract currency symbol
                    currency_symbol = re.search(r'([$€£¥])', amount_text)
                    if currency_symbol:
                        currency = currency_symbol.group(1)
            except:
                pass
            
            # Detect plan type (monthly/yearly)
            plan_type = "one_time"
            try:
                page_text = self.driver.page_source.lower()
                if "per month" in page_text or "/month" in page_text or "monthly" in page_text:
                    plan_type = "monthly"
                elif "per year" in page_text or "/year" in page_text or "yearly" in page_text:
                    plan_type = "yearly"
                elif "annual" in page_text:
                    plan_type = "yearly"
            except:
                pass
            
            # Extract email
            email = None
            try:
                email_elem = self.driver.find_element(By.CSS_SELECTOR,
                    '.ReadOnlyFormField-title, input[type="email"][readonly], [data-testid*="email"]')
                email = email_elem.get_attribute('value') or email_elem.text
                if not email or '@' not in email:
                    email = None
            except:
                pass
            
            # Get MUID, GUID, SID from stripe network request
            muid = None
            guid = None
            sid = None
            
            if pk_token and cs_token:
                try:
                    # Make network request to get muid/guid/sid
                    net_headers = {
                        'accept': '*/*',
                        'accept-language': 'en-US',
                        'cache-control': 'no-cache',
                        'content-type': 'text/plain;charset=UTF-8',
                        'origin': 'https://m.stripe.network',
                        'pragma': 'no-cache',
                        'referer': 'https://m.stripe.network/',
                        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    }
                    
                    net_resp = requests.post('https://m.stripe.com/6', headers=net_headers, timeout=5)
                    if net_resp.status_code == 200:
                        data = net_resp.json()
                        muid = data.get("muid")
                        guid = data.get("guid")
                        sid = data.get("sid")
                except:
                    pass
            
            # If no site URL found, try to extract from page
            if not site_url and site_name != "Unknown":
                try:
                    # Look for any links that might be the main site
                    all_links = self.driver.find_elements(By.TAG_NAME, "a")
                    for link in all_links:
                        href = link.get_attribute("href")
                        if href and not ("stripe.com" in href or "checkout.stripe.com" in href):
                            if href.startswith("http"):
                                site_url = href
                                break
                except:
                    pass
            
            # Prepare response
            response = {
                "url": url,
                "site": site_name,
                "site_url": site_url,
                "amount": amount,
                "currency": currency,
                "type": plan_type,
                "email": email,
                "cs_token": cs_token,
                "pk_token": pk_token,
                "guid": guid,
                "muid": muid,
                "sid": sid,
                "status": "success"
            }
            
            return response
            
        except Exception as e:
            return {"error": str(e), "status": "error"}
        
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up driver resources"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None

def extract_with_timeout(url, timeout=30):
    """Extract information with timeout"""
    extractor = StripeCheckoutExtractor()
    
    def extract():
        return extractor.extract_all_info(url)
    
    try:
        # Run extraction with timeout
        future = executor.submit(extract)
        return future.result(timeout=timeout)
    except TimeoutError:
        return {"error": "Extraction timed out", "status": "error"}
    except Exception as e:
        return {"error": str(e), "status": "error"}
    finally:
        extractor.cleanup()

@app.route('/extract', methods=['GET'])
def extract_checkout():
    """API endpoint to extract Stripe checkout information"""
    checkout_url = request.args.get('checkout_url')
    
    if not checkout_url:
        return jsonify({
            "error": "Missing checkout_url parameter",
            "status": "error"
        }), 400
    
    # Validate URL
    if not checkout_url.startswith(('http://', 'https://')):
        checkout_url = 'https://' + checkout_url
    
    # Ensure it's a Stripe checkout URL
    if 'checkout.stripe.com' not in checkout_url:
        return jsonify({
            "error": "Not a valid Stripe checkout URL",
            "status": "error"
        }), 400
    
    try:
        # Extract information with timeout
        result = extract_with_timeout(checkout_url)
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "status": "error"
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "Stripe Checkout Extractor API"
    })

@app.route('/')
def index():
    """API documentation"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Stripe Checkout Extractor API</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #333; }
            code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }
            pre { background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }
            .endpoint { background: #e8f4f8; padding: 10px; border-left: 4px solid #007bff; margin: 10px 0; }
        </style>
    </head>
    <body>
        <h1>Stripe Checkout Extractor API</h1>
        <p>Extract tokens and information from Stripe checkout pages.</p>
        
        <div class="endpoint">
            <h3>Extract Endpoint</h3>
            <p><code>GET /extract?checkout_url=URL</code></p>
            
            <h4>Example:</h4>
            <pre>curl "http://localhost:5000/extract?checkout_url=https://checkout.stripe.com/..."</pre>
            
            <h4>Response Format:</h4>
            <pre>
{
    "url": "checkout_url",
    "site": "Site Name",
    "site_url": "https://site.com",
    "amount": "9.99",
    "currency": "$",
    "type": "monthly",
    "email": "user@example.com",
    "cs_token": "cs_live_...",
    "pk_token": "pk_live_...",
    "guid": "...",
    "muid": "...",
    "sid": "...",
    "status": "success"
}
            </pre>
        </div>
        
        <div class="endpoint">
            <h3>Health Check</h3>
            <p><code>GET /health</code></p>
        </div>
    </body>
    </html>
    """

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)