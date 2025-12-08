# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Renovision** is an AI-powered property renovation visualization tool for Rightmove listings. Users paste a Rightmove URL, select a room, configure renovation preferences (style, room type, lighting, colors, flooring, greenery), and receive AI-generated photorealistic renovation visualizations.

**Tech Stack:**
- Backend: Python 3.11+, FastAPI, Playwright (headless Chromium)
- Frontend: Single-page React 18 app (embedded in `index.html` via Babel/CDN)
- AI: Gemini 2.0 Flash (Google) or Replicate's Nano Banana model for image generation
- Deployment: Render.com (configured via `render.yaml`)

## Architecture

### Three-Module System

1. **`rightmove_scraper.py`** - Playwright-based scraper module
   - Uses headless Chromium to render client-side JavaScript content
   - Waits for gallery to fully load before extracting data
   - Extracts property data by parsing `window.PAGE_MODEL` from rendered HTML
   - Uses brace-counting algorithm to handle large (500KB+) JSON objects
   - Fallback to DOM extraction if PAGE_MODEL unavailable
   - Detects room types from captions using keyword matching with fallback heuristics
   - Upgrades image URLs to high-resolution by stripping `_max_` and `/crop/` parameters
   - Can run independently as CLI: `python rightmove_scraper.py <url>`

2. **`main.py`** - FastAPI backend server
   - **`POST /property`** - Accepts Rightmove URL, returns property metadata + images
   - **`POST /renovate`** - Accepts image URL + configuration, returns base64 renovated image
   - **`GET /proxy-image`** - Proxies Rightmove images as base64 (bypasses CORS/hotlinking)
   - **`GET /health`** - Health check endpoint
   - **`GET /models`** - Lists available Gemini models for debugging
   - Image generation supports two providers: `gemini` (default) or `replicate`
   - Provider selection via `IMAGE_PROVIDER` env var (defaults to `replicate` if `REPLICATE_API_TOKEN` is set)

3. **`index.html`** - Single-file React frontend
   - Self-contained with embedded React via CDN (no build step for development)
   - API base URL: `https://renovision-5z2b.onrender.com`
   - Four-step workflow: URL input → image selection → configuration → results with before/after slider

### Data Flow

```
User pastes URL → FastAPI /property endpoint
                → rightmove_scraper.scrape_rightmove_listing()
                → Launch headless Chromium
                → Navigate to page, wait for networkidle
                → Wait for gallery selectors
                → Extract PAGE_MODEL from rendered HTML (brace counting)
                → Parse images, metadata
                → Close browser
                → Return PropertyResponse

User configures → FastAPI /renovate endpoint
                → fetch_image_as_base64() (resize if >2048px)
                → build_renovation_prompt() (configurable style/room/lighting/etc)
                → generate_with_gemini() OR generate_with_replicate()
                → Return base64 image

Frontend displays before/after with interactive comparison slider
```

## Development Commands

### Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (required)
playwright install chromium

# Run development server (default port 8000)
uvicorn main:app --reload --port 8000

# Run with custom port
uvicorn main:app --reload --port 8001

# Test scraper independently
python rightmove_scraper.py "https://www.rightmove.co.uk/properties/87288435"

# Debug scraper with full traceback
python debug_scraper.py

# Test specific listing with detailed output
python test_listing.py
```

### Frontend

```bash
# Development: serve with simple HTTP server
python -m http.server 3000
# Then visit: http://localhost:3000

# Alternative: just open the file
open index.html
```

### Environment Variables

Create `.env` file in project root:

```bash
# Choose ONE image provider:

# Option 1: Google Gemini (geo-blocked in UK/EU)
GEMINI_API_KEY=your_key_here

# Option 2: Replicate (works worldwide)
REPLICATE_API_TOKEN=your_token_here

# Optional: CometAPI (alternative Gemini access)
COMET_API_KEY=your_comet_key

# Optional: Force provider (gemini or replicate)
IMAGE_PROVIDER=replicate
```

## Key Implementation Details

### Playwright Browser Automation

Rightmove heavily relies on client-side JavaScript rendering, making traditional HTTP scraping unreliable. The scraper uses **Playwright with headless Chromium**:

- **Launch browser**: Starts headless Chromium with realistic viewport (1920x1080) and user agent
- **Navigation**: Uses `wait_until='networkidle'` to ensure all dynamic content loads
- **Gallery wait**: Explicitly waits for gallery selectors `[data-testid="gallery"]` or `img[src*="media.rightmove"]`
- **Lazy loading**: Additional 2-second delay to catch lazy-loaded images
- **DOM extraction fallback**: If PAGE_MODEL parsing fails, extracts images directly from DOM
- **Browser lifecycle**: Always closes browser in `finally` block to prevent resource leaks
- See `scrape_rightmove_listing()` in `rightmove_scraper.py:369-469`

### PAGE_MODEL Extraction

Rightmove embeds property data in `window.PAGE_MODEL = {...}` in the HTML. This JSON can be 500KB+, so the scraper uses a **brace-counting algorithm** (not regex) to find the complete object:

- Tracks open/close braces while respecting string boundaries and escape sequences
- Handles quoted braces inside strings correctly
- See `parse_page_model()` in `rightmove_scraper.py:157-214`

### Room Type Detection

Two-stage detection in `detect_room_type()`:

1. **Caption matching**: Check image caption against `ROOM_KEYWORDS` dictionary
2. **Positional heuristics**: If caption is just a filename (e.g., `_DSC1234.jpg`), infer room type based on typical Rightmove ordering (exterior → reception → kitchen → bedroom → bathroom → garden)

### Image Generation Prompts

The `build_renovation_prompt()` function creates **image editing prompts** (not generation):

- **Critical constraint**: Emphasizes keeping exact room dimensions, window/door positions, camera angle
- **Garden mode**: Special handling for outdoor spaces with English garden landscaping
- **Configurable elements**: Style presets (midcentury, minimalist, industrial, scandinavian, japanese, mediterranean), room furniture types, time-of-day lighting, color schemes, flooring materials, greenery options
- See `main.py:168-302` for full prompt engineering

### Provider Flexibility

Supports two AI image providers:

- **Gemini**: Direct Google API or CometAPI (alternative access point)
- **Replicate**: Uses `google/nano-banana` model, better for geo-restricted regions
- Provider auto-selected based on available API keys
- Both use same prompt format; backend handles provider-specific API calls

## API Response Formats

### PropertyResponse
```python
{
    "url": str,
    "property_id": str,
    "address": str,
    "price": str,
    "property_type": str,
    "bedrooms": int,
    "bathrooms": int,
    "images": [
        {
            "id": int,
            "url": str,
            "url_high_res": str,
            "room": str,  # detected room type
            "caption": str
        }
    ],
    "floorplan_urls": list[str],
    "agent_name": str
}
```

### RenovationResponse
```python
{
    "original_url": str,
    "generated_image_base64": str,  # base64-encoded JPEG
    "room_type": str,
    "style": str,
    "configuration_applied": dict  # summary of settings
}
```

## Common Pitfalls

1. **Client-side rendering**: Rightmove uses heavy JavaScript; static HTTP scraping will fail - must use Playwright
2. **Browser not installed**: Must run `playwright install chromium` after `pip install` or deployment will fail
3. **Rightmove blocking**: Playwright uses realistic user agent and viewport to avoid detection
4. **Image hotlinking**: Rightmove blocks direct image access; use `/proxy-image` endpoint
5. **Large PAGE_MODEL**: Don't use regex for JSON extraction; use brace counting
6. **Gemini geo-blocking**: Image generation blocked in UK/EU; use Replicate instead
7. **Image size limits**: Gemini recommends max 2048px; scraper auto-resizes
8. **CORS in development**: Frontend must proxy images through backend
9. **Browser memory leaks**: Always close browser in `finally` block; Playwright manages this automatically
10. **Slow scraping**: Browser automation takes ~5-10 seconds per property; consider caching responses

## Deployment

Configured for Render.com via `render.yaml`:

- Service type: `web`
- Runtime: Python 3.12
- Build: `pip install --upgrade pip setuptools wheel && pip install -r requirements.txt && playwright install --with-deps chromium`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Plan: Free tier
- Environment variables:
  - `PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.cache/ms-playwright` (for browser caching)
  - API keys: `GEMINI_API_KEY` or `REPLICATE_API_TOKEN`

**Critical**: The `playwright install --with-deps chromium` command in `buildCommand` is essential. It installs:
- Chromium browser binary
- System dependencies (fonts, libraries) required for headless rendering

For production deployment:
1. Set environment variables in Render dashboard (API keys)
2. Ensure build command includes Playwright installation
3. Configure custom domain if needed
4. Update `API_BASE_URL` in `index.html` to production backend URL
5. Monitor memory usage - browser automation can be resource-intensive
