import os
import scraper
from playwright.sync_api import sync_playwright

print("==========================================================")
print("             RFP PDF Download and Parser Test")
print("==========================================================")

bid_no = "GEM_2026_B_7553726"
pdf_url = "https://bidplus.gem.gov.in/showbidDocument/GEM/2026/B/7553726"
save_path = f"tenders/downloads/{bid_no}.pdf"

os.makedirs("tenders/downloads", exist_ok=True)

print("Launching Playwright with stealth settings...")
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        accept_downloads=True
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    page = context.new_page()
    # Visit search query page to mimic exact user flow and referrer headers
    search_url = "https://bidplus.gem.gov.in/all-bids?bid_number=&items_per_page=&search_under=&search=PSU"
    print(f"Navigating to GeM search query page: {search_url}...")
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"Warning: Search page load: {e}")
        
    page.close()

    # Now download using the same context
    print(f"Requesting document PDF URL: {pdf_url}...")
    success = scraper.download_rfp_pdf(context, pdf_url, save_path)
    browser.close()

if success and os.path.exists(save_path) and os.path.getsize(save_path) > 0:
    print(f"\nPDF downloaded successfully to: {save_path} ({os.path.getsize(save_path)} bytes)")
    
    # 2. Parse and evaluate PDF
    print("\nRunning PDF evaluation parser...")
    analysis = scraper.analyze_rfp_pdf(save_path)
    
    if analysis:
        print("\n================== Evaluation Report ==================")
        print(f"Score: {analysis['score']}/10")
        print(f"EMD Amount: {analysis['emd_amount']}")
        print(f"EMD Status: {analysis['emd_status']}")
        print(f"Startup Relaxation: {analysis['startup_exemption']}")
        print(f"MSE Relaxation: {analysis['mse_exemption']}")
        print(f"Pre-Bid Meeting Required: {analysis['pre_bid_required']}")
        print(f"Pre-Bid Meeting Date: {analysis['pre_bid_date']}")
        print(f"ePBG Guarantee Required: {analysis['epbg_required']}")
        print(f"ePBG Guarantee Percentage: {analysis['epbg_percentage']}")
        print("\nReasons for Score:")
        for r in analysis["reasons"]:
            print(f"  - {r}")
        print("=======================================================")
    else:
        print("Error: Evaluation failed.")
else:
    print(f"Error: Download failed or PDF is empty. Success status: {success}")

# Clean up
if os.path.exists(save_path):
    os.remove(save_path)
    print("\nTemporary PDF file cleaned up.")
