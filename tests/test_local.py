#!/usr/bin/env python3
"""
Quick test script for Renovision.

Run this locally to verify the Rightmove scraper and Gemini integration work.

Usage:
    python test_local.py

Requirements:
    pip install httpx beautifulsoup4 python-dotenv pillow
"""

import asyncio
import sys
import os

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rightmove_scraper import scrape_rightmove_listing


async def test_rightmove_scraper():
    """Test the Rightmove scraper with a real listing."""
    
    # You can replace this with any current Rightmove listing
    test_urls = [
        "https://www.rightmove.co.uk/properties/154372299",
        "https://www.rightmove.co.uk/properties/149876543",
    ]
    
    print("=" * 60)
    print("RIGHTMOVE SCRAPER TEST")
    print("=" * 60)
    
    for url in test_urls:
        print(f"\nTesting: {url}")
        print("-" * 60)
        
        try:
            listing = await scrape_rightmove_listing(url)
            
            print(f"✅ Success!")
            print(f"   Address: {listing.address}")
            print(f"   Price: {listing.price}")
            print(f"   Property ID: {listing.property_id}")
            print(f"   Type: {listing.property_type}")
            print(f"   Beds: {listing.bedrooms} | Baths: {listing.bathrooms}")
            print(f"   Agent: {listing.agent_name}")
            print(f"   Images: {len(listing.images)}")
            
            if listing.images:
                print(f"\n   Sample images:")
                for img in listing.images[:5]:
                    print(f"      [{img.id}] {img.room_type}")
                    print(f"          {img.url_high_res[:70]}...")
            
            if listing.floorplan_urls:
                print(f"\n   Floorplans: {len(listing.floorplan_urls)}")
            
            # Test passed for this URL, no need to try others
            break
            
        except Exception as e:
            print(f"❌ Failed: {type(e).__name__}: {e}")
            continue
    
    print("\n" + "=" * 60)


async def test_gemini_models():
    """List available Gemini models."""
    import httpx
    from dotenv import load_dotenv
    
    load_dotenv()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("\n⚠️  GEMINI_API_KEY not set - skipping model test")
        return
    
    print("\n" + "=" * 60)
    print("GEMINI MODEL TEST")
    print("=" * 60)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            )
            
            if response.status_code != 200:
                print(f"❌ API Error: {response.status_code}")
                print(response.text[:500])
                return
            
            data = response.json()
            models = data.get('models', [])
            
            print(f"\n✅ Found {len(models)} models")
            print("\nModels supporting generateContent (for image gen):")
            
            image_capable = []
            for model in models:
                name = model.get('name', '').replace('models/', '')
                methods = model.get('supportedGenerationMethods', [])
                
                if 'generateContent' in methods:
                    # Check if it might support images
                    desc = model.get('description', '').lower()
                    if 'image' in name.lower() or 'image' in desc or 'vision' in desc:
                        image_capable.append(name)
                        print(f"   🖼️  {name}")
                    else:
                        print(f"   📝 {name}")
            
            print(f"\nRecommended for image generation:")
            for model in ['gemini-3-pro-image-preview', 'gemini-2.0-flash-exp', 'imagen-3.0-generate-001']:
                status = "✅" if model in [m.replace('models/', '') for m in [model.get('name', '') for model in models]] else "❌"
                print(f"   {status} {model}")
                
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
    
    print("\n" + "=" * 60)


async def test_image_generation():
    """Test a simple image generation call."""
    import httpx
    from dotenv import load_dotenv
    
    load_dotenv()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("\n⚠️  GEMINI_API_KEY not set - skipping generation test")
        return
    
    print("\n" + "=" * 60)
    print("IMAGE GENERATION TEST")
    print("=" * 60)
    
    # Try different models
    models_to_try = [
        "gemini-3-pro-image-preview",
        "gemini-2.0-flash-exp", 
        "gemini-1.5-pro-latest",
    ]
    
    for model in models_to_try:
        print(f"\nTrying model: {model}")
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                    json={
                        "contents": [{
                            "role": "user",
                            "parts": [{
                                "text": "Generate a simple image of a modern minimalist kitchen with white cabinets and wooden countertops. Photorealistic style."
                            }]
                        }],
                        "generationConfig": {
                            "responseModalities": ["IMAGE", "TEXT"]
                        }
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check if we got an image
                    candidates = data.get('candidates', [])
                    if candidates:
                        parts = candidates[0].get('content', {}).get('parts', [])
                        for part in parts:
                            if 'inlineData' in part:
                                print(f"✅ {model} - Image generated successfully!")
                                print(f"   MIME type: {part['inlineData'].get('mimeType', 'unknown')}")
                                print(f"   Data length: {len(part['inlineData'].get('data', ''))} chars")
                                return model
                            elif 'text' in part:
                                print(f"   Got text response: {part['text'][:100]}...")
                    
                    print(f"⚠️  {model} - No image in response")
                    
                else:
                    error = response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
                    print(f"❌ {model} - Error {response.status_code}")
                    if isinstance(error, dict):
                        print(f"   {error.get('error', {}).get('message', str(error)[:200])}")
                    else:
                        print(f"   {str(error)[:200]}")
                        
        except Exception as e:
            print(f"❌ {model} - {type(e).__name__}: {e}")
    
    print("\n⚠️  No model successfully generated an image")
    print("   You may need to:")
    print("   1. Enable the Gemini API in Google Cloud Console")
    print("   2. Request access to image generation models")
    print("   3. Use an alternative like CometAPI or Vertex AI")
    
    print("\n" + "=" * 60)


async def main():
    print("\n🏠 RENOVISION - Local Test Suite\n")
    
    # Test 1: Rightmove scraper
    await test_rightmove_scraper()
    
    # Test 2: Available Gemini models
    await test_gemini_models()
    
    # Test 3: Image generation
    await test_image_generation()
    
    print("\n✨ Tests complete!\n")


if __name__ == "__main__":
    asyncio.run(main())
