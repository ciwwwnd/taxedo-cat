from __future__ import annotations

import json
import logging
from datetime import datetime
from io import BytesIO

import anthropic
import fitz  # PyMuPDF

from config import Config

logger = logging.getLogger(__name__)
config = Config()

_tesseract = None


def _get_tesseract():
    global _tesseract
    if _tesseract is None:
        import pytesseract
        _tesseract = pytesseract
    return _tesseract


SYSTEM_PROMPT = """You are a receipt/expense analyzer for two people in Germany.
They are Steuerklasse 1, NOT married, filing SEPARATE tax declarations.

You receive OCR-extracted text from a receipt or an expense description.
Analyze it and return ONLY a valid JSON object (no markdown, no backticks).

GERMAN TAX DEDUCTION CATEGORIES you must use:

DEDUCTIBLE:
• Werbungskosten > Arbeitsmittel — work equipment (laptop, monitor, keyboard, software, desk, chair). If >€952 net, must depreciate.
• Werbungskosten > Arbeitsmittel (Software) — work software subscriptions (JetBrains, Adobe, GitHub Copilot, Notion, etc.)
• Werbungskosten > Fortbildungskosten — professional training (Udemy, Coursera, certifications, conferences)
• Werbungskosten > Fahrtkosten — commuting (Deutschlandticket, train tickets, fuel for commute)
• Werbungskosten > Homeoffice-Pauschale — home office days (€6/day, max €1,260/year)
• Werbungskosten > Telefon / Internet — phone/internet (typically 20% of bill deductible as work portion)
• Werbungskosten > Reisekosten — business travel (hotels, flights, meals during business trips)
• Werbungskosten > Bewerbungskosten — job application costs
• Werbungskosten > Kontoführungsgebühren — bank fees (Pauschale €16/year)
• Werbungskosten > Gewerkschaftsbeiträge — union membership
• Werbungskosten > Umzugskosten — work-related moving costs
• Sonderausgaben > Vorsorgeaufwendungen — insurance premiums (Haftpflicht, BU, Unfall, Riester, Rürup, health insurance)
• Sonderausgaben > Kirchensteuer — church tax
• Sonderausgaben > Spenden — charitable donations (need Zuwendungsbestätigung)
• Außergewöhnliche Belastungen > Krankheitskosten — medical expenses (doctor co-pays, prescriptions, glasses, dental, physio, hearing aids)
• Haushaltsnahe Dienstleistungen — household services: cleaning, gardening, pet care at home (20% of labor, max €4,000 credit)
• Handwerkerleistungen — craftsman at home: plumber, electrician, painter (20% of labor, max €1,200 credit). ONLY labor counts!
• Vermietung und Verpachtung — rental property expenses (mortgage interest, repairs, AfA)

NOT DEDUCTIBLE:
• Groceries, regular clothing, restaurants (private), Netflix/Spotify/Disney+ (private use), gym, vacations, hobbies, private furniture, cosmetics, alcohol, tobacco

GRAY ZONE (flag as "check manually"):
• Amazon — depends on what was bought
• Books — professional literature yes, novels no
• Furniture — only if for dedicated Arbeitszimmer
• Phone/internet — only ~20% as work portion
• Fuel — commute covered by Pauschale, business trips by actual cost

Return this JSON structure:
{
    "extracted_text": "cleaned up version of the OCR text",
    "store_name": "Store Name",
    "receipt_date": "YYYY-MM-DD or null",
    "total_amount": 12.34,
    "currency": "EUR",
    "items": [{"name": "Item", "price": 1.23, "quantity": 1}],
    "tax_category": "Main category in German",
    "tax_subcategory": "Specific subcategory",
    "tax_deductible": true/false,
    "deduction_notes": "Brief explanation WHY. If gray zone, explain conditions."
}

Rules:
- Be smart about context: "Saturn" + "Monitor" = Arbeitsmittel, not generic electronics
- Total amount as float, not string
- Always provide deduction_notes with reasoning
- Be conservative: only mark deductible if it genuinely qualifies
- For gray zone items, set tax_deductible to true but add conditions in notes
- Use German tax terminology
"""


class ReceiptAnalyzer:
    def __init__(self):
        api_key = config.ANTHROPIC_API_KEY
        if api_key:
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self.client = None
            logger.warning("No ANTHROPIC_API_KEY set.")
        self.model = config.CLAUDE_MODEL

        from budget_watchdog import BudgetWatchdog
        self.watchdog = BudgetWatchdog()
        self._alert_callback = None

    async def analyze_image(self, image_bytes: bytes, caption: str = "") -> dict:
        ocr_text = self._ocr_image(image_bytes)
        return await self._analyze_extracted_text(ocr_text, caption)

    async def analyze_pdf(self, pdf_bytes: bytes, caption: str = "") -> dict:
        text = self._extract_pdf_text(pdf_bytes)
        if not text.strip() or len(text.strip()) < 20:
            text = self._ocr_pdf_pages(pdf_bytes)
        return await self._analyze_extracted_text(text, caption)

    async def analyze_text(self, text: str) -> dict:
        return await self._analyze_extracted_text(text, "")

    def _ocr_image(self, image_bytes: bytes) -> str:
        try:
            from PIL import Image
            pytesseract = _get_tesseract()
            img = Image.open(BytesIO(image_bytes))
            text = pytesseract.image_to_string(img, lang="deu+eng")
            if text.strip():
                logger.info(f"Tesseract extracted {len(text)} chars")
                return text
        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
        return ""

    def _extract_pdf_text(self, pdf_bytes: bytes) -> str:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            parts = [page.get_text() for page in doc]
            doc.close()
            return "\n".join(parts)
        except Exception as e:
            logger.error(f"PDF text extraction failed: {e}")
            return ""

    def _ocr_pdf_pages(self, pdf_bytes: bytes) -> str:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            all_text = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_bytes = pix.tobytes("png")
                text = self._ocr_image(img_bytes)
                all_text.append(text)
            doc.close()
            return "\n".join(all_text)
        except Exception as e:
            logger.error(f"PDF OCR failed: {e}")
            return ""

    async def _analyze_extracted_text(self, ocr_text: str, caption: str) -> dict:
        combined = ocr_text.strip()
        if caption:
            combined = f"{combined}\n\nUser note: {caption}"

        if not combined:
            return self._empty_result("No text could be extracted from this file.")

        if not self.client:
            return self._empty_result("No API key configured — cannot analyze.")

        return await self._call_claude(combined)

    async def _call_claude(self, text: str) -> dict:
        try:
            prompt = (
                f"Analyze this receipt/expense and categorize for German taxes.\n"
                f"Today's date: {datetime.now().strftime('%Y-%m-%d')}\n\n"
                f"--- RECEIPT TEXT ---\n{text}\n--- END ---"
            )

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = response.content[0].text.strip()

            if response_text.startswith("```"):
                response_text = response_text.split("\n", 1)[1] if "\n" in response_text else response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            result = json.loads(response_text)

            usage = response.usage
            alert = self.watchdog.log_usage(
                self.model, usage.input_tokens, usage.output_tokens
            )
            if alert and self._alert_callback:
                await self._alert_callback(alert)

            return result

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return self._empty_result("Analysis returned invalid JSON. Please try again.")
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            alert = self.watchdog.check_api_error(e)
            if alert and self._alert_callback:
                await self._alert_callback(alert)
            return self._empty_result(f"API error: {e}. Please try again later.")
        except Exception as e:
            logger.error(f"Analysis error: {e}")
            return self._empty_result(f"Unexpected error during analysis: {e}")

    def _empty_result(self, notes: str) -> dict:
        return {
            "extracted_text": "",
            "store_name": "",
            "receipt_date": None,
            "total_amount": None,
            "currency": "EUR",
            "items": [],
            "tax_category": "Unklassifiziert",
            "tax_subcategory": "",
            "tax_deductible": False,
            "deduction_notes": notes,
        }
