# receipt_automation.py

import os
import requests
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from dotenv import load_dotenv
from langdetect import detect
import json

# Load environment variables
load_dotenv()

# Airtable config
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
RECEIPTS_TABLE_NAME = "Receipts"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{RECEIPTS_TABLE_NAME}"

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# Groq config
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json"
}

def fetch_unprocessed_receipts():
    params = {
        "filterByFormula": "AND({Receipt File}, NOT({Vendor}))",
        "pageSize": 10
    }
    res = requests.get(AIRTABLE_URL, headers=HEADERS, params=params)
    return res.json().get("records", [])

def download_image(url, filename="temp"):
    r = requests.get(url)
    content_type = r.headers.get("Content-Type", "")
    ext = ".jpg"
    if "pdf" in content_type:
        ext = ".pdf"
    filepath = filename + ext
    with open(filepath, "wb") as f:
        f.write(r.content)
    return filepath

def run_ocr(filename="temp.jpg"):
    if filename.endswith(".pdf"):
        images = convert_from_path(filename)
        text = ""
        for i, img in enumerate(images):
            img_path = f"page_{i}.jpg"
            img.save(img_path, "JPEG")
            text += pytesseract.image_to_string(img, lang="eng+tha+ukr+mya+ben") + "\n"
            os.remove(img_path)
    else:
        text = pytesseract.image_to_string(Image.open(filename), lang="eng+tha+ukr+mya+ben")
    lang = detect(text)
    return text, lang

def translate_to_english(text):
    messages = [
        {
            "role": "system",
            "content": "Translate the following receipt text into English. Only translate. Do not summarize or explain."
        },
        {"role": "user", "content": text}
    ]
    data = {
        "model": "llama3-70b-8192",
        "messages": messages
    }
    res = requests.post(GROQ_URL, headers=GROQ_HEADERS, json=data)
    print("🔁 Groq raw response:", res.text)  # Add this
    
    try:
        return res.json()["choices"][0]["message"]["content"]
    except KeyError:
        print("❌ Groq translation failed. Response:")
        print(res.text)
        raise Exception("Translation failed")

def ask_groq(text):
    messages = [
        {
            "role": "system",
            "content": (
                "Extract the following from this receipt text:\n"
                "- Vendor (store or company name)\n"
                "- Amount (total paid)\n"
                "- Currency (e.g., USD, THB, MYR)\n"
                "- Purchase Date (date of purchase)\n"
                "Return the result as JSON like this:\n"
                "{\"Vendor\": \"ABC Store\", \"Amount\": 123.45, \"Currency\": \"USD\", \"Purchase Date\": \"2025-05-01\"}"
            )
        },
        {"role": "user", "content": text}
    ]
    data = {
        "model": "llama3-70b-8192",
        "messages": messages
    }
    res = requests.post(GROQ_URL, headers=GROQ_HEADERS, json=data)
    print("🔁 Groq raw response:", res.text)  # Add this
    
    try:
        return json.loads(res.json()["choices"][0]["message"]["content"])
    except KeyError:
        print("❌ Groq extraction failed. Response:")
        print(res.text)
        raise Exception("Extraction failed")

def update_airtable_record(record_id, raw_text, structured_data, status="Success"):
    update_data = {
        "fields": {
            "Processed Text": raw_text,
            "Vendor": structured_data.get("Vendor"),
            "Amount": structured_data.get("Amount"),
            "Currency": structured_data.get("Currency"),
            "Purchase Date": structured_data.get("Purchase Date"),
            "Status": status
        }
    }
    url = f"{AIRTABLE_URL}/{record_id}"
    requests.patch(url, headers=HEADERS, json=update_data)

def process_receipts():
    records = fetch_unprocessed_receipts()
    for rec in records:
        print(f"Processing: {rec['id']}")
        try:
            attachment_url = rec['fields']['Receipt File'][0]['url']
            filepath = download_image(attachment_url)
            ocr_text, lang = run_ocr(filepath)
            print(f"Detected language: {lang}")
            if lang != "en":
                ocr_text = translate_to_english(ocr_text)
            structured = ask_groq(ocr_text)
            update_airtable_record(rec["id"], ocr_text, structured, status="Success")
            print("✅ Updated:", structured)
        except Exception as e:
            print(f"❌ Failed to process {rec['id']}: {e}")
            update_airtable_record(rec["id"], "", {}, status="Failed")

if __name__ == "__main__":
    process_receipts()
