"""
Rightmove Property Scraper - Playwright Edition
================================================
Production-ready scraper using headless Chromium to handle client-side rendering.

Rightmove renders content dynamically, so we use Playwright to:
1. Launch headless browser
2. Navigate to property page
3. Wait for gallery to fully load
4. Extract PAGE_MODEL from rendered page
5. Parse high-resolution images and metadata

Usage:
    from rightmove_scraper import scrape_rightmove_listing

    result = await scrape_rightmove_listing("https://www.rightmove.co.uk/properties/123456789")
    print(result.images)
"""

import re
import json
import asyncio
from typing import Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[WARNING] Playwright not available, will use httpx fallback")

import httpx


@dataclass
class PropertyImage:
    """A single property image with metadata."""
    id: int
    url: str
    url_high_res: str
    room_type: str
    caption: str = ""
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class PropertyListing:
    """Complete property listing data."""
    url: str
    property_id: str
    address: str
    price: str
    price_qualifier: str = ""
    property_type: str = ""
    bedrooms: int = 0
    bathrooms: int = 0
    images: list[PropertyImage] = field(default_factory=list)
    floorplan_urls: list[str] = field(default_factory=list)
    agent_name: str = ""
    agent_phone: str = ""
    description: str = ""
    features: list[str] = field(default_factory=list)


# Room type detection based on image captions
ROOM_KEYWORDS = {
    'Kitchen': ['kitchen', 'cooking', 'culinary', 'breakfast'],
    'Living Room': ['living', 'lounge', 'sitting', 'reception', 'drawing'],
    'Bedroom': ['bedroom', 'bed room', 'master bed', 'guest bed', 'sleep'],
    'Bathroom': ['bathroom', 'bath room', 'shower', 'wc', 'toilet', 'en-suite', 'ensuite'],
    'Garden': ['garden', 'outdoor', 'patio', 'terrace', 'yard', 'lawn'],
    'Exterior': ['exterior', 'front', 'outside', 'facade', 'entrance'],
    'Dining Room': ['dining', 'dinner', 'eating'],
    'Study': ['study', 'office', 'home office', 'work'],
    'Hallway': ['hall', 'hallway', 'entrance hall', 'corridor'],
    'Utility': ['utility', 'laundry', 'boot room'],
    'Conservatory': ['conservatory', 'sun room', 'sunroom'],
    'Garage': ['garage', 'parking', 'car port'],
    'Basement': ['basement', 'cellar'],
    'Attic': ['attic', 'loft'],
}


def detect_room_type(caption: str, index: int, total_images: int) -> str:
    """
    Detect room type from image caption.
    Falls back to smart defaults if caption is just a filename.
    """
    caption_lower = caption.lower()

    # Check if caption is actually descriptive (not just a filename)
    is_filename = (
        caption_lower.startswith('_dsc') or
        caption_lower.startswith('img_') or
        caption_lower.startswith('dsc_') or
        caption_lower.startswith('photo') or
        caption_lower.endswith('.jpg') or
        caption_lower.endswith('.jpeg') or
        caption_lower.endswith('.png') or
        len(caption) < 4 or
        caption_lower == 'font'
    )

    # If it's a real caption, try to match room types
    if not is_filename:
        for room_type, keywords in ROOM_KEYWORDS.items():
            if any(kw in caption_lower for kw in keywords):
                return room_type

    # Smart defaults based on typical Rightmove photo ordering
    if index == 0:
        return "Exterior"

    if total_images >= 10:
        if index <= 2:
            return "Reception Room"
        elif index <= 4:
            return "Kitchen / Dining"
        elif index <= 7:
            return "Bedroom"
        elif index <= 9:
            return "Bathroom"
        else:
            return "Garden / Other"

    return f"Photo {index + 1}"


def extract_property_id(url: str) -> str:
    """Extract property ID from Rightmove URL."""
    match = re.search(r'/propert(?:y|ies)[/-](\d+)', url)
    if match:
        return match.group(1)

    match = re.search(r'propertyId=(\d+)', url)
    if match:
        return match.group(1)

    return ""


def upgrade_image_resolution(url: str) -> str:
    """
    Upgrade Rightmove image URL to highest resolution.
    Strip crop and _max_ parameters to get full resolution.
    """
    # Remove crop parameters
    url = re.sub(r'/crop/\d+x\d+/', '/', url)

    # Remove _max_ parameter entirely - base URL gives full resolution
    url = re.sub(r'/_max_\d+x\d+/', '/', url)

    return url


def parse_page_model(html: str) -> Optional[dict]:
    """
    Extract and parse the PAGE_MODEL JavaScript object from HTML.

    Rightmove embeds property data like:
    window.PAGE_MODEL = {"propertyData": {...}, ...}

    Uses brace counting instead of regex since the JSON can be 500KB+.
    """
    marker = 'window.PAGE_MODEL = '
    start_idx = html.find(marker)

    if start_idx == -1:
        return None

    json_start = start_idx + len(marker)

    # Count braces to find the complete JSON object
    brace_count = 0
    in_string = False
    escape_next = False
    json_end = json_start

    for i, char in enumerate(html[json_start:], start=json_start):
        if escape_next:
            escape_next = False
            continue

        if char == '\\':
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                json_end = i + 1
                break

    if brace_count != 0:
        return None

    json_str = html[json_start:json_end]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def extract_images_from_page_model(data: dict) -> list[PropertyImage]:
    """Extract images from parsed PAGE_MODEL data."""
    images = []

    property_data = data.get('propertyData', data)
    image_list = property_data.get('images', [])
    total_images = len(image_list)

    for idx, img in enumerate(image_list):
        if isinstance(img, dict):
            url = img.get('url') or img.get('srcUrl') or img.get('src', '')
            caption = img.get('caption', '') or img.get('alt', '')
            width = img.get('width')
            height = img.get('height')
        elif isinstance(img, str):
            url = img
            caption = ''
            width = height = None
        else:
            continue

        if not url:
            continue

        # Ensure full URL
        if url.startswith('//'):
            url = 'https:' + url
        elif url.startswith('/'):
            url = 'https://media.rightmove.co.uk' + url

        high_res_url = upgrade_image_resolution(url)
        room_type = detect_room_type(caption, idx, total_images)

        images.append(PropertyImage(
            id=idx + 1,
            url=url,
            url_high_res=high_res_url,
            room_type=room_type,
            caption=caption,
            width=width,
            height=height
        ))

    return images


async def extract_images_from_dom(page: Page) -> list[PropertyImage]:
    """
    Fallback: Extract images directly from DOM if PAGE_MODEL is unavailable.
    Waits for gallery to load and extracts high-res image URLs.
    """
    images = []

    # Wait for gallery to be present
    try:
        await page.wait_for_selector('[data-testid="gallery"], [class*="gallery" i], img[src*="media.rightmove"]', timeout=10000)
    except PlaywrightTimeout:
        print("[WARNING] Gallery selector not found, proceeding anyway")

    # Extract all image URLs from the page
    image_elements = await page.query_selector_all('img')

    seen_urls = set()

    for elem in image_elements:
        src = await elem.get_attribute('src')
        alt = await elem.get_attribute('alt') or ''

        if not src:
            continue

        # Only include Rightmove media URLs
        if 'media.rightmove' not in src.lower():
            continue

        # Skip thumbnails
        if '_max_135x' in src or '_max_100x' in src:
            continue

        if src in seen_urls:
            continue

        seen_urls.add(src)

        # Ensure full URL
        if src.startswith('//'):
            src = 'https:' + src

        high_res_url = upgrade_image_resolution(src)

        images.append(PropertyImage(
            id=len(images) + 1,
            url=src,
            url_high_res=high_res_url,
            room_type=f"Photo {len(images) + 1}",
            caption=alt
        ))

    return images


def extract_property_details(data: dict, page_content: str) -> dict:
    """Extract property metadata from PAGE_MODEL."""
    details = {
        'address': '',
        'price': '',
        'price_qualifier': '',
        'property_type': '',
        'bedrooms': 0,
        'bathrooms': 0,
        'agent_name': '',
        'agent_phone': '',
        'description': '',
        'features': [],
        'floorplan_urls': []
    }

    if not data:
        return details

    prop = data.get('propertyData', data)

    # Address
    addr = prop.get('address', {})
    if isinstance(addr, dict):
        details['address'] = addr.get('displayAddress', '')
    elif isinstance(addr, str):
        details['address'] = addr

    # Price
    prices = prop.get('prices', {})
    if isinstance(prices, dict):
        details['price'] = prices.get('primaryPrice', '')
        details['price_qualifier'] = prices.get('priceQualifier', '')

    # Property info
    details['property_type'] = prop.get('propertySubType', '') or prop.get('propertyType', '')
    details['bedrooms'] = prop.get('bedrooms', 0) or 0
    details['bathrooms'] = prop.get('bathrooms', 0) or 0

    # Agent
    agent = prop.get('customer', {}) or prop.get('agent', {})
    if isinstance(agent, dict):
        details['agent_name'] = agent.get('branchDisplayName', '') or agent.get('name', '')
        details['agent_phone'] = agent.get('contactTelephone', '') or agent.get('phone', '')

    # Description
    details['description'] = prop.get('text', {}).get('description', '') if isinstance(prop.get('text'), dict) else ''

    # Features
    details['features'] = prop.get('keyFeatures', []) or []

    # Floorplans
    floorplans = prop.get('floorplans', [])
    details['floorplan_urls'] = [fp.get('url', '') for fp in floorplans if isinstance(fp, dict) and fp.get('url')]

    return details


async def scrape_with_httpx_fallback(url: str, timeout: float = 30.0) -> PropertyListing:
    """
    Fallback scraper using httpx when Playwright is not available.
    Less reliable but works on resource-constrained environments.
    """
    parsed = urlparse(url)
    if 'rightmove.co.uk' not in parsed.netloc:
        raise ValueError(f"Not a Rightmove URL: {url}")

    property_id = extract_property_id(url)
    if not property_id:
        raise ValueError(f"Could not extract property ID from URL: {url}")

    print(f"[httpx] Fetching {url} (Playwright fallback)")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

        html = response.text
        print(f"[httpx] Fetched HTML ({len(html):,} chars)")

        # Try to extract PAGE_MODEL
        page_model = parse_page_model(html)

        if page_model:
            print(f"[httpx] PAGE_MODEL found!")
            images = extract_images_from_page_model(page_model)
            details = extract_property_details(page_model, html)
        else:
            print(f"[httpx] PAGE_MODEL not found, limited data available")
            images = []
            details = extract_property_details({}, html)

        return PropertyListing(
            url=url,
            property_id=property_id,
            address=details['address'],
            price=details['price'],
            price_qualifier=details['price_qualifier'],
            property_type=details['property_type'],
            bedrooms=details['bedrooms'],
            bathrooms=details['bathrooms'],
            images=images,
            floorplan_urls=details['floorplan_urls'],
            agent_name=details['agent_name'],
            agent_phone=details['agent_phone'],
            description=details['description'],
            features=details['features']
        )


async def scrape_rightmove_listing(url: str, timeout: float = 60.0, headless: bool = True) -> PropertyListing:
    """
    Scrape a Rightmove property listing.

    Tries Playwright first for best results, falls back to httpx if unavailable.

    Args:
        url: Full Rightmove property URL
        timeout: Request timeout in seconds
        headless: Run browser in headless mode (default: True)

    Returns:
        PropertyListing with images and metadata

    Raises:
        ValueError: If URL is invalid
        Exception: If scraping fails
    """
    # Use httpx fallback if Playwright is not available
    if not PLAYWRIGHT_AVAILABLE:
        print("[INFO] Using httpx fallback (Playwright not available)")
        return await scrape_with_httpx_fallback(url, timeout=30.0)

    # Try Playwright, fallback to httpx on failure
    try:
        return await _scrape_with_playwright(url, timeout, headless)
    except Exception as e:
        print(f"[WARNING] Playwright failed ({str(e)}), trying httpx fallback...")
        return await scrape_with_httpx_fallback(url, timeout=30.0)


async def _scrape_with_playwright(url: str, timeout: float = 60.0, headless: bool = True) -> PropertyListing:
    """Internal Playwright scraper."""
    # Validate URL
    parsed = urlparse(url)
    if 'rightmove.co.uk' not in parsed.netloc:
        raise ValueError(f"Not a Rightmove URL: {url}")

    property_id = extract_property_id(url)
    if not property_id:
        raise ValueError(f"Could not extract property ID from URL: {url}")

    async with async_playwright() as p:
        # Launch browser with stealth settings to avoid detection
        try:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-web-security',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--disable-extensions',
                    '--single-process',  # Helps with memory on limited resources
                ]
            )
        except Exception as e:
            raise Exception(f"Failed to launch browser: {str(e)}. Playwright may not be installed correctly.")

        try:
            # Create context with realistic user agent
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                locale='en-GB',
            )

            # Create new page
            page = await context.new_page()

            # Remove automation indicators
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            # Set extra headers
            await page.set_extra_http_headers({
                'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Upgrade-Insecure-Requests': '1',
            })

            print(f"[Playwright] Navigating to: {url}")

            # Navigate to page and wait for network to be idle
            await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)

            print(f"[Playwright] Page loaded, waiting for gallery...")

            # Wait for hydrated gallery DOM with actual images
            try:
                await page.wait_for_selector('img[src*="media.rightmove.co.uk"]', timeout=15000)
                print(f"[Playwright] Gallery images detected")
            except PlaywrightTimeout:
                print(f"[Playwright] Warning: Gallery images not detected")

            # Give extra time for lazy-loaded images
            await asyncio.sleep(2)

            # Get page content
            html = await page.content()
            print(f"[Playwright] Extracted HTML ({len(html):,} chars)")

            # Try to extract PAGE_MODEL first (most reliable)
            page_model = parse_page_model(html)

            if page_model:
                print(f"[Playwright] PAGE_MODEL found!")
                images = extract_images_from_page_model(page_model)
                print(f"[Playwright] Extracted {len(images)} images from PAGE_MODEL")
            else:
                # Fallback: Extract images directly from DOM
                print(f"[Playwright] PAGE_MODEL not found, extracting from DOM...")
                image_elements = await page.query_selector_all('img[src*="media.rightmove.co.uk"]')

                seen_urls = set()
                images = []

                for idx, elem in enumerate(image_elements):
                    src = await elem.get_attribute('src')
                    if not src:
                        continue

                    # Clean URL by removing query params
                    clean_url = src.split('?')[0]

                    # Skip non-property images (logos, maps, UI elements)
                    skip_patterns = [
                        'branch_logo',
                        'BRANCH_PROFILE',
                        '_generate',  # map tiles
                        '/map/',
                        '/assets/',
                        'logo',
                        'icon',
                        'placeholder'
                    ]
                    if any(pattern in clean_url.lower() for pattern in skip_patterns):
                        continue

                    # Deduplicate
                    if clean_url in seen_urls:
                        continue
                    seen_urls.add(clean_url)

                    # Skip thumbnails
                    if '_max_135x' in clean_url or '_max_100x' in clean_url:
                        continue

                    # Only include actual property images (IMG_XX or FLP for floorplans)
                    if '_IMG_' not in clean_url and '_FLP_' not in clean_url:
                        continue

                    # Get alt text for room detection
                    alt = await elem.get_attribute('alt') or ''

                    # Upgrade to high resolution
                    high_res_url = upgrade_image_resolution(clean_url)
                    room_type = detect_room_type(alt, idx, len(image_elements))

                    images.append(PropertyImage(
                        id=len(images) + 1,
                        url=clean_url,
                        url_high_res=high_res_url,
                        room_type=room_type,
                        caption=alt
                    ))

                print(f"[Playwright] Extracted {len(images)} unique images from DOM")

            # Extract property details
            details = extract_property_details(page_model or {}, html)

            return PropertyListing(
                url=url,
                property_id=property_id,
                address=details['address'],
                price=details['price'],
                price_qualifier=details['price_qualifier'],
                property_type=details['property_type'],
                bedrooms=details['bedrooms'],
                bathrooms=details['bathrooms'],
                images=images,
                floorplan_urls=details['floorplan_urls'],
                agent_name=details['agent_name'],
                agent_phone=details['agent_phone'],
                description=details['description'],
                features=details['features']
            )

        finally:
            await browser.close()


# ============================================
# CLI for testing
# ============================================

if __name__ == "__main__":
    import sys

    async def main():
        if len(sys.argv) < 2:
            print("Usage: python rightmove_scraper.py <rightmove_url>")
            print("Example: python rightmove_scraper.py https://www.rightmove.co.uk/properties/154372299")
            sys.exit(1)

        url = sys.argv[1]
        print(f"Scraping: {url}\n")

        try:
            listing = await scrape_rightmove_listing(url, headless=False)

            print(f"\n{'='*60}")
            print(f"RESULTS")
            print(f"{'='*60}")
            print(f"Address: {listing.address}")
            print(f"Price: {listing.price}")
            print(f"Property ID: {listing.property_id}")
            print(f"Type: {listing.property_type}")
            print(f"Bedrooms: {listing.bedrooms}")
            print(f"Bathrooms: {listing.bathrooms}")
            print(f"Agent: {listing.agent_name}")
            print(f"\nImages ({len(listing.images)}):")

            for img in listing.images:
                print(f"  [{img.id}] {img.room_type}: {img.url_high_res[:80]}...")

            if listing.floorplan_urls:
                print(f"\nFloorplans ({len(listing.floorplan_urls)}):")
                for fp in listing.floorplan_urls:
                    print(f"  {fp}")

            if listing.features:
                print(f"\nFeatures:")
                for feat in listing.features[:5]:
                    print(f"  - {feat}")

            print(f"\n{'='*60}")
            print(f"✅ SCRAPING SUCCESSFUL!")
            print(f"{'='*60}")

        except Exception as e:
            print(f"\n{'='*60}")
            print(f"❌ ERROR: {type(e).__name__}: {e}")
            print(f"{'='*60}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    asyncio.run(main())
