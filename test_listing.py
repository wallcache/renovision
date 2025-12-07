#!/usr/bin/env python3
"""
Test script for specific Rightmove listing - FIXED VERSION
Run: python test_listing.py
"""

import asyncio
import httpx
from bs4 import BeautifulSoup
import re
import json

URL = "https://www.rightmove.co.uk/properties/87288435"

def extract_page_model(html: str) -> dict:
    """
    Extract PAGE_MODEL using a more robust approach.
    The JSON can be huge, so we need to find the complete object.
    """
    # Find the start of PAGE_MODEL
    marker = 'window.PAGE_MODEL = '
    start_idx = html.find(marker)
    
    if start_idx == -1:
        return None
    
    # Move past the marker
    json_start = start_idx + len(marker)
    
    # Now we need to find where the JSON object ends
    # We'll count braces to find the matching closing brace
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
        print(f"Warning: Unbalanced braces, count = {brace_count}")
        return None
    
    json_str = html[json_start:json_end]
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON parse error at position {e.pos}: {e.msg}")
        # Save for debugging
        with open("page_model_raw.json", "w") as f:
            f.write(json_str)
        print("Saved raw JSON to page_model_raw.json")
        return None


async def test():
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    
    print(f"Fetching: {URL}\n")
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(URL, headers=headers)
        
        print(f"Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"Error: {response.text[:500]}")
            return
        
        html = response.text
        print(f"HTML length: {len(html):,} chars\n")
        
        # Extract PAGE_MODEL with brace counting
        print("=" * 60)
        print("EXTRACTING PAGE_MODEL")
        print("=" * 60)
        
        data = extract_page_model(html)
        
        if not data:
            print("❌ Failed to extract PAGE_MODEL")
            return
        
        print("✅ PAGE_MODEL extracted successfully!")
        print(f"   Top-level keys: {list(data.keys())}")
        
        # Get property data
        prop = data.get('propertyData', {})
        print(f"\n   propertyData keys: {list(prop.keys())[:20]}...")
        
        # Address
        addr = prop.get('address', {})
        print(f"\n📍 Address: {addr.get('displayAddress', 'N/A')}")
        
        # Price
        prices = prop.get('prices', {})
        print(f"💰 Price: {prices.get('primaryPrice', 'N/A')}")
        
        # Property details
        print(f"🛏️  Bedrooms: {prop.get('bedrooms', 'N/A')}")
        print(f"🛁 Bathrooms: {prop.get('bathrooms', 'N/A')}")
        print(f"🏠 Type: {prop.get('propertySubType', prop.get('propertyType', 'N/A'))}")
        
        # Agent
        customer = prop.get('customer', {})
        print(f"🏢 Agent: {customer.get('branchDisplayName', 'N/A')}")
        
        # Images
        images = prop.get('images', [])
        print(f"\n📸 Found {len(images)} images:\n")
        
        for idx, img in enumerate(images):
            if isinstance(img, dict):
                url = img.get('url', img.get('srcUrl', ''))
                caption = img.get('caption', '')
                
                # Upgrade to high res
                high_res = re.sub(r'_max_\d+x\d+', '_max_1024x1024', url)
                if not high_res.startswith('http'):
                    high_res = 'https:' + high_res if high_res.startswith('//') else high_res
                
                room = caption if caption else f"Photo {idx + 1}"
                print(f"   [{idx+1:2d}] {room}")
                print(f"        {high_res}")
                print()
            else:
                print(f"   [{idx+1:2d}] (raw): {str(img)[:80]}...")
        
        # Floorplans
        floorplans = prop.get('floorplans', [])
        if floorplans:
            print(f"\n📐 Floorplans ({len(floorplans)}):")
            for fp in floorplans:
                if isinstance(fp, dict):
                    print(f"   {fp.get('url', fp)}")
        
        # Key features
        features = prop.get('keyFeatures', [])
        if features:
            print(f"\n✨ Key Features:")
            for feat in features[:8]:
                print(f"   • {feat}")
        
        print("\n" + "=" * 60)
        print("✅ SCRAPING SUCCESSFUL!")
        print("=" * 60)
        
        return data

if __name__ == "__main__":
    asyncio.run(test())
