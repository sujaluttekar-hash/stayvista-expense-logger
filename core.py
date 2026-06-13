"""
StayVista Expense Logger — Core Library
Shared utilities: Google services, Selenium helpers, form interactions.
All three loggers (inhouse, foodtown, clover) import from here.
"""

import os
import io
import re
import time
import tempfile
import logging
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoAlertPresentException

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")

def log(msg):
    print(f"[LOG] {msg}", flush=True)
    logging.info(msg)

# ─── Config ───────────────────────────────────────────────────────────────────

import json, tempfile as _tf
_cj = os.environ.get("GOOGLE_CREDENTIALS_JSON")
if _cj:
    _t = _tf.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    _t.write(_cj); _t.flush()
    CREDENTIALS_FILE = _t.name
else:
    import json, tempfile as _tf
_cj = os.environ.get("GOOGLE_CREDENTIALS_JSON")
if _cj:
    _t = _tf.NamedTemporaryFile(delete=False, suffix=".json", mode="w")
    _t.write(_cj); _t.flush()
    CREDENTIALS_FILE = _t.name
else:
    CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
ADMIN_URL        = "https://admin.vistarooms.com"
USERNAME         = "sujal.uttekar@stayvista.com"
PASSWORD         = "Sujal@2025"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# ─── Google Services ──────────────────────────────────────────────────────────

def get_services():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES
    )
    drive_service = build("drive", "v3", credentials=creds)
    gs_client     = gspread.authorize(creds)
    log("✅ Google services initialised")
    return drive_service, gs_client

# ─── Sheet helpers ────────────────────────────────────────────────────────────

def read_sheet_data(gs_client, sheet_id, tab_name):
    ws     = gs_client.open_by_key(sheet_id).worksheet(tab_name)
    values = ws.get_all_values()
    if not values:
        log(f"⚠️  Sheet '{tab_name}' is empty")
        return []
    headers = values[0]
    rows = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        rows.append({headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))})
    log(f"📋 {len(rows)} row(s) in '{tab_name}'")
    return rows


def already_processed(row, expense_id_col="Expense ID"):
    """Return True if this row already has an Expense ID logged."""
    val = str(row.get(expense_id_col, "")).strip()
    return bool(val)


def write_expense_id(gs_client, sheet_id, tab_name, booking_id_col, booking_id, expense_id):
    """Write expense_id back to the correct row in the correct sheet."""
    try:
        ws     = gs_client.open_by_key(sheet_id).worksheet(tab_name)
        values = ws.get_all_values()
        if not values:
            return

        headers = list(values[0])
        if "Expense ID" not in headers:
            headers.append("Expense ID")
            ws.update_cell(1, len(headers), "Expense ID")
            expense_col = len(headers)
        else:
            expense_col = headers.index("Expense ID") + 1

        if booking_id_col not in headers:
            log(f"⚠️  Column '{booking_id_col}' not found in sheet")
            return
        bid_col = headers.index(booking_id_col) + 1

        for row_idx, row in enumerate(values[1:], start=2):
            cell = row[bid_col - 1] if len(row) >= bid_col else ""
            if str(cell).strip() == str(booking_id).strip():
                ws.update_cell(row_idx, expense_col, expense_id)
                log(f"✅ Expense ID {expense_id} written → row {row_idx} (booking {booking_id})")
                return

        log(f"⚠️  Booking ID {booking_id} not found in sheet — Expense ID not written")

    except Exception as e:
        log(f"❌ Failed to write Expense ID: {e}")

# ─── Drive: download bill ─────────────────────────────────────────────────────

def download_bill(drive_service, folder_ids, booking_id):
    """
    Search one or more Drive folders for a file containing booking_id,
    download to a temp directory, and return the local path.
    """
    if isinstance(folder_ids, str):
        folder_ids = [folder_ids]

    for folder_id in folder_ids:
        query = (
            f"'{folder_id}' in parents "
            f"and name contains '{booking_id}' "
            f"and trashed = false"
        )
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=5,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()

        files = results.get("files", [])
        if not files:
            continue

        matched    = next((f for f in files if str(booking_id) in f["name"]), files[0])
        log(f"📥 Downloading: {matched['name']}")

        tmp_dir    = Path(tempfile.gettempdir()) / "sv_bills"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        local_path = tmp_dir / matched["name"]

        request    = drive_service.files().get_media(fileId=matched["id"], supportsAllDrives=True)
        fh         = io.FileIO(str(local_path), "wb")
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        log(f"✅ Downloaded → {local_path}")
        return str(local_path)

    log(f"⚠️  No bill found for booking {booking_id}")
    return None

# ─── Driver setup ─────────────────────────────────────────────────────────────

def setup_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_experimental_option("prefs", {
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
    })
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )
    return driver

# ─── Login ────────────────────────────────────────────────────────────────────

def login(driver):
    wait = WebDriverWait(driver, 20)
    log("🔐 Logging in...")
    driver.get(ADMIN_URL)
    wait.until(EC.element_to_be_clickable((By.NAME, "email"))).send_keys(USERNAME)
    driver.find_element(By.ID, "loginViaPasswordBtn").click()
    wait.until(EC.element_to_be_clickable((By.NAME, "password"))).send_keys(PASSWORD)
    driver.find_element(By.ID, "loginViaPasswordBtn").click()
    wait.until(lambda d: "login" not in d.current_url)
    time.sleep(1)
    if "login" in driver.current_url:
        raise Exception("Login failed — check credentials")
    log("✅ Logged in")

# ─── Navigate ─────────────────────────────────────────────────────────────────

def open_expense_page(driver):
    driver.get(f"{ADMIN_URL}/expenses/log")
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.ID, "select2-expensetype-container"))
    )
    log("➡️  Expense page ready")

# ─── Form helpers ─────────────────────────────────────────────────────────────

def select2(driver, container_id, value):
    wait = WebDriverWait(driver, 15)
    wait.until(EC.element_to_be_clickable((By.ID, container_id))).click()
    search = wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, ".select2-container--open .select2-search__field")
    ))
    search.clear()
    search.send_keys(value)
    time.sleep(0.5)
    search.send_keys(Keys.RETURN)
    time.sleep(0.3)


def fill_comment(driver, value):
    if not value or not value.strip():
        log("⚠️  Comment empty — skipping")
        return
    wait = WebDriverWait(driver, 15)
    el   = wait.until(EC.visibility_of_element_located((By.ID, "expense_head_categoriespart")))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].focus();", el)
    time.sleep(0.4)
    for attempt in range(3):
        driver.execute_script("""
            arguments[0].value = arguments[1];
            arguments[0].dispatchEvent(new Event('focus',  {bubbles:true}));
            arguments[0].dispatchEvent(new Event('input',  {bubbles:true}));
            arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
            arguments[0].dispatchEvent(new Event('blur',   {bubbles:true}));
        """, el, value)
        time.sleep(0.3)
        if driver.execute_script("return arguments[0].value;", el).strip():
            log(f"✅ Comment: {value}")
            return
        # Fallback: direct typing
        try:
            el.click(); el.clear(); el.send_keys(value)
            time.sleep(0.3)
            if driver.execute_script("return arguments[0].value;", el).strip():
                log(f"✅ Comment (typed): {value}")
                return
        except Exception:
            pass
        log(f"⚠️  Comment attempt {attempt+1} failed, retrying...")
        time.sleep(1)
    log("⚠️  Comment could not be filled — continuing")


def fill_input(driver, field_id, value):
    el = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.ID, field_id)))
    el.clear()
    el.send_keys(str(value))


def select_cost_bearer(driver):
    wait = WebDriverWait(driver, 10)
    try:
        Select(wait.until(EC.element_to_be_clickable((By.NAME, "cost_bearer")))).select_by_visible_text("VISTA")
        log("→ Cost Bearer: VISTA")
    except Exception:
        try:
            Select(driver.find_element(By.NAME, "cost_bearer")).select_by_visible_text("SV Managed")
            log("→ Cost Bearer: SV Managed (fallback)")
        except Exception:
            raise Exception("Neither VISTA nor SV Managed available for cost bearer")


def select_payment_status(driver):
    wait = WebDriverWait(driver, 10)
    try:
        Select(wait.until(EC.element_to_be_clickable((By.ID, "payment_status")))).select_by_visible_text("To Be Paid")
        log("→ Payment: To Be Paid")
    except Exception as e:
        log(f"⚠️  Payment status failed: {e}")


def fill_line_item(driver, qty, amount, gst):
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "quantity[]")))
    driver.find_element(By.NAME, "quantity[]").clear()
    driver.find_element(By.NAME, "quantity[]").send_keys(str(qty))
    driver.find_element(By.NAME, "rate_per_unit[]").clear()
    driver.find_element(By.NAME, "rate_per_unit[]").send_keys(str(amount))
    Select(driver.find_element(By.NAME, "tax_percentage[]")).select_by_visible_text(str(gst))
    log(f"→ Line item: qty={qty}  amount={amount}  gst={gst}%")


def set_bill_date(driver):
    today = datetime.now()
    driver.execute_script("""
        const el = document.getElementById('bill_date');
        el.valueAsDate = new Date(Date.UTC(arguments[0], arguments[1]-1, arguments[2]));
        el.dispatchEvent(new Event('input',  {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
    """, today.year, today.month, today.day)
    log(f"→ Bill date: {today.strftime('%d %b %Y')}")

# ─── Popup / modal handlers ───────────────────────────────────────────────────

def dismiss_any_alert(driver, timeout=3):
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        alert = driver.switch_to.alert
        log(f"⚠️  Alert: '{alert.text}' — dismissed")
        alert.accept()
        return True
    except (TimeoutException, NoAlertPresentException):
        return False


def dismiss_invoice_extraction_modal(driver, timeout=5):
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.XPATH, "//*[contains(text(),'Failed to extract invoice details')]"
            ))
        )
        log("⚠️  Invoice extraction modal — dismissing")
        for selector in [
            (By.XPATH, "//button[contains(translate(text(),'okOK','OKOK'),'OK')]"),
            (By.XPATH, "//button[contains(translate(text(),'closeClose','CLOSECLOSE'),'CLOSE')]"),
            (By.CSS_SELECTOR, ".modal.show .btn-primary"),
            (By.CSS_SELECTOR, ".modal.show .btn-secondary"),
            (By.CSS_SELECTOR, ".modal.show button.close"),
            (By.CSS_SELECTOR, ".modal.show [data-dismiss='modal']"),
        ]:
            try:
                btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable(selector))
                driver.execute_script("arguments[0].click();", btn)
                log("✅ Modal dismissed")
                return True
            except TimeoutException:
                continue
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        return True
    except TimeoutException:
        return False


def handle_duplicate_popup(driver, timeout=5):
    try:
        btn = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.ID, "btnYes")))
        driver.execute_script("arguments[0].click();", btn)
        log("⚠️  Duplicate popup — confirmed YES")
        time.sleep(0.5)
        return True
    except TimeoutException:
        pass
    try:
        btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((
            By.XPATH,
            "//button[contains(translate(text(),'yes','YES'),'YES') "
            "or contains(translate(text(),'confirm','CONFIRM'),'CONFIRM')]"
        )))
        driver.execute_script("arguments[0].click();", btn)
        log("⚠️  Duplicate popup (fallback) — confirmed YES")
        time.sleep(0.5)
        return True
    except TimeoutException:
        return False

# ─── Bill upload ──────────────────────────────────────────────────────────────

def upload_bill(driver, local_file_path):
    if not local_file_path:
        log("⚠️  No bill file — skipping upload")
        return
    try:
        upload_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "invoiceInput"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", upload_input)
        upload_input.send_keys(local_file_path)
        log(f"📎 Uploaded: {os.path.basename(local_file_path)}")
        time.sleep(2)
        dismiss_any_alert(driver, timeout=3)
        dismiss_invoice_extraction_modal(driver)
        WebDriverWait(driver, 10).until(
            lambda d: len(d.find_elements(By.CLASS_NAME, "inv-row")) > 0
        )
        log("✅ Bill row confirmed")
        driver.execute_script("window.setPrimaryInvoice(0)")
        dismiss_any_alert(driver, timeout=2)
    except Exception as e:
        log(f"❌ Bill upload failed: {e}")

# ─── Submit ───────────────────────────────────────────────────────────────────

def submit_expense(driver):
    try:
        btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.NAME, "submitButton")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        log("🟡 Submitting...")
    except Exception as e:
        log(f"❌ Submit click failed: {e}")
        return False

    try:
        WebDriverWait(driver, 10).until(lambda d: (
            d.find_elements(By.CSS_SELECTOR, ".notiny-theme-success")
            or d.find_elements(By.ID, "btnYes")
        ))
    except TimeoutException:
        log("⚠️  No response after submit")
        return False

    if driver.find_elements(By.ID, "btnYes"):
        handle_duplicate_popup(driver)
        try:
            WebDriverWait(driver, 8).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, ".notiny-theme-success"))
            )
            log("✅ Submitted (after duplicate confirmation)")
            return True
        except TimeoutException:
            return False

    if driver.find_elements(By.CSS_SELECTOR, ".notiny-theme-success"):
        log("✅ Expense submitted")
        return True

    return False
