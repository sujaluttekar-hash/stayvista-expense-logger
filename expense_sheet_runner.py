"""StayVista Expense Sheet Runner for manual GitHub Actions execution."""
import os, io, re, time, html, email, imaplib, tempfile, logging
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

logging.basicConfig(level=logging.INFO, format="%(message)s")
def log(msg):
    print(f"[LOG] {msg}", flush=True)
    logging.info(msg)

# ================= CONFIG =================
CREDENTIALS_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", str(Path(__file__).with_name("credentials.json")))
SHEET_ID = os.environ.get("EXPENSE_SHEET_ID", "1lpsy7p0PkXGGsa_NhiDGPBXv79pEcxOiucHl_ubqeQ8")
MAIN_TAB = os.environ.get("EXPENSE_MAIN_TAB", "Main")
ID_TAB = os.environ.get("EXPENSE_CREDENTIALS_TAB", "id")
LOGS_TAB = os.environ.get("EXPENSE_LOGS_TAB", "Logs")
DRIVE_FOLDER_ID = os.environ.get("EXPENSE_DRIVE_FOLDER_ID", "1EZF3Ye9DWWdLxw2iVxhjssNadOBfe1NS")
EXPENSE_SENDER = os.environ.get("EXPENSE_CONFIRMATION_SENDER", "customerhappiness@stayvista.com")
SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]

# ================= SERVICES =================
def get_services():
    if not os.path.isfile(CREDENTIALS_FILE):
        raise FileNotFoundError("Google service-account credentials missing. Configure GOOGLE_SERVICE_ACCOUNT_JSON in GitHub Secrets.")
    creds = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds), gspread.authorize(creds)

def read_credentials(gs_client):
    ws = gs_client.open_by_key(SHEET_ID).worksheet(ID_TAB)
    vals = [ws.cell(i, 2).value or "" for i in range(1, 5)]
    if not all(str(v).strip() for v in vals):
        raise ValueError("The id tab must contain admin username, admin password, Gmail address and Gmail app password in B1:B4.")
    return str(vals[0]).strip(), str(vals[1]).strip(), str(vals[2]).strip(), str(vals[3]).strip().replace(" ", "")

# ================= SHEET =================
def read_sheet_data(gs_client):
    ws = gs_client.open_by_key(SHEET_ID).worksheet(MAIN_TAB)
    values = ws.get_all_values()
    if not values: return [], ws
    headers, rows = values[0], []
    for row_idx, row in enumerate(values[1:], start=2):
        if not any(str(c).strip() for c in row): continue
        item = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        item["__sheet_row__"] = row_idx
        rows.append(item)
    log(f"📋 {len(rows)} row(s) in '{MAIN_TAB}'")
    return rows, ws

def delete_main_row(main_ws, row_number):
    main_ws.delete_rows(row_number)

LOGS_HEADERS = ["Expense ID", "Expense Type", "Expense Head", "Property Name", "Vendor Name", "Invoice Number", "Amount"]
def ensure_logs_tab(gs_client):
    ss = gs_client.open_by_key(SHEET_ID)
    try:
        ws = ss.worksheet(LOGS_TAB)
        if not ws.row_values(1): ws.append_row(LOGS_HEADERS)
        return ws
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=LOGS_TAB, rows=1000, cols=20)
        ws.append_row(LOGS_HEADERS)
        return ws

def write_log_row(logs_ws, row_data, email_data):
    total = 0.0
    for n in range(1, 7):
        raw = str(row_data.get(f"Amount_{n}", "") or "").strip().replace(",", "")
        if raw: total += float(raw)
    logs_ws.append_row([
        email_data.get("Expense ID", ""),
        email_data.get("Expense Type", row_data.get("Expense Type", "")),
        email_data.get("Expense Head", row_data.get("Expense Head", "")),
        row_data.get("Property Name", ""), row_data.get("Vendor Name", ""),
        row_data.get("Invoice Number", ""),
        email_data.get("Amount", str(round(total, 2)))
    ], value_input_option="USER_ENTERED")

# ================= IMAP =================
def connect_imap(email_addr, email_password):
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_addr, email_password)
        mail.select("inbox")
        return mail
    except Exception as exc:
        log(f"⚠️ Gmail connection unavailable: {exc}")
        return None

def get_latest_uid(mail):
    if not mail: return None
    try:
        _, data = mail.uid("search", None, "ALL")
        uids = data[0].split()
        return uids[-1] if uids else b"0"
    except Exception: return None

def parse_expense_email(msg_bytes):
    try:
        msg = email.message_from_bytes(msg_bytes)
        if "Expense Logged" not in str(msg.get("Subject", "")): return None
        match = re.search(r"\[(\d+)\]", str(msg.get("Subject", "")))
        result = {"Expense ID": match.group(1) if match else ""}
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    body = payload.decode(errors="ignore") if payload else ""
                    break
        else:
            payload = msg.get_payload(decode=True)
            body = payload.decode(errors="ignore") if payload else ""
        amount = re.search(r"Amount[\s\t:]+₹?([\d,]+\.?\d*)", body)
        if amount: result["Amount"] = amount.group(1).replace(",", "")
        return result
    except Exception: return None

def wait_for_confirmation_email(mail, baseline_uid, timeout_secs=60):
    if not mail or baseline_uid is None: return None
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        time.sleep(3)
        try:
            mail.noop()
            _, data = mail.uid("search", None, f'FROM "{EXPENSE_SENDER}" SUBJECT "Expense Logged"')
            new_uids = [u for u in data[0].split() if int(u) > int(baseline_uid)]
            if new_uids:
                _, messages = mail.uid("fetch", new_uids[-1], "(RFC822)")
                for part in messages:
                    if isinstance(part, tuple) and len(part) > 1:
                        parsed = parse_expense_email(part[1])
                        if parsed: return parsed
        except Exception: pass
    return None

# ================= DRIVE =================
def list_drive_files(drive_service):
    try:
        result = drive_service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
            fields="files(id,name,mimeType)", pageSize=100,
            includeItemsFromAllDrives=True, supportsAllDrives=True
        ).execute()
        return result.get("files", [])
    except Exception as exc:
        log(f"⚠️ Could not list Drive folder: {exc}")
        return []

def resolve_drive_file(drive_service, value, all_files):
    tmp = Path(tempfile.gettempdir()) / "sv_bills_new"
    tmp.mkdir(parents=True, exist_ok=True)
    value = str(value).strip()
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", value) or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", value)
    file_id = match.group(1) if match else None
    if not file_id and value:
        found = next((f for f in all_files if f.get("name", "").lower() == value.lower()), None)
        if found: file_id = found["id"]
    if not file_id:
        log("⚠️ No matching Drive file link/name; refusing to upload an arbitrary file.")
        return None
    try:
        meta = drive_service.files().get(fileId=file_id, supportsAllDrives=True, fields="name").execute()
        filename = Path(meta.get("name") or f"{file_id}.pdf").name
        local_path = tmp / filename
        request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(str(local_path), "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done: _, done = downloader.next_chunk()
        return str(local_path.resolve())
    except Exception as exc:
        log(f"❌ Drive download failed for {file_id}: {exc}")
        return None

# ================= DRIVER =================
def setup_driver():
    options = Options()
    options.add_argument("--headless=new")
    for arg in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080",
                "--disable-popup-blocking", "--disable-notifications", "--disable-blink-features=AutomationControlled"]:
        options.add_argument(arg)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_experimental_option("prefs", {
        "download.prompt_for_download": False,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False
    })
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(40)
    driver.implicitly_wait(0)
    return driver

# ================= LOGIN =================
def login(driver, username, password):
    wait = WebDriverWait(driver, 25)
    log("🔐 Logging in...")
    driver.get("https://admin.vistarooms.com/")
    wait.until(EC.element_to_be_clickable((By.NAME, "email"))).send_keys(username)
    driver.find_element(By.ID, "loginViaPasswordBtn").click()
    wait.until(EC.element_to_be_clickable((By.NAME, "password"))).send_keys(password)
    driver.find_element(By.ID, "loginViaPasswordBtn").click()
    try: wait.until(EC.invisibility_of_element_located((By.NAME, "email")))
    except TimeoutException: time.sleep(5)
    if "login" in driver.current_url.lower():
        raise RuntimeError("Login failed; verify credentials in the id sheet.")
    time.sleep(2)
    log("✅ Login successful")

def open_expense_page(driver):
    for _ in range(3):
        try:
            driver.get("https://admin.vistarooms.com/expenses/log")
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "select2-expensetype-container")))
            time.sleep(0.8)
            return
        except TimeoutException: time.sleep(1)
    raise RuntimeError("Could not load expense page")

# ================= SELECT2 =================
def _clean_option_text(txt):
    txt = html.unescape(txt)
    txt = re.sub(r"\s*\(VENDOR\)\s*$", "", txt, flags=re.IGNORECASE).strip()
    txt = re.sub(r"^\d+\s*-\s*", "", txt).strip()
    return txt.lower()

def _find_option(driver, value):
    options = driver.find_elements(By.CSS_SELECTOR, ".select2-results__option")
    candidates = []
    for option in options:
        try:
            cls, txt = option.get_attribute("class") or "", option.text.strip()
            if "loading-results" in cls or "select2-results__message" in cls or not txt: continue
            candidates.append((txt, option))
        except Exception: continue
    target = value.strip().lower()
    for txt, option in candidates:
        if _clean_option_text(txt) == target: return option
    for txt, option in candidates:
        if _clean_option_text(txt).startswith(target): return option
    for txt, option in candidates:
        if target in _clean_option_text(txt): return option
    return None

def select2(driver, container_id, value, strict=False, wait_after=0):
    wait = WebDriverWait(driver, 20)
    placeholders = {"", "select", "-- select --", "expense type", "expense head", "villa", "vendor", "booking id"}
    for attempt in range(2):
        try:
            container = wait.until(EC.presence_of_element_located((By.ID, container_id)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", container)
            try: container.click()
            except Exception: driver.execute_script("arguments[0].click();", container)
            time.sleep(0.3)
            search = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".select2-container--open .select2-search__field")))
            search.clear()
            for char in value:
                search.send_keys(char)
                time.sleep(0.03)
            time.sleep(0.8)
            try:
                wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".select2-container--open .select2-results__option:not(.loading-results):not(.select2-results__message)")))
            except TimeoutException: pass
            option = _find_option(driver, value)
            if option is None and strict:
                raise RuntimeError(f"STRICT_FAIL: '{value}' not found in {container_id}")
            if option is not None:
                try: option.click()
                except Exception: driver.execute_script("arguments[0].click();", option)
            else: search.send_keys(Keys.RETURN)
            time.sleep(0.4 + wait_after)
            rendered = driver.find_element(By.ID, container_id).text.strip()
            if rendered and rendered.lower() not in placeholders:
                log(f"✅ select2 → '{rendered}'")
                return
            if strict: raise RuntimeError(f"STRICT_FAIL: '{value}' was not selected")
        except Exception as exc:
            if "STRICT_FAIL" in str(exc) or attempt == 1: raise
            time.sleep(0.5)

# ================= COMMENT / INPUT =================
def fill_comment(driver, value):
    if not value or not value.strip(): return
    el = WebDriverWait(driver, 12).until(EC.visibility_of_element_located((By.ID, "expense_head_categoriespart")))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        el.click()
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(value)
    except Exception:
        driver.execute_script("""
            const el=arguments[0], val=arguments[1];
            el.focus(); el.value=val;
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
            el.dispatchEvent(new Event('blur',{bubbles:true}));
        """, el, value)

def fill_input(driver, field_id, value):
    el = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.ID, field_id)))
    el.clear()
    el.send_keys(str(value))

# ================= COST / PAYMENT =================
def select_cost_bearer(driver, value):
    select = Select(WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.NAME, "cost_bearer"))))
    choices = [str(value).strip(), "VISTA", "SV Managed", "OWNER"]
    for choice in dict.fromkeys(c for c in choices if c):
        try:
            select.select_by_visible_text(choice)
            return
        except Exception: pass
    raise RuntimeError(f"Could not select cost bearer: {value}")

def select_payment_status(driver):
    Select(WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.ID, "payment_status")))).select_by_value("to_be_paid")

# ================= LINE ITEMS =================
def fill_all_line_items(driver, line_items):
    wait = WebDriverWait(driver, 15)
    for idx, item in enumerate(line_items):
        n = idx + 1
        qty = wait.until(EC.presence_of_element_located((By.XPATH, f"//*[@id='group-gst-range-div']/div[{n}]/div[1]/div/input")))
        qty.clear(); qty.send_keys(str(item["qty"]))
        amount = wait.until(EC.presence_of_element_located((By.XPATH, f"//*[@id='group-gst-range-div']/div[{n}]/div[2]/div/input")))
        amount.clear(); amount.send_keys(str(item["amount"]))
        gst = Select(wait.until(EC.presence_of_element_located((By.XPATH, f"//*[@id='group-gst-range-div']/div[{n}]/div[3]/div/select"))))
        gst.select_by_visible_text(str(int(float(item["gst"]))))
        if idx < len(line_items) - 1:
            add = wait.until(EC.presence_of_element_located((By.NAME, "add")))
            driver.execute_script("arguments[0].click();", add)
            time.sleep(0.5)

# ================= ALERTS =================
def dismiss_any_alert(driver, timeout=2):
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        driver.switch_to.alert.accept()
        return True
    except (TimeoutException, NoAlertPresentException): return False

def dismiss_invoice_modal(driver, timeout=4):
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Failed to extract invoice details')]")))
        for selector in [(By.XPATH, "//button[contains(text(),'OK')]"), (By.CSS_SELECTOR, ".modal.show .btn-primary")]:
            try:
                button = WebDriverWait(driver, 1).until(EC.element_to_be_clickable(selector))
                driver.execute_script("arguments[0].click();", button)
                return True
            except Exception: pass
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        return True
    except TimeoutException: return False

def handle_duplicate_popup(driver, timeout=5):
    try:
        button = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.ID, "btnYes")))
        driver.execute_script("arguments[0].click();", button)
        return True
    except TimeoutException: return False

# ================= UPLOAD BILL =================
def upload_bills(driver, local_file_path):
    if not local_file_path:
        log("⚠️ No bill file resolved; continuing without upload.")
        return False
    try:
        upload_input = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.ID, "invoiceInput")))
        driver.execute_script("arguments[0].style.display='block';arguments[0].style.visibility='visible';arguments[0].style.opacity='1';arguments[0].removeAttribute('disabled');", upload_input)
        upload_input.send_keys(str(Path(local_file_path).resolve()))
        time.sleep(2)
        if driver.execute_script("return arguments[0].files.length;", upload_input) == 0: return False
        dismiss_any_alert(driver); dismiss_invoice_modal(driver)
        WebDriverWait(driver, 10).until(lambda d: len(d.find_elements(By.CLASS_NAME, "inv-row")) > 0)
        driver.execute_script("window.setPrimaryInvoice(0)")
        time.sleep(0.5); dismiss_any_alert(driver)
        return True
    except Exception as exc:
        log(f"❌ Bill upload failed: {exc}")
        return False

# ================= SUBMIT =================
def submit_expense(driver):
    try:
        button = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.NAME, "submitButton")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});arguments[0].click();", button)
        log("🟡 Submit clicked")
    except Exception as exc:
        log(f"❌ Submit failed: {exc}")
        return False
    try:
        WebDriverWait(driver, 10, poll_frequency=0.3).until(lambda d: d.find_elements(By.CSS_SELECTOR, ".notiny-theme-success") or d.find_elements(By.ID, "btnYes"))
    except TimeoutException: return False
    if driver.find_elements(By.ID, "btnYes"):
        handle_duplicate_popup(driver)
        try:
            WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".notiny-theme-success")))
            return True
        except TimeoutException: return False
    return bool(driver.find_elements(By.CSS_SELECTOR, ".notiny-theme-success"))

# ================= PARSE HELPERS =================
def parse_line_items(row):
    items = []
    for n in range(1, 7):
        amount = str(row.get(f"Amount_{n}", "")).strip()
        if not amount or amount == "0": continue
        items.append({
            "amount": amount,
            "qty": str(row.get(f"Quantity_{n}", "1")).strip() or "1",
            "gst": str(row.get(f"GST_{n}", "0")).strip() or "0"
        })
    return items

def parse_bill_date(date_str):
    val = str(date_str).strip()
    if not val:
        today = datetime.now()
        return today.year, today.month, today.day
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(val, fmt)
            return d.year, d.month, d.day
        except ValueError: pass
    raise ValueError(f"Unsupported Bill_Date format: {val}")

# ================= PROCESS ONE ROW =================
def log_expense(driver, drive_service, row, all_drive_files, mail, logs_ws, main_ws):
    expense_type = str(row.get("Expense Type", "")).strip()
    expense_head = str(row.get("Expense Head", "")).strip()
    comment = str(row.get("Comment", "")).strip()
    property_name = str(row.get("Property Name", "")).strip()
    cost_bearer = str(row.get("Cost Bearer", "")).strip()
    vendor_name = str(row.get("Vendor Name", "")).strip()
    invoice_num = str(row.get("Invoice Number", "")).strip() or "01"
    drive_cell = str(row.get("Drive", "")).strip()
    bill_date_raw = str(row.get("Bill_Date", "")).strip()
    line_items = parse_line_items(row)
    if not expense_type: return False

    log(f"\n📝 {expense_type} | {expense_head} | {property_name}")
    baseline_uid = get_latest_uid(mail)
    select2(driver, "select2-expensetype-container", expense_type)
    time.sleep(0.3)
    select2(driver, "select2-expenshead-container", expense_head)
    time.sleep(0.3)
    WebDriverWait(driver, 15).until(EC.visibility_of_element_located((By.ID, "expense_head_categoriespart")))
    fill_comment(driver, comment)
    select2(driver, "select2-expense_villa_list-container", property_name, strict=True, wait_after=5)
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.NAME, "cost_bearer")))
    select_cost_bearer(driver, cost_bearer)
    select_payment_status(driver)
    select2(driver, "select2-vendor_name-container", vendor_name)
    fill_input(driver, "invoice_number", invoice_num)
    if line_items: fill_all_line_items(driver, line_items)

    year, month, day = parse_bill_date(bill_date_raw)
    driver.execute_script("""
        const el=document.getElementById('bill_date');
        if(el){
            el.valueAsDate=new Date(Date.UTC(arguments[0],arguments[1]-1,arguments[2]));
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
        }
    """, year, month, day)

    local_bill = resolve_drive_file(drive_service, drive_cell, all_drive_files)
    if local_bill: upload_bills(driver, local_bill)

    if submit_expense(driver):
        log(f"✅ Done: {expense_type}")
        email_data = wait_for_confirmation_email(mail, baseline_uid)
        write_log_row(logs_ws, row, email_data or {
            "Expense ID": "SUBMITTED", "Expense Type": expense_type,
            "Expense Head": expense_head, "Amount": ""
        })
        # Processing rows bottom-up keeps remaining source row numbers stable.
        delete_main_row(main_ws, row["__sheet_row__"])
        return True

    log(f"❌ Failed: {expense_type}")
    return False

# ================= MAIN =================
def main():
    drive_service, gs_client = get_services()
    log("✅ Google services ready")
    username, password, email_addr, email_password = read_credentials(gs_client)
    all_drive_files = list_drive_files(drive_service)
    rows, main_ws = read_sheet_data(gs_client)
    logs_ws = ensure_logs_tab(gs_client)
    mail = connect_imap(email_addr, email_password)
    log(f"📋 {len(rows)} rows to process")
    if not rows:
        if mail: mail.logout()
        log("Nothing to process.")
        return

    # Delete source rows safely by processing from the bottom upwards.
    rows.sort(key=lambda row: row["__sheet_row__"], reverse=True)
    driver = None
    results = {"success": 0, "failed": 0, "skipped": 0}
    try:
        driver = setup_driver()
        login(driver, username, password)
        for idx, row in enumerate(rows, start=1):
            expense_type = str(row.get("Expense Type", "")).strip()
            if not expense_type:
                results["skipped"] += 1
                continue
            log(f"\n🔄 Row {idx}/{len(rows)}: {expense_type}")
            try:
                open_expense_page(driver)
                if log_expense(driver, drive_service, row, all_drive_files, mail, logs_ws, main_ws):
                    results["success"] += 1
                else:
                    results["failed"] += 1
                time.sleep(0.5)
            except Exception as exc:
                log(f"❌ Row failed: {exc}")
                results["failed"] += 1
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass
        if mail:
            try: mail.logout()
            except Exception: pass
        log("\n" + "=" * 50)
        log(f"DONE ✅ {results['success']} ok  ❌ {results['failed']} failed  ⏭ {results['skipped']} skipped")

    if results["failed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
