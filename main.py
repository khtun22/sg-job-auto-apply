import os
import json
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from openai import OpenAI
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv()

# Initialize API clients
ds_client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")
oai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Load user profile data
with open("profile.json", "r", encoding="utf-8") as f:
    USER_PROFILE = json.load(f)

def init_db():
    """Initialize the SQLite database for logging applications."""
    conn = sqlite3.connect("applications.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            job_title TEXT,
            url TEXT,
            status TEXT,
            applied_at TEXT
        )
    ''')
    conn.commit()
    return conn

def extract_form_html(page):
    """Extract and clean the form DOM from the current page."""
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    
    if not forms:
        return ""
        
    # Keep only essential input attributes to significantly reduce token usage
    for tag in forms[0].find_all(True):
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in ["name", "id", "type", "placeholder", "required"]}
        
    return str(forms[0])

def parse_form_with_routing(form_html):
    """Dual-model routing: DeepSeek as primary, OpenAI as fallback."""
    prompt = f"""
    Analyze the following HTML form and output Playwright filling instructions based on the provided user profile.
    Return ONLY a JSON array. Format example: [{{"selector": "input[name='email']", "value": "xxx", "action": "fill"}}]
    
    User Profile: {json.dumps(USER_PROFILE)}
    Form HTML: {form_html}
    """
    
    # 1. Try DeepSeek first (Cost-effective)
    try:
        print("-> Calling DeepSeek to parse the form...")
        response = ds_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=15
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"-> DeepSeek parsing failed ({e}), switching to OpenAI fallback...")
        
    # 2. Fallback to OpenAI (High stability)
    try:
        response = oai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"-> OpenAI parsing also failed: {e}")
        return []

def main():
    db_conn = init_db()
    
    with sync_playwright() as p:
        # Launch browser (headless=False allows you to see the automation visually)
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # Target job application URL for testing
        target_url = "https://example.com/apply" 
        print(f"-> Visiting: {target_url}")
        
        try:
            page.goto(target_url, timeout=30000)
            
            # Extract form and request LLM parsing
            form_html = extract_form_html(page)
            
            if form_html:
                instructions = parse_form_with_routing(form_html)
                
                # Parse returned instructions (handle both dict and list structures)
                actions = instructions.get("instructions", instructions) if isinstance(instructions, dict) else instructions
                
                # Execute filling actions
                for cmd in actions:
                    if cmd.get("action") == "fill":
                        page.locator(cmd["selector"]).fill(cmd["value"])
                        print(f"Filled: {cmd['selector']} -> {cmd['value']}")
                
                # Pause for manual inspection before actual submission
                print("-> Pausing execution. Please check the browser.")
                page.pause() 
                
                # Log application attempt to database
                cursor = db_conn.cursor()
                cursor.execute(
                    "INSERT INTO jobs (company_name, job_title, url, status, applied_at) VALUES (?, ?, ?, ?, ?)",
                    ("Test Company", "Backend Engineer", target_url, "success", datetime.now().isoformat())
                )
                db_conn.commit()
                print("-> Database record saved successfully.")
                
            else:
                print("-> No form found on the page.")
                
        except Exception as e:
            print(f"-> Process interrupted: {e}")
            
        finally:
            browser.close()
            db_conn.close()

if __name__ == "__main__":
    main()