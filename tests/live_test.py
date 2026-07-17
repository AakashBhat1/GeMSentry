import os
import shutil
import scraper

print("==========================================================")
print("             Live Scraper & Parser Validation")
print("==========================================================")

# 1. Backup current metadata
files = ["metadata.csv", "metadata.json", "metadata.js"]
backup_dir = "tenders_backup"
os.makedirs(backup_dir, exist_ok=True)

print("Backing up current database files...")
for f in files:
    src = os.path.join("tenders", f)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(backup_dir, f))

# 2. Clear current metadata to run a fresh scrape
print("Clearing current database to force live discovery...")
for f in files:
    src = os.path.join("tenders", f)
    if os.path.exists(src):
        os.remove(src)

try:
    # 3. Run scrape for 'drone'
    print("\nRunning scrape for 'drone'...")
    tenders, new_count = scraper.scrape(selected_keywords=["drone"], max_pages=1)
    
    print(f"\nScrape finished. Discovered {new_count} tenders.")
    
    # 4. Display results
    print("\n--- Scraped Tenders & Scores ---")
    for t in tenders:
        print(f"\nBid: {t['bid_no']}")
        print(f"Title: {t['title']}")
        print(f"Start Date: {t['start_date']} | End Date: {t['end_date']}")
        print(f"Downloaded: {t['downloaded']} | Status: {t['status']}")
        
        analysis = t.get("analysis")
        if analysis:
            print(f"Score: {analysis['score']}/10")
            print(f"EMD Status: {analysis['emd_status']}")
            print(f"Exemptions: Startup: {analysis['startup_exemption']}, MSE: {analysis['mse_exemption']}")
            print(f"Reasons:")
            for r in analysis.get("reasons", []):
                print(f"  - {r}")
                
except Exception as e:
    print(f"\nLive scrape failed: {e}")
finally:
    # 5. Restore backup
    print("\nRestoring database backups...")
    for f in files:
        src = os.path.join(backup_dir, f)
        dest = os.path.join("tenders", f)
        if os.path.exists(src):
            shutil.copy(src, dest)
            
    # Clean up backup dir
    shutil.rmtree(backup_dir, ignore_errors=True)
    print("Database restored.")
