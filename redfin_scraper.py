import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright


@dataclass
class HomeCard:
    title: Optional[str]
    price: Optional[str]
    beds: Optional[str]
    baths: Optional[str]
    sqft: Optional[str]
    address: Optional[str]
    detail_url: str
    image_url: Optional[str]


@dataclass
class PropertyDetails:
    status_badge: Optional[str]
    price: Optional[str]
    monthly_payment: Optional[str]
    beds: Optional[str]
    baths: Optional[str]
    sqft: Optional[str]
    address: Optional[str]
    on_redfin: Optional[str]
    views: Optional[str]
    favorites: Optional[str]
    description: Optional[str]
    key_details: Dict[str, str]
    agent_name: Optional[str]
    agent_broker: Optional[str]
    agent_profile_url: Optional[str]
    listing_updated: Optional[str]
    redfin_checked: Optional[str]
    mls_source: Optional[str]
    mls_id: Optional[str]


class RedfinScraper:
    def __init__(self) -> None:
        self.base_url = "https://www.redfin.com"
        self.list_url = "https://www.redfin.com/"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/118.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def scrape(self, limit: int = 10) -> List[Dict[str, Any]]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = self._create_context(p, browser)
            try:
                logging.info("Collecting up to %s home cards from %s", limit, self.list_url)
                home_cards = self._collect_home_cards(context, limit)
                logging.info("Found %s home cards", len(home_cards))
                results: List[Dict[str, Any]] = []

                for idx, card in enumerate(home_cards, start=1):
                    logging.info("Scraping detail page %s/%s: %s", idx, len(home_cards), card.detail_url)
                    detail = self._scrape_property_detail(context, card.detail_url)
                    results.append(
                        {
                            "card": asdict(card),
                            "detail": asdict(detail),
                        }
                    )
                return results
            finally:
                context.close()
                browser.close()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _create_context(self, playwright: Playwright, browser) -> Any:
        context = browser.new_context(
            user_agent=self.session.headers["User-Agent"],
            locale="en-US",
            timezone_id="America/Chicago",
            viewport={"width": 1440, "height": 900},
        )

        # Basic stealth tweaks to avoid bot challenges.
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        context.add_init_script("window.chrome = {runtime: {}};")
        context.add_init_script("Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});")
        context.add_init_script("Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});")
        return context

    def _collect_home_cards(self, context, limit: int) -> List[HomeCard]:
        page = context.new_page()
        try:
            page.goto(self.list_url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_selector("div[data-rf-test-name='basicNode-homeCard']", timeout=15_000)
            except PlaywrightTimeoutError:
                logging.warning("Timed out waiting for home cards to appear on the homepage.")

            locator = page.locator("div[data-rf-test-name='basicNode-homeCard']")

            # Scroll until we have enough cards or stop after a few tries.
            attempts = 0
            while locator.count() < limit and attempts < 10:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(1000)
                attempts += 1

            cards: List[HomeCard] = []
            available = locator.count()
            if available == 0:
                raise RuntimeError("No home cards loaded on the Redfin homepage.")

            logging.info("Homepage rendered %s cards; using %s", available, min(limit, available))
            for index in range(min(limit, available)):
                card_locator = locator.nth(index)
                cards.append(self._parse_home_card(card_locator))

            return cards
        finally:
            page.close()

    def _parse_home_card(self, card_locator) -> HomeCard:
        def get_text(selector: str) -> Optional[str]:
            loc = card_locator.locator(selector)
            if loc.count():
                return loc.first.inner_text().strip()
            return None

        def get_attr(selector: str, attribute: str) -> Optional[str]:
            loc = card_locator.locator(selector)
            if loc.count():
                return loc.first.get_attribute(attribute)
            return None

        price = get_text(".bp-Homecard__Price--value")
        beds = get_text(".bp-Homecard__Stats--beds")
        baths = get_text(".bp-Homecard__Stats--baths")
        sqft = get_text(".bp-Homecard__Stats--sqft")
        address = get_text("a.bp-Homecard__Address")
        title = card_locator.get_attribute("title")
        detail_path = get_attr("a.bp-Homecard__Address", "href")
        image_url = get_attr(".bp-Homecard__Photo img", "src")

        if not detail_path:
            raise RuntimeError("Unable to locate detail URL for a home card.")

        detail_url = detail_path if detail_path.startswith("http") else f"{self.base_url}{detail_path}"

        return HomeCard(
            title=title.strip() if title else address,
            price=self._clean_card_stat(price),
            beds=self._clean_card_stat(beds),
            baths=self._clean_card_stat(baths),
            sqft=self._clean_card_stat(sqft),
            address=address,
            detail_url=detail_url,
            image_url=image_url,
        )

    def _scrape_property_detail(self, context, url: str) -> PropertyDetails:
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            if not response or response.status >= 400:
                raise RuntimeError(f"Failed to load detail page ({response.status if response else 'no response'}): {url}")

            try:
                page.wait_for_selector("[data-rf-test-id='abp-price']", timeout=15_000)
            except PlaywrightTimeoutError:
                logging.warning("Price section did not load in time for %s", url)

            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = ""

            # Expand remarks if the toggle is present.
            try:
                show_more = page.locator("button:has-text('Show more')")
                if show_more.count() and show_more.first.is_enabled():
                    show_more.first.click()
                    page.wait_for_timeout(250)
            except PlaywrightTimeoutError:
                pass
            except Exception:
                pass

            # Collect primary stats via locators for accuracy.
            def stat_text(selector: str) -> Optional[str]:
                loc = page.locator(selector)
                try:
                    text_value = loc.first.inner_text(timeout=0)
                except PlaywrightTimeoutError:
                    return None
                except Exception:
                    return None
                return self._clean_card_stat(text_value)

            status_badge = self._extract_status_badge(page)
            price = stat_text("[data-rf-test-id='abp-price'] .statsValue.price")
            monthly = stat_text("[data-rf-test-id='abp-monthly-payment-entry-point-estimate']")
            beds = stat_text("[data-rf-test-id='abp-beds']")
            baths = stat_text("[data-rf-test-id='abp-baths']")
            sqft = stat_text("[data-rf-test-id='abp-sqFt']")
            address = stat_text("[data-rf-test-id='abp-homeinfo-homeaddress']")

            # Parse the house-info section with BeautifulSoup for details list.
            house_info_html = page.evaluate(
                "() => document.querySelector('[data-rf-test-id=\"house-info\"]').outerHTML"
            )
            house_info = BeautifulSoup(house_info_html, "lxml")

            description_node = house_info.select_one("#marketing-remarks-scroll")
            description = description_node.get_text(" ", strip=True) if description_node else None

            key_details: Dict[str, str] = {}
            for row in house_info.select(".KeyDetailsTable .keyDetails-row"):
                label = row.select_one(".valueType")
                value = row.select_one(".valueText")
                if label and value:
                    label_text = label.get_text(strip=True)
                    if label_text.lower() == "on redfin":
                        continue
                    key_details[label_text] = value.get_text(" ", strip=True)

            agent_name = agent_broker = agent_profile_url = None
            agent_section = house_info.select_one("[data-rf-test-id='agentInfoItem-redfinAgentDisplay']")
            if agent_section:
                name_node = agent_section.select_one(".agent-basic-details--heading a")
                broker_node = agent_section.select_one(".agent-basic-details--broker span")
                if name_node:
                    agent_name = name_node.get_text(strip=True)
                    agent_profile_url = name_node.get("href")
                    if agent_profile_url and not agent_profile_url.startswith("http"):
                        agent_profile_url = f"{self.base_url}{agent_profile_url}"
                if broker_node:
                    agent_broker = broker_node.get_text(" ", strip=True)

            on_redfin = views = favorites = None
            stats_pattern = re.compile(
                r"(?P<on_redfin>[0-9,]+\s+\w+\s+on\s+Redfin)\s•\s(?P<views>[0-9,]+\sviews)\s•\s(?P<favorites>[0-9,]+\sfavorites)",
                re.IGNORECASE,
            )
            stats_match = stats_pattern.search(body_text)
            if not stats_match:
                try:
                    page.wait_for_timeout(800)
                    body_text = page.inner_text("body")
                    stats_match = stats_pattern.search(body_text)
                except Exception:
                    pass
            if stats_match:
                on_redfin = self._clean_card_stat(stats_match.group("on_redfin"))
                views = self._clean_card_stat(stats_match.group("views"))
                favorites = self._clean_card_stat(stats_match.group("favorites"))
            else:
                snippet_index = body_text.lower().find("on redfin")
                if snippet_index != -1:
                    snippet = body_text[max(0, snippet_index - 40): snippet_index + 80]
                    logging.debug("Engagement snippet without match for %s: %r", url, snippet)
                else:
                    logging.debug("Engagement text absent for %s", url)

            listing_updated = redfin_checked = mls_source = mls_id = None
            listing_info = house_info.select_one(".listingInfoSection")
            if listing_info:
                time_node = listing_info.select_one(".data-quality time")
                redfin_checked_node = listing_info.select_one(".data-quality a")
                source_node = listing_info.select_one(".ListingSource--dataSourceName")
                mls_node = listing_info.select_one(".ListingSource--mlsId")

                if time_node:
                    listing_updated = f"Listing updated: {self._clean_card_stat(time_node.get_text(strip=True))}"
                if redfin_checked_node:
                    redfin_checked = f"Redfin checked: {self._clean_card_stat(redfin_checked_node.get_text(strip=True))}"
                if source_node:
                    mls_source = f"Source: {self._clean_card_stat(source_node.get_text(strip=True))}"
                if mls_node:
                    mls_id = f"MLS ID: {self._clean_card_stat(mls_node.get_text(strip=True).lstrip('#'))}"

            return PropertyDetails(
                status_badge=status_badge,
                price=price,
                monthly_payment=monthly,
                beds=beds,
                baths=baths,
                sqft=sqft,
                address=address,
                description=description,
                key_details=key_details,
                agent_name=agent_name,
                agent_broker=agent_broker,
                agent_profile_url=agent_profile_url,
                on_redfin=on_redfin,
                views=views,
                favorites=favorites,
                listing_updated=listing_updated,
                redfin_checked=redfin_checked,
                mls_source=mls_source,
                mls_id=mls_id,
            )
        finally:
            page.close()

    @staticmethod
    def _clean_card_stat(value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        return " ".join(value.split())

    def _extract_status_badge(self, page) -> Optional[str]:
        script = r"""() => Array.from(
            document.querySelectorAll('[class*="status" i]')
        ).map(el => el.textContent.trim()).filter(Boolean)"""
        try:
            candidates = page.evaluate(script)
        except Exception:
            candidates = []

        preferred_keywords = ("for sale", "pending", "sold", "contingent", "active", "off market", "new listing")

        for raw in candidates:
            cleaned = self._clean_card_stat(raw)
            if not cleaned:
                continue
            if len(cleaned) > 80:
                continue
            lowered = cleaned.lower()
            if any(keyword in lowered for keyword in preferred_keywords):
                return cleaned

        # Fall back to plain text search inside the body if nothing obvious was found.
        try:
            body_text = page.inner_text("body")
        except Exception:
            body_text = ""

        for line in body_text.splitlines():
            cleaned = self._clean_card_stat(line)
            if cleaned:
                lowered = cleaned.lower()
                if any(keyword in lowered for keyword in preferred_keywords):
                    return cleaned
        return None


def main() -> None:
    scraper = RedfinScraper()
    data = scraper.scrape(limit=10)
    numbered = []
    for idx, entry in enumerate(data, start=1):
        numbered.append({"id": idx, **entry})

    logging.info("Scraped %s property records", len(numbered))
    output_path = "redfin_results.json"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(numbered, fh, indent=2, ensure_ascii=False)

    logging.info("Saved data to %s", output_path)
    print(json.dumps(numbered, indent=2, ensure_ascii=False))
    print(f"\nSaved {len(numbered)} records to {output_path}")


if __name__ == "__main__":
    main()


