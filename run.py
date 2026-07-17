import os
import sys

def main():
    print("=" * 60)
    print("           GeMSentry: Smart RFP Acquisition System")
    print("=" * 60)

    # 1. Dependency Checks
    try:
        import playwright
        import bs4
        import pypdf
        import flask
    except ImportError as e:
        print(f"Error: Missing Python dependencies: {e}")
        print("Please run: pip install -r requirements.txt")
        sys.exit(1)

    # 2. Start the local server
    print("\nStarting GeMSentry Dashboard and Scraper Backend Server...")
    try:
        from app import app
        app.run(host="127.0.0.1", port=5000, debug=False)
    except Exception as e:
        print(f"\nServer failed to start: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
