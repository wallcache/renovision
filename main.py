"""
Renovision Backend
==================
FastAPI backend for Rightmove image extraction and Gemini 3 Pro Image generation.

Setup:
    pip install fastapi uvicorn httpx beautifulsoup4 google-generativeai python-dotenv pillow

Run:
    uvicorn main:app --reload --port 8000
"""

import os
import re
import json
import base64
import asyncio
from io import BytesIO
from typing import Optional
from datetime import datetime

import httpx
from PIL import Image
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from dotenv import load_dotenv

# Import our Rightmove scraper
from rightmove_scraper import scrape_rightmove_listing, PropertyListing

# Load environment variables
load_dotenv()

# ============================================
# CONFIGURATION
# ============================================

GEMINI_API_KEY = os.getenv("REPLICATE_API_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Image generation provider: "gemini" or "replicate"
# Gemini image gen is geo-blocked in some countries (UK, EU)
# Replicate works worldwide
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "replicate" if REPLICATE_API_TOKEN else "gemini")

# Gemini config
GEMINI_MODEL = "gemini-2.0-flash-exp"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# For CometAPI (alternative provider mentioned in docs - often has newer models faster)
COMET_API_KEY = os.getenv("COMET_API_KEY")
COMET_API_URL = "https://api.cometapi.com/v1beta/models/gemini-3-pro-image-preview:generateContent"

# Use CometAPI if available, otherwise use Google's direct API
USE_COMET_API = bool(COMET_API_KEY)

app = FastAPI(
    title="Renovision API",
    description="Transform doer-upper properties with AI-powered renovation visualisation",
    version="1.0.0"
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# MODELS
# ============================================

class RightmoveRequest(BaseModel):
    url: HttpUrl

class PropertyImage(BaseModel):
    id: int
    url: str
    url_high_res: str
    room: str
    caption: str = ""

class PropertyResponse(BaseModel):
    url: str
    property_id: str
    address: str
    price: str
    property_type: str = ""
    bedrooms: int = 0
    bathrooms: int = 0
    images: list[PropertyImage]
    floorplan_urls: list[str] = []
    agent_name: str = ""

class RenovationRequest(BaseModel):
    image_url: str
    # Primary configuration options
    style: Optional[str] = None  # midcentury, minimal, industrial, scandinavian, wabisabi, mediterranean
    room_type: Optional[str] = None  # living, bedroom, kitchen, dining, bathroom, office, hallway, garden, outdoor
    # Optional configuration toggles
    time_of_day: Optional[str] = None  # day, night, golden_hour
    colour_scheme: Optional[str] = None  # white, black, charcoal, navy, teal, forest_green, olive, mustard, terracotta, burgundy
    flooring: Optional[str] = None  # wood_parquet, tiled, stone_slabs, polished_concrete
    extra_notes: Optional[str] = None
    auto_download: Optional[bool] = False

class RenovationResponse(BaseModel):
    original_url: str
    generated_image_base64: str
    room_type: Optional[str] = None
    style: Optional[str] = None
    configuration_applied: dict = {}

# ============================================
# RIGHTMOVE SCRAPING (uses rightmove_scraper module)
# ============================================

async def get_property_from_rightmove(url: str) -> PropertyResponse:
    """
    Fetch and parse a Rightmove listing using our scraper module.
    """
    try:
        listing = await scrape_rightmove_listing(url)
        
        # Convert to API response format
        images = [
            PropertyImage(
                id=img.id,
                url=img.url,
                url_high_res=img.url_high_res,
                room=img.room_type,
                caption=img.caption
            )
            for img in listing.images
        ]
        
        return PropertyResponse(
            url=listing.url,
            property_id=listing.property_id,
            address=listing.address,
            price=listing.price,
            property_type=listing.property_type,
            bedrooms=listing.bedrooms,
            bathrooms=listing.bathrooms,
            images=images,
            floorplan_urls=listing.floorplan_urls,
            agent_name=listing.agent_name
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Log the full error for debugging
        import traceback
        print(f"[ERROR] Scraper failed: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to scrape property: {str(e)}")

# ============================================
# GEMINI IMAGE GENERATION
# ============================================

def build_renovation_prompt(request: RenovationRequest) -> str:
    """Build an optimised prompt for image EDITING (not generation) with style and configuration toggles."""
    
    # Interior Design Style descriptions
    style_prompts = {
        'midcentury': 'Mid-Century Modern design featuring warm walnut and teak wood tones, clean architectural lines, organic curves, iconic furniture with hairpin legs, statement lighting, warm mustard and olive accents',
        'minimal': 'Minimal design with sleek clutter-free styling, refined neutral palette of whites and greys, functional furniture with clean lines, strategic negative space, hidden storage, understated elegance',
        'industrial': 'Industrial aesthetic with metal fixtures and Edison bulbs, concrete-effect surfaces, black steel elements, leather and distressed wood furniture, urban warehouse character',
        'scandinavian': 'Scandinavian hygge-inspired design with light oak and birch woods, cosy wool and linen textiles, bright neutral whites and soft greys, functional furniture, warm ambient lighting',
        'wabisabi': 'Wabi-Sabi design embracing natural imperfection, low platform furniture, natural wood and stone materials, zen minimalism, asymmetric balance, clean simplicity',
        'mediterranean': 'Mediterranean style with terracotta tones, wrought iron details, warm ochre and sun-bleached colours, rustic wooden elements, artisanal ceramics',
    }

    # Room Type descriptions (for furniture context only)
    room_type_furniture = {
        'living': 'appropriate living room furniture like sofa, coffee table, ambient lighting',
        'bedroom': 'appropriate bedroom furniture like bed, bedside tables, soft lighting',
        'kitchen': 'appropriate kitchen elements like updated cabinets, countertops, modern appliances',
        'dining': 'appropriate dining furniture like table and chairs, statement lighting',
        'bathroom': 'appropriate bathroom fixtures like updated vanity, modern taps, quality tiles',
        'office': 'appropriate office furniture like desk, ergonomic chair, task lighting',
        'hallway': 'appropriate hallway elements like console table, runner rug, wall lighting',
        'garden': 'landscaped garden features',
        'outdoor': 'outdoor furniture and landscaping elements',
    }
    
    # Time of day lighting descriptions
    time_of_day_prompts = {
        'day': 'bright natural daylight, crisp clean shadows',
        'night': 'warm artificial lighting, cosy interior mood, soft ambient glow',
        'golden_hour': 'warm cinematic golden hour lighting with glowing highlights',
    }

    # Colour scheme palettes
    colour_scheme_prompts = {
        'white': 'crisp white walls, clean bright palette',
        'black': 'dramatic black walls with contrasting light elements',
        'charcoal': 'charcoal grey walls, moody sophisticated atmosphere',
        'navy': 'navy blue walls with warm brass or gold accents',
        'teal': 'teal walls, jewel-toned rich atmosphere',
        'forest_green': 'dark forest green walls, warm timber, bronze accents',
        'olive': 'olive green walls, earthy natural tones',
        'mustard': 'mustard yellow walls, warm vibrant energy',
        'terracotta': 'terracotta walls, Mediterranean warmth',
        'burgundy': 'burgundy walls, deep luxurious tones',
    }

    # Flooring descriptions
    flooring_prompts = {
        'wood_parquet': 'wood parquet flooring in herringbone or chevron pattern',
        'tiled': 'large format ceramic or porcelain tiles',
        'stone_slabs': 'natural stone slab flooring',
        'polished_concrete': 'polished concrete floors with subtle sheen',
    }
    
    # Build the prompt - FOCUS ON EDITING, NOT GENERATING
    
    # Special handling for garden/outdoor spaces
    if request.room_type == 'garden':
        prompt_parts = [
            "EDIT THE PROVIDED PHOTOGRAPH of this outdoor space. Do not create a new image - modify the existing photo only.",
            "Keep the EXACT same garden boundaries, fences, walls, and structures in their current positions.",
            "Keep the EXACT same camera angle and perspective as the input photo.",
            "Transform this into a beautifully landscaped English garden.",
            "Add a pristine manicured lawn with healthy lush green grass.",
            "Include neatly trimmed box hedges (buxus) creating structure, borders, and definition.",
            "Add natural York stone or flagstone stepping stones creating an elegant garden path.",
            "Include tasteful planting beds with layered perennials, ornamental grasses, and seasonal flowers.",
            "Add classic English garden elements: wooden bench, terracotta pots, or elegant planters.",
            "The garden should look established, well-maintained, and quintessentially British.",
        ]
        
        # Add time of day if specified
        if request.time_of_day and request.time_of_day in time_of_day_prompts:
            prompt_parts.append(f"Lighting: {time_of_day_prompts[request.time_of_day]}.")
        
        # Add extra notes if provided
        if request.extra_notes:
            prompt_parts.append(f"Also: {request.extra_notes}")
        
        prompt_parts.append("Photorealistic result, professional landscape photography quality, natural daylight.")
        
        return " ".join(prompt_parts)
    
    # Standard interior renovation prompt
    prompt_parts = [
        "EDIT THE PROVIDED PHOTOGRAPH. Do not create a new image - modify the existing photo only.",
        "Keep the EXACT same room - same size, same shape, same walls, same ceiling height.",
        "Keep ALL windows EXACTLY where they are in the photo - same position, same size, same shape.",
        "Keep ALL doors EXACTLY where they are in the photo - same position, same size, same shape.", 
        "Keep the EXACT same camera angle and perspective as the input photo.",
        "ONLY CHANGE: paint colours, flooring material, furniture, fixtures, and decor.",
        "DO NOT: enlarge the room, add windows, add doors, change the room shape, or change the viewpoint.",
    ]
    
    # Style
    if request.style and request.style in style_prompts:
        prompt_parts.append(f"Apply {style_prompts[request.style]}.")
    
    # Room-appropriate furniture
    if request.room_type and request.room_type in room_type_furniture:
        prompt_parts.append(f"Use {room_type_furniture[request.room_type]}.")
    
    # Add time of day if specified
    if request.time_of_day and request.time_of_day in time_of_day_prompts:
        prompt_parts.append(f"Lighting: {time_of_day_prompts[request.time_of_day]}.")
    
    # Add colour scheme if specified
    if request.colour_scheme and request.colour_scheme in colour_scheme_prompts:
        prompt_parts.append(f"Colours: {colour_scheme_prompts[request.colour_scheme]}.")
    
    # Add flooring if specified
    if request.flooring and request.flooring in flooring_prompts:
        prompt_parts.append(f"Flooring: {flooring_prompts[request.flooring]}.")

    # Always include greenery for realism and warmth
    prompt_parts.append("Include tasteful placement of indoor plants and flowers to enhance realism and warmth.")

    # Add extra notes if provided
    if request.extra_notes:
        prompt_parts.append(f"Also: {request.extra_notes}")

    # Final quality reminder
    prompt_parts.append("Photorealistic result, professional interior photography quality.")

    return " ".join(prompt_parts)


async def fetch_image_as_base64(url: str) -> str:
    """Fetch an image from URL and return as base64."""
    
    # Clean up the URL - remove any double encoding issues
    import urllib.parse
    
    # If URL doesn't start with http, it might be malformed
    if not url.startswith('http'):
        url = 'https:' + url if url.startswith('//') else 'https://' + url
    
    print(f"[DEBUG] Fetching image from: {url}")
    
    # Headers to look like a real browser - needed for Rightmove images
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.rightmove.co.uk/",
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, headers=headers, follow_redirects=True)
            print(f"[DEBUG] Response status: {response.status_code}, URL: {response.url}")
            
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to fetch source image: {response.status_code} from {url}")
            
            # Optionally resize if too large
            img = Image.open(BytesIO(response.content))
            
            # Resize if larger than 2048 on any side (Gemini's recommended max)
            max_size = 2048
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = tuple(int(dim * ratio) for dim in img.size)
                img = img.resize(new_size, Image.LANCZOS)
            
            # Convert to RGB if necessary (remove alpha channel)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            
            # Save to bytes
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=90)
            return base64.b64encode(buffer.getvalue()).decode('utf-8')
            
        except httpx.RequestError as e:
            print(f"[DEBUG] Request error: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to fetch source image: {str(e)}")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')


async def generate_with_replicate(source_image_b64: str, prompt: str) -> str:
    """
    Generate image using Replicate's google/nano-banana model.
    """
    if not REPLICATE_API_TOKEN:
        raise HTTPException(status_code=500, detail="REPLICATE_API_TOKEN not configured")
    
    headers = {
        "Authorization": f"Token {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Convert our base64 source image to a temporary data URL
    image_data_url = f"data:image/jpeg;base64,{source_image_b64}"
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        # Create prediction
        response = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers=headers,
            json={
                "version": "google/nano-banana",
                "input": {
                    "prompt": prompt,
                    "image_input": [
                        image_data_url
                    ],
                    "aspect_ratio": "match_input_image",
                    "output_format": "jpg"
                }
            }
        )
        
        if response.status_code != 201:
            error_detail = response.text
            if response.status_code == 402:
                error_detail = "Replicate API: Payment required. Your API token may be out of credits or invalid. Please check your account at https://replicate.com/account"
            elif response.status_code == 401:
                error_detail = "Replicate API: Invalid or missing API token. Please check REPLICATE_API_TOKEN environment variable."

            print(f"[ERROR] Replicate API failed: {response.status_code} - {response.text}")

            raise HTTPException(
                status_code=500,
                detail=error_detail
            )
        
        prediction = response.json()
        prediction_url = prediction["urls"]["get"]
        
        # Poll until finished
        for _ in range(60):  # ~5 minutes max
            await asyncio.sleep(5)
            
            status_response = await client.get(prediction_url, headers=headers)
            status = status_response.json()
            
            if status["status"] == "succeeded":
                output_url = status["output"]
                
                # Fetch the generated image
                img_response = await client.get(output_url)
                return base64.b64encode(img_response.content).decode("utf-8")
            
            elif status["status"] == "failed":
                raise HTTPException(
                    status_code=500,
                    detail=f"Nano-Banana generation failed: {status.get('error', 'Unknown error')}"
                )
        
        raise HTTPException(
            status_code=500,
            detail="Nano-Banana generation timed out"
        )


async def generate_with_gemini(source_image_b64: str, prompt: str) -> str:
    """Generate image using Gemini API."""
    
    if USE_COMET_API:
        api_url = COMET_API_URL
        headers = {
            "x-goog-api-key": COMET_API_KEY,
            "Content-Type": "application/json"
        }
    else:
        api_url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"
        headers = {
            "Content-Type": "application/json"
        }
    
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": source_image_b64
                    }
                },
                {
                    "text": prompt
                }
            ]
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"]
        }
    }
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(api_url, json=payload, headers=headers)
        
        print(f"[DEBUG] Gemini response status: {response.status_code}")
        
        if response.status_code != 200:
            error_detail = response.text
            print(f"[DEBUG] Gemini error: {error_detail}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Gemini API error: {error_detail}"
            )
        
        result = response.json()
        
        candidates = result.get('candidates', [])
        if not candidates:
            raise HTTPException(status_code=500, detail="No image generated")
        
        parts = candidates[0].get('content', {}).get('parts', [])
        
        for part in parts:
            if 'inlineData' in part:
                return part['inlineData']['data']
        
        raise HTTPException(status_code=500, detail="No image in response")


async def generate_renovation_image(request: RenovationRequest) -> str:
    """
    Generate renovated room image using configured provider.
    Returns base64-encoded image.
    """
    # Fetch and encode the source image
    source_image_b64 = await fetch_image_as_base64(request.image_url)
    
    # Build the prompt
    prompt = build_renovation_prompt(request)
    
    print(f"[DEBUG] Using image provider: {IMAGE_PROVIDER}")
    print(f"[DEBUG] Prompt: {prompt[:200]}...")
    
    if IMAGE_PROVIDER == "replicate":
        if not REPLICATE_API_TOKEN:
            raise HTTPException(
                status_code=500, 
                detail="REPLICATE_API_TOKEN not configured. Get one at https://replicate.com"
            )
        return await generate_with_replicate(source_image_b64, prompt)
    else:
        if not GEMINI_API_KEY and not COMET_API_KEY:
            raise HTTPException(
                status_code=500, 
                detail="No API key configured. Set GEMINI_API_KEY or REPLICATE_API_TOKEN."
            )
        return await generate_with_gemini(source_image_b64, prompt)


# ============================================
# API ENDPOINTS
# ============================================


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "gemini_configured": bool(GEMINI_API_KEY or COMET_API_KEY)
    }


@app.get("/models")
async def list_available_models():
    """
    List available Gemini models. Useful for debugging which models
    support image generation on your API key.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="No API key configured")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        
        models = response.json().get('models', [])
        
        # Filter to models that support generateContent (needed for image gen)
        relevant_models = []
        for model in models:
            name = model.get('name', '').replace('models/', '')
            methods = model.get('supportedGenerationMethods', [])
            if 'generateContent' in methods:
                relevant_models.append({
                    "name": name,
                    "display_name": model.get('displayName', ''),
                    "description": model.get('description', ''),
                    "methods": methods
                })
        
        return {
            "models": relevant_models,
            "recommended_for_images": [
                "gemini-3-pro-image-preview",
                "gemini-2.0-flash-exp", 
                "gemini-2.5-flash-preview-04-17"
            ]
        }


@app.post("/property", response_model=PropertyResponse)
async def get_property_images(request: RightmoveRequest):
    """
    Extract property images from a Rightmove listing URL.
    Returns list of images with detected room types.
    """
    return await get_property_from_rightmove(str(request.url))


@app.get("/proxy-image")
async def proxy_image(url: str):
    """
    Proxy images from Rightmove - returns base64 encoded image data.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.rightmove.co.uk/",
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers, follow_redirects=True)
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch image")
        
        # Determine content type from URL
        if "png" in url.lower():
            mime_type = "image/png"
        elif "webp" in url.lower():
            mime_type = "image/webp"
        else:
            mime_type = "image/jpeg"
        
        # Return as base64 data URL
        b64_data = base64.b64encode(response.content).decode('utf-8')
        data_url = f"data:{mime_type};base64,{b64_data}"
        
        return {"data_url": data_url}


@app.post("/renovate", response_model=RenovationResponse)
async def generate_renovation(request: RenovationRequest):
    """
    Generate a renovated version of a room image.
    Returns the original URL and generated image as base64.
    Single image only - batch processing removed.
    """
    generated_image = await generate_renovation_image(request)
    
    # Build configuration summary
    config = {}
    if request.style:
        config['style'] = request.style
    if request.room_type:
        config['room_type'] = request.room_type
    if request.time_of_day:
        config['time_of_day'] = request.time_of_day
    if request.colour_scheme:
        config['colour_scheme'] = request.colour_scheme
    if request.flooring:
        config['flooring'] = request.flooring
    if request.greenery:
        config['greenery'] = request.greenery
    
    return RenovationResponse(
        original_url=request.image_url,
        generated_image_base64=generated_image,
        room_type=request.room_type,
        style=request.style,
        configuration_applied=config
    )


# Health check endpoint
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "provider": IMAGE_PROVIDER,
        "replicate_configured": bool(REPLICATE_API_TOKEN),
        "gemini_configured": bool(GEMINI_API_KEY),
        "note": "402 errors indicate API payment/credits issue"
    }

from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory=".", html=True), name="static")


# ============================================
# RUN SERVER
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
