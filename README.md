# Redfin Scraper

Automated scraper for collecting property data from Redfin using Playwright.

## Setup and Run Instructions

1. Clone the project, then open Terminal and navigate to the project folder:
   ```bash
   cd Redfine-Scraper
   ```
2. Create and activate a virtual environment to keep dependencies isolated:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install required dependencies from the `requirements.txt` file:
   ```bash
   pip install -r requirements.txt
   ```
4. Install the Playwright browser bundle (one-time setup):
   ```bash
   playwright install chromium
   ```
5. Run the scraper:
   ```bash
   python redfin_scraper.py
   ```
