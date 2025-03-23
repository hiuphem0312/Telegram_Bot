import os
import re
import time
import json
import logging
import requests
import gspread
from newspaper import Article  # <-- NEW: newspaper3k import
from google.oauth2.service_account import Credentials
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
REQUEST_TIMEOUT = 30
MAX_CONTENT_LENGTH = 50000  # ~50KB

# API Configuration for DeepSeek (via OpenRouter)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
if not OPENROUTER_API_KEY:
    raise Exception("Missing OPENROUTER_API_KEY in .env")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_HEADERS = {
    'Authorization': f'Bearer {OPENROUTER_API_KEY}',
    'HTTP-Referer': 'https://github.com/phonghoang2k/adbot',
    'X-Title': 'ADBOT',
    'Content-Type': 'application/json',
    'OpenAI-Organization': 'org-123',
    'User-Agent': 'Mozilla/5.0'
}

# -----------------------------
# Helper: Parse DeepSeek result
# -----------------------------
def parse_deepseek_result(result_text: str) -> dict:
    """
    Parse the DeepSeek API response line by line to capture
    'Chủ đề', 'Tiêu đề', 'Tóm tắt'.
    """
    sections = {
        'Chủ đề': '',
        'Tiêu đề': '',
        'Tóm tắt': ''
    }
    current_section = None

    for line in result_text.splitlines():
        line = line.strip()
        if line.startswith('Chủ đề:'):
            current_section = 'Chủ đề'
            sections['Chủ đề'] = line[len('Chủ đề:'):].strip()
        elif line.startswith('Tiêu đề:'):
            current_section = 'Tiêu đề'
            sections['Tiêu đề'] = line[len('Tiêu đề:'):].strip()
        elif line.startswith('Tóm tắt:'):
            current_section = 'Tóm tắt'
            sections['Tóm tắt'] = line[len('Tóm tắt:'):].strip()
        else:
            # If we're in a section, append additional text
            if current_section and line:
                sections[current_section] += ' ' + line

    return {
        'subject': sections['Chủ đề'].strip(),
        'title': sections['Tiêu đề'].strip(),
        'summary': sections['Tóm tắt'].strip()
    }

# -----------------------------
# NEW: Newspaper3k-based function
# -----------------------------
def fetch_webpage_content(url: str) -> tuple:
    """
    Fetch and clean the text content + actual title from a webpage using newspaper3k.
    
    Args:
        url (str): URL of the webpage.
    
    Returns:
        tuple: (Cleaned text content, real article title)
    """
    for attempt in range(MAX_RETRIES):
        try:
            article = Article(url)
            article.download()
            article.parse()

            content = article.text.strip()
            real_title = article.title.strip() if article.title else "Không có tiêu đề"
            
            if not content:
                raise Exception("No content extracted by newspaper3k")

            # Limit content length
            if len(content) > MAX_CONTENT_LENGTH:
                content = content[:MAX_CONTENT_LENGTH] + "..."

            return (content, real_title)
        
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to parse article with newspaper3k: {e}")
            time.sleep(RETRY_DELAY)
    
    logger.error(f"Failed to fetch content from {url} after {MAX_RETRIES} attempts.")
    return None, None

# -----------------------------
# Content Analysis with DeepSeek
# -----------------------------
def analyze_content(content: str) -> dict:
    """
    Analyze content using the DeepSeek API (via OpenRouter) and extract key sections:
    Chủ đề, Tiêu đề, Tóm tắt.
    
    Args:
        content (str): The content to analyze.
    
    Returns:
        dict: Dictionary containing 'subject', 'title', 'summary'.
    """
    if not content:
        raise ValueError("Empty content provided for analysis.")
    
    prompt = f"""Hãy phân tích nội dung sau và trả về kết quả theo định dạng chính xác:

Chủ đề: [chủ đề chính (vd: chính trị, thể thao, thời trang...)]
Tiêu đề: [tiêu đề của bài viết]
Tóm tắt: [tóm tắt ngắn gọn nội dung]

Nội dung cần phân tích:
{content}

Lưu ý: Phải trả về đúng định dạng với các từ khóa 'Chủ đề:', 'Tiêu đề:', 'Tóm tắt:' ở đầu mỗi phần."""
    
    data = {
        "model": "deepseek/deepseek-r1:free",
        "messages": [
            {
                "role": "system", 
                "content": "Bạn là một trợ lý AI chuyên phân tích nội dung. Hãy trả về kết quả theo đúng định dạng được yêu cầu."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1000
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"Sending analysis request to DeepSeek API (attempt {attempt + 1})...")
            response = requests.post(
                OPENROUTER_API_URL,
                headers=OPENROUTER_HEADERS,
                json=data,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            result_json = response.json()
            
            # Extract response content
            result_text = result_json['choices'][0]['message']['content']
            logger.info("Raw API response received:")
            logger.info(result_text)
            
            # Parse the result text
            result_dict = parse_deepseek_result(result_text)
            
            # Warn if any sections are missing
            for section_key in ['subject', 'title', 'summary']:
                if not result_dict.get(section_key):
                    logger.warning(f"Warning: '{section_key}' section is empty in the analysis result.")
            
            return result_dict
        
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt + 1} failed during analysis: {e}")
            time.sleep(RETRY_DELAY)
    
    logger.error("Failed to analyze content after multiple attempts.")
    return {}

# -----------------------------
# Google Sheets Functions
# -----------------------------
def init_google_sheets():
    """
    Initializes connection to Google Sheets using service account credentials
    stored as a Secret File on Render at /etc/secrets/credentials.json.

    Returns:
        gspread.Spreadsheet: The opened spreadsheet object.
    """
    secret_path = "/etc/secrets/credentials.json"
    if not os.path.exists(secret_path):
        raise Exception(f"Credentials file not found at {secret_path}")

    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    # Create credentials from the secret file
    credentials = Credentials.from_service_account_file(secret_path, scopes=scopes)
    client = gspread.authorize(credentials)

    # Read the spreadsheet ID from an environment variable
    spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
    if not spreadsheet_id:
        raise Exception("Missing GOOGLE_SHEETS_SPREADSHEET_ID in .env")

    return client.open_by_key(spreadsheet_id)


def get_or_create_worksheet(spreadsheet) -> gspread.Worksheet:
    """
    Retrieves the first worksheet or creates one if none exists.
    
    Args:
        spreadsheet (gspread.Spreadsheet): The spreadsheet object.
    
    Returns:
        gspread.Worksheet: The worksheet to update.
    """
    try:
        worksheet = spreadsheet.get_worksheet(0)
        if not worksheet:
            worksheet = spreadsheet.add_worksheet(title="Sheet1", rows="1000", cols="20")
            logger.info("Created new worksheet 'Sheet1'.")
        else:
            logger.info("Worksheet found.")
    except Exception as e:
        raise Exception(f"Error handling worksheet: {str(e)}")
    
    # Ensure header row exists
    try:
        # Columns: Chủ đề, Tiêu đề, Tóm tắt, Link bài báo, Timestamp
        headers = ['Chủ đề', 'Tiêu đề', 'Tóm tắt', 'Link bài báo', 'Timestamp']
        first_row = worksheet.row_values(1)
        if not first_row:
            logger.info("Adding headers to the worksheet...")
            worksheet.append_row(headers)
    except Exception as e:
        raise Exception(f"Error processing headers: {str(e)}")
    
    return worksheet

def update_google_sheet(data: dict, url: str) -> None:
    """
    Append analysis results to Google Sheet along with a timestamp
    and the user-provided URL as 'Link bài báo'.
    
    Args:
        data (dict): Analysis results with keys 'subject', 'title', 'summary'.
        url (str): The URL that was analyzed (used as 'Link bài báo').
    """
    try:
        spreadsheet = init_google_sheets()
        worksheet = get_or_create_worksheet(spreadsheet)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_row = [
            data.get('subject', 'Không có thông tin'),
            data.get('title', 'Không có thông tin'),
            data.get('summary', 'Không có thông tin'),
            url,
            timestamp
        ]
        
        logger.info("Appending new data to Google Sheet...")
        worksheet.append_row(new_row)
        logger.info("Data successfully added to Google Sheet.")
        
        # Backup the data locally in a JSON file
        backup_data = {
            'timestamp': timestamp,
            'analysis': data,
            'url': url
        }
        backup_file = f"backup_{datetime.now().strftime('%Y%m%d')}.json"
        with open(backup_file, 'a', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False)
            f.write('\n')
        logger.info(f"Backup data saved to {backup_file}.")
    
    except Exception as e:
        raise Exception(f"Error updating Google Sheet: {str(e)}")

# -----------------------------
# End-to-End Process Function
# -----------------------------
def process_article(url: str) -> None:
    """
    Processes the article by extracting content (with newspaper3k),
    analyzing it (DeepSeek), and updating Google Sheet.
    
    Args:
        url (str): URL of the article.
    """
    logger.info(f"Processing article: {url}")
    
    # 1) Fetch the content AND the real article title
    content, real_title = None, None
    for attempt in range(MAX_RETRIES):
        try:
            article = Article(url)
            article.download()
            article.parse()

            content = article.text.strip()
            real_title = article.title.strip() if article.title else "Không có tiêu đề"

            if not content:
                raise Exception("No content extracted by newspaper3k")

            if len(content) > MAX_CONTENT_LENGTH:
                content = content[:MAX_CONTENT_LENGTH] + "..."
            
            break  # Successfully got content/title
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(RETRY_DELAY)

    if not content:
        logger.error("Content extraction failed.")
        return

    # 2) Analyze with DeepSeek
    analysis = analyze_content(content)
    if not analysis:
        logger.error("Content analysis failed.")
        return
    
    # 3) Override 'title' with the real headline
    analysis['title'] = real_title

    # 4) Update Google Sheet
    update_google_sheet(analysis, url)
    logger.info("Article processing complete.")
