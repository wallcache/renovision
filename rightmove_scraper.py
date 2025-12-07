"""
Rightmove Property Scraper
==========================
Production-ready scraper for extracting property images from Rightmove listings.

Rightmove embeds property data in a JavaScript variable called PAGE_MODEL.
This module extracts that data and parses out the high-resolution images.

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

import httpx
from bs4 import BeautifulSoup


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
        caption_lower == 'font'  # Rightmove sometimes uses this for hero images
    )
    
    # If it's a real caption, try to match room types
    if not is_filename:
        for room_type, keywords in ROOM_KEYWORDS.items():
            if any(kw in caption_lower for kw in keywords):
                return room_type
    
    # Smart defaults based on typical Rightmove photo ordering
    # First image is usually exterior/hero shot
    if index == 0:
        return "Exterior"
    
    # Try to provide useful defaults based on position
    # Most listings follow: exterior, reception rooms, kitchen, bedrooms, bathroom, garden
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
    # URLs look like: https://www.rightmove.co.uk/properties/154372299
    # or: https://www.rightmove.co.uk/property-for-sale/property-154372299.html
    
    match = re.search(r'/propert(?:y|ies)[/-](\d+)', url)
    if match:
        return match.group(1)
    
    match = re.search(r'propertyId=(\d+)', url)
    if match:
        return match.group(1)
    
    return ""


def upgrade_image_resolution(url: str) -> str:
    """
    Clean up Rightmove image URL.
    
    Rightmove's PAGE_MODEL sometimes returns URLs with _max_ in wrong places.
    We strip it out entirely and use the base URL which gives full resolution.
    """
    # Remove crop parameters
    url = re.sub(r'/crop/\d+x\d+/', '/', url)
    
    # Remove _max_ parameter entirely - the base URL gives full resolution
    url = re.sub(r'/_max_\d+x\d+/', '/', url)
    
    return url


def parse_page_model(html: str) -> Optional[dict]:
    """
    Extract and parse the PAGE_MODEL JavaScript object from HTML.
    
    Rightmove embeds property data like:
    window.PAGE_MODEL = {"propertyData": {...}, ...}
    
    Uses brace counting instead of regex since the JSON can be 500KB+.
    """
    # Find the start of PAGE_MODEL
    marker = 'window.PAGE_MODEL = '
    start_idx = html.find(marker)
    
    if start_idx == -1:
        return None
    
    # Move past the marker
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
    
    # Navigate to images array - structure varies slightly
    property_data = data.get('propertyData', data)
    image_list = property_data.get('images', [])
    total_images = len(image_list)
    
    for idx, img in enumerate(image_list):
        # Handle different image object structures
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


def extract_images_from_html(soup: BeautifulSoup, html: str) -> list[PropertyImage]:
    """
    Fallback: Extract images directly from HTML when PAGE_MODEL isn't available.
    """
    images = []
    seen_urls = set()
    
    # Pattern 1: Find all Rightmove media URLs in the HTML
    url_pattern = r'https?://media\.rightmove\.co\.uk[^"\'>\s\\]+\.(?:jpg|jpeg|png|webp)'
    urls = re.findall(url_pattern, html, re.IGNORECASE)
    
    for idx, url in enumerate(urls):
        # Clean URL (remove escape characters)
        url = url.replace('\\u002F', '/').replace('\\/', '/')
        
        # Skip thumbnails and duplicates
        if url in seen_urls:
            continue
        if '_max_135x' in url or '_max_100x' in url:
            continue
        
        seen_urls.add(url)
        high_res_url = upgrade_image_resolution(url)
        
        images.append(PropertyImage(
            id=len(images) + 1,
            url=url,
            url_high_res=high_res_url,
            room_type=f"Photo {len(images) + 1}",
            caption=""
        ))
    
    # Pattern 2: Look for gallery images in specific elements
    gallery_imgs = soup.select('[data-testid*="gallery"] img, [class*="gallery"] img, [class*="Gallery"] img')
    for img in gallery_imgs:
        src = img.get('src') or img.get('data-src', '')
        if src and 'rightmove' in src.lower() and src not in seen_urls:
            seen_urls.add(src)
            images.append(PropertyImage(
                id=len(images) + 1,
                url=src,
                url_high_res=upgrade_image_resolution(src),
                room_type=f"Photo {len(images) + 1}",
                caption=img.get('alt', '')
            ))
    
    return images


def extract_property_details(data: dict, soup: BeautifulSoup) -> dict:
    """Extract property metadata from PAGE_MODEL or HTML."""
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
    
    if data:
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
    
    # Fallback to HTML parsing
    if not details['address']:
        title = soup.find('meta', property='og:title')
        if title:
            details['address'] = title.get('content', '').split(' | ')[0]
    
    if not details['price']:
        price_elem = soup.select_one('[data-testid="price"], [class*="price" i]')
        if price_elem:
            details['price'] = price_elem.get_text(strip=True)
    
    return details


async def scrape_rightmove_listing(url: str, timeout: float = 30.0) -> PropertyListing:
    """
    Scrape a Rightmove property listing.
    
    Args:
        url: Full Rightmove property URL
        timeout: Request timeout in seconds
    
    Returns:
        PropertyListing with images and metadata
    
    Raises:
        ValueError: If URL is invalid
        httpx.HTTPError: If request fails
    """
    # Validate URL
    parsed = urlparse(url)
    if 'rightmove.co.uk' not in parsed.netloc:
        raise ValueError(f"Not a Rightmove URL: {url}")
    
    property_id = extract_property_id(url)
    if not property_id:
        raise ValueError(f"Could not extract property ID from URL: {url}")
    
    # Request headers to look like a real browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True
    ) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # Try to get structured data from PAGE_MODEL
        page_model = parse_page_model(html)
        
        # Extract images
        if page_model:
            images = extract_images_from_page_model(page_model)
        else:
            images = extract_images_from_html(soup, html)
        
        # Extract property details
        details = extract_property_details(page_model or {}, soup)
        
        return PropertyListing(
            url=str(response.url),
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
            listing = await scrape_rightmove_listing(url)
            
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
        
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}")
            sys.exit(1)
    
    asyncio.run(main())
