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
def parse_deepseek_result(result_text: str, article_title: str) -> dict:
    """
    Parse the DeepSeek API response line by line to capture
    'Chủ đề', 'Tiêu đề', 'Tóm tắt'.
    """
    sections = {
        'Chủ đề': '',
        'Tiêu đề': article_title,  # Use actual article title
        'Tóm tắt': ''
    }
    current_section = None

    for line in result_text.splitlines():
        line = line.strip()
        if line.startswith('Chủ đề:'):
            current_section = 'Chủ đề'
            sections['Chủ đề'] = line[len('Chủ đề:'):].strip()
        elif line.startswith('Tóm tắt:'):
            current_section = 'Tóm tắt'
            sections['Tóm tắt'] = line[len('Tóm tắt:'):].strip()
        else:
            # If we're in a section, append additional text
            if current_section and line:
                sections[current_section] += ' ' + line

    return {
        'subject': sections['Chủ đề'].strip(),
        'title': sections['Tiêu đề'].strip(),  # Ensuring title remains unchanged
        'summary': sections['Tóm tắt'].strip()
    }

# -----------------------------
# NEW: Newspaper3k-based function
# -----------------------------
def fetch_webpage_content(url: str) -> tuple:
    """
    Fetch and clean the text content from a webpage using newspaper3k.
    
    Args:
        url (str): URL of the webpage.
    
    Returns:
        tuple: (Cleaned text content, article title)
    """
    for attempt in range(MAX_RETRIES):
        try:
            article = Article(url)
            article.download()
            article.parse()

            # article.text is the extracted main text
            content = article.text.strip()
            title = article.title.strip() if article.title else "Không có tiêu đề"
            
            if not content:
                raise Exception("No content extracted by newspaper3k")

            # Limit content length
            if len(content) > MAX_CONTENT_LENGTH:
                content = content[:MAX_CONTENT_LENGTH] + "..."

            return content, title
        
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to parse article with newspaper3k: {e}")
            time.sleep(RETRY_DELAY)
    
    logger.error(f"Failed to fetch content from {url} after {MAX_RETRIES} attempts.")
    return None, None

# -----------------------------
# Content Analysis with DeepSeek
# -----------------------------
def analyze_content(content: str, article_title: str) -> dict:
    """
    Analyze content using the DeepSeek API (via OpenRouter) and extract key sections:
    Chủ đề, Tiêu đề, Tóm tắt.
    
    Args:
        content (str): The content to analyze.
        article_title (str): The actual title extracted from the article.
    
    Returns:
        dict: Dictionary containing 'subject', 'title', 'summary'.
    """
    if not content:
        raise ValueError("Empty content provided for analysis.")
    
    prompt = f"""Hãy phân tích nội dung sau và trả về kết quả theo định dạng chính xác:

Chủ đề: [chủ đề chính (vd: chính trị, thể thao, thời trang...)]
Tóm tắt: [tóm tắt ngắn gọn nội dung]

Nội dung cần phân tích:
{content}

Lưu ý: Phải trả về đúng định dạng với các từ khóa 'Chủ đề:', 'Tóm tắt:' ở đầu mỗi phần."""
    
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
            
            # Parse the result text with actual article title
            result_dict = parse_deepseek_result(result_text, article_title)
            return result_dict
        
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt + 1} failed during analysis: {e}")
            time.sleep(RETRY_DELAY)
    
    logger.error("Failed to analyze content after multiple attempts.")
    return {}

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
    
    content, article_title = fetch_webpage_content(url)
    if not content:
        logger.error("Content extraction failed.")
        return
    
    analysis = analyze_content(content, article_title)
    if not analysis:
        logger.error("Content analysis failed.")
        return
    
    update_google_sheet(analysis, url)
    logger.info("Article processing complete.")
