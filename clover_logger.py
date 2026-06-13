"""
StayVista Expense Logger — Clover
GST: 18%  |  Sheet: Vista Export  |  Drive: two Clover folders (searches both)
"""

import time
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

from core import (
    log, get_services, read_sheet_data, already_processed,
    setup_driver, login, open_expense_page,
    select2, fill_comment, fill_input,
    select_cost_bearer, select_payment_status,
    fill_line_item, set_bill_date, upload_bill, submit_expense,
    download_bill,
)

# ─── Sheet / Drive config ─────────────────────────────────────────────────────

SHEET_ID      = "1CQwVe3Z96AuHa1IcYdn48PMFX-FMYHksEYLBzOYjUWI"
SHEET_TAB     = "Vista Export"
DRIVE_FOLDERS = [
    "1O0pPzhdrqQ-dQZYM1v1D346UrBO9Rgnb",  # primary (Nashik)
    "1PJ2UfATRhfipzX3zlHubH7USAHT8P30b",  # secondary (other cities)
]

COL_BOOKING_ID  = "Booking_id"
COL_VENDOR      = "Vendor"
COL_PROPERTY    = "property name"
COL_AMOUNT      = "base amount"
COL_INVOICE     = "invoice no"
COL_COMMENT     = "subexpense"
COL_EXPENSE_ID  = "Expense ID"

# ─── Single row processor ─────────────────────────────────────────────────────

def log_clover_expense(driver, drive_service, row):
    booking_id    = str(row.get(COL_BOOKING_ID, "")).strip()
    vendor        = str(row.get(COL_VENDOR,      "")).strip()
    property_name = str(row.get(COL_PROPERTY,    "")).strip()
    amount        = str(row.get(COL_AMOUNT,       "")).strip()
    invoice_num   = str(row.get(COL_INVOICE,      "01")).strip() or "01"
    comment       = str(row.get(COL_COMMENT,      "")).strip()

    if not booking_id:
        log("⚠️  No booking ID — skipping row")
        return False

    log(f"\n📝 [CLOVER] Booking: {booking_id}")

    wait = WebDriverWait(driver, 20)
    select2(driver, "select2-expensetype-container", "f&b")
    select2(driver, "select2-expensehead-container", "Cook Arranged")
    wait.until(EC.visibility_of_element_located((By.ID, "expense_head_categoriespart")))
    time.sleep(1)

    fill_comment(driver, comment)
    select2(driver, "select2-expense_villa_list-container", property_name)
    select_cost_bearer(driver)
    select_payment_status(driver)
    select2(driver, "select2-vendor_name-container", vendor)
    fill_input(driver, "invoice_number", invoice_num)

    try:
        select2(driver, "select2-bookingid_expenses-container", booking_id)
    except Exception:
        log("⚠️  Booking ID select skipped")

    fill_line_item(driver, "1", amount, gst=18)
    set_bill_date(driver)

    # Search both Drive folders — whichever has the bill wins
    local_bill = download_bill(drive_service, DRIVE_FOLDERS, booking_id)
    upload_bill(driver, local_bill)

    success = submit_expense(driver)
    if success:
        log(f"✅ Done [CLOVER]: {booking_id}")
    else:
        log(f"❌ Failed [CLOVER]: {booking_id}")
        try:
            driver.save_screenshot(f"error_clover_{booking_id}.png")
        except Exception:
            pass

    return success

# ─── Entry point ──────────────────────────────────────────────────────────────

def run():
    drive_service, gs_client = get_services()
    rows = read_sheet_data(gs_client, SHEET_ID, SHEET_TAB)

    pending = [r for r in rows if not already_processed(r, COL_EXPENSE_ID)]
    log(f"\n========== CLOVER: {len(pending)} pending / {len(rows)} total ==========")

    if not pending:
        log("✅ Nothing to process — all rows already have an Expense ID")
        return

    driver = setup_driver(headless=True)
    try:
        login(driver)
        for i, row in enumerate(pending):
            log(f"\n🔄 Row {i+1}/{len(pending)}")
            try:
                open_expense_page(driver)
                time.sleep(1)
                log_clover_expense(driver, drive_service, row)
                time.sleep(1)
            except Exception as e:
                log(f"❌ Error on row {i+1}: {e}")
                continue
    finally:
        driver.quit()
        log("🔌 Browser closed")

if __name__ == "__main__":
    run()
