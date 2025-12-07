# Renovision

**See your future home** — AI-powered renovation visualisation for Rightmove listings.

Paste a Rightmove URL, select rooms, choose your style, and let Gemini 3 Pro Image (Nano Banana Pro) show you what your renovation could look like.

![Renovision Demo](./demo.png)

## Features

- 🏠 **Rightmove Integration** — Automatically extracts property images from any listing
- 🎨 **Style Selection** — Choose from Minimal, Japanese, Mid-Century, Industrial, Scandi, or Mediterranean
- 🎯 **Kitchen Customisation** — Pick your ideal cabinet colour
- ✨ **AI Transformation** — Photorealistic renovations using Gemini 3 Pro Image
- 📊 **Before/After Slider** — Interactive comparison of original vs renovated

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│   React App     │────▶│   FastAPI       │────▶│   Gemini 3      │
│   (Frontend)    │     │   (Backend)     │     │   Pro Image     │
│                 │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │                 │
                        │   Rightmove     │
                        │   (Scraping)    │
                        │                 │
                        └─────────────────┘
```

## Quick Start

### 1. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# Run the server
uvicorn main:app --reload --port 8000
```

### 2. Frontend Setup

The frontend is a single HTML file with embedded React. For development:

```bash
# Simple option: just open the file
open index.html

# Better option: use a local server
python -m http.server 3000
# Then visit http://localhost:3000
```

For production, you'd want to:
1. Build this into a proper React app with Vite
2. Deploy to Vercel/Netlify
3. Point API calls to your production backend

### 3. Get Your API Key

**Option A: Google AI Studio (Direct)**
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create a new API key
3. Add to `.env` as `GEMINI_API_KEY`

**Option B: CometAPI (Alternative)**
1. Sign up at [CometAPI](https://cometapi.com)
2. Get your API key
3. Add to `.env` as `COMET_API_KEY`

## API Endpoints

### `POST /property`
Extract images from a Rightmove listing.

```bash
curl -X POST http://localhost:8000/property \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.rightmove.co.uk/properties/123456789"}'
```

### `POST /renovate`
Generate a renovated version of a room.

```bash
curl -X POST http://localhost:8000/renovate \
  -H "Content-Type: application/json" \
  -d '{
    "image_url": "https://example.com/kitchen.jpg",
    "room_type": "Kitchen",
    "design_style": "japanese",
    "kitchen_colour": "#8B9A7D",
    "additional_notes": "Herringbone wood floors"
  }'
```

## Design Styles

| Style | Description |
|-------|-------------|
| `minimal` | Clean lines, neutral palette, uncluttered spaces |
| `japanese` | Wabi-sabi, natural materials, zen simplicity |
| `midcentury` | Retro warmth, organic curves, bold accents |
| `industrial` | Raw materials, exposed elements, urban edge |
| `scandi` | Hygge comfort, light woods, cosy textiles |
| `mediterranean` | Warm terracotta, arched doorways, rustic charm |

## Kitchen Colours

- Sage Green (`#8B9A7D`)
- Navy Blue (`#2C3E50`)
- Terracotta (`#C67B5C`)
- Warm Cream (`#F5F0E8`)
- Charcoal (`#36454F`)
- Forest Green (`#228B22`)
- Blush Pink (`#E8B4B8`)
- Ochre (`#CC7722`)

## Limitations & Notes

- **Rightmove Scraping**: Rightmove may block or rate-limit aggressive scraping. For production, consider caching results or using a proxy rotation service.
- **Image Quality**: Results depend heavily on the source image quality. Well-lit, clear photos work best.
- **Generation Time**: Each image takes ~20-40 seconds to generate at high resolution.
- **API Costs**: Gemini 3 Pro Image generation has associated costs. Monitor your usage.

## Tech Stack

- **Frontend**: React 18, Tailwind CSS, Instrument Serif + DM Sans
- **Backend**: Python 3.11+, FastAPI, httpx, BeautifulSoup
- **AI**: Gemini 3 Pro Image (Nano Banana Pro)

## Future Ideas

- [ ] Save/export renovation boards
- [ ] Side-by-side multiple style comparisons
- [ ] Cost estimation integration
- [ ] Share links for listings
- [ ] User accounts with saved properties
- [ ] Integration with Zoopla, OnTheMarket

## License

MIT — do what you want with it.

---

Built for dreamers of doer-uppers 🏠✨
