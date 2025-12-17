#!/usr/bin/env python3
"""
Debug script - run this directly to see the full error traceback
"""

import asyncio
import sys
sys.path.insert(0, '.')

from rightmove_scraper import scrape_rightmove_listing

async def test():
    url = "https://www.rightmove.co.uk/properties/87288435"
    print(f"Testing: {url}\n")
    
    try:
        listing = await scrape_rightmove_listing(url)
        print(f"✅ Success!")
        print(f"Address: {listing.address}")
        print(f"Price: {listing.price}")
        print(f"Images: {len(listing.images)}")
        
        for img in listing.images[:5]:
            print(f"  [{img.id}] {img.room_type}: {img.url_high_res[:60]}...")
            
    except Exception as e:
        import traceback
        print(f"❌ Error: {type(e).__name__}: {e}")
        print("\nFull traceback:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
