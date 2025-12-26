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
import jwt
from io import BytesIO
from typing import Optional
from datetime import datetime

import httpx
from PIL import Image
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl
from dotenv import load_dotenv
from clerk_backend_api import Clerk

# Import our Rightmove scraper
from rightmove_scraper import scrape_rightmove_listing, PropertyListing

# Load environment variables
load_dotenv()

# ============================================
# CONFIGURATION
# ============================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")

# Clerk authentication configuration
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
clerk_client = Clerk(bearer_auth=CLERK_SECRET_KEY) if CLERK_SECRET_KEY else None

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
    colour_scheme: Optional[str] = None  # Full ROYGBIV spectrum + neutrals
    flooring: Optional[str] = None  # wood_parquet, tiled, stone_slabs, polished_concrete, carpetted
    wallpaper: Optional[str] = None  # floral, geometric, striped, damask, botanical
    garden_style: Optional[str] = None  # english_cottage, naturalistic_meadow, modern_contemporary, japanese, mediterranean, woodland, urban_courtyard, wildlife_pollinator
    extra_notes: Optional[str] = None
    auto_download: Optional[bool] = False

class RenovationResponse(BaseModel):
    original_url: str
    generated_image_base64: str
    room_type: Optional[str] = None
    style: Optional[str] = None
    configuration_applied: dict = {}

# ============================================
# AUTHENTICATION
# ============================================

async def verify_clerk_session(authorization: Optional[str] = Header(None)) -> dict:
    """
    FastAPI dependency to verify Clerk session tokens.
    Extracts and validates the Bearer token from Authorization header.
    Returns user data on success, raises HTTPException on failure.
    """
    if not CLERK_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="Server misconfiguration: Authentication not properly configured"
        )

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication. Please sign in to use this service."
        )

    # Extract Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header format. Expected: Bearer <token>"
        )

    session_token = parts[1]

    try:
        print(f"[AUTH] Attempting to verify session token...")

        # Decode JWT without verification first to get the header and payload
        unverified_header = jwt.get_unverified_header(session_token)
        unverified_payload = jwt.decode(session_token, options={"verify_signature": False})

        # Get the key ID from the JWT header
        kid = unverified_header.get('kid')
        if not kid:
            raise HTTPException(status_code=401, detail="Invalid token: missing key ID")

        # Get the issuer from the JWT payload to construct the JWKS URL
        issuer = unverified_payload.get('iss')
        if not issuer:
            raise HTTPException(status_code=401, detail="Invalid token: missing issuer")

        # Construct JWKS URL from issuer (e.g., https://clerk.example.com/.well-known/jwks.json)
        jwks_url = f"{issuer.rstrip('/')}/.well-known/jwks.json"

        # Fetch Clerk's JWKS (JSON Web Key Set) to get the public key
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url)

            if response.status_code != 200:
                print(f"[AUTH] Failed to fetch JWKS: {response.status_code}")
                raise HTTPException(status_code=401, detail="Failed to verify token")

            jwks = response.json()

            # Find the matching key
            signing_key = None
            for key in jwks.get('keys', []):
                if key.get('kid') == kid:
                    # Convert JWK to PEM format for PyJWT
                    from jwt.algorithms import RSAAlgorithm
                    signing_key = RSAAlgorithm.from_jwk(json.dumps(key))
                    break

            if not signing_key:
                raise HTTPException(status_code=401, detail="Invalid token: key not found")

        # Verify and decode the JWT
        verified_claims = jwt.decode(
            session_token,
            signing_key,
            algorithms=['RS256'],
            options={"verify_signature": True, "verify_exp": True}
        )

        print(f"[AUTH] ✅ Session verified successfully. User ID: {verified_claims.get('sub')}")

        # Extract user information from JWT claims
        return {
            "user_id": verified_claims.get("sub"),
            "session_id": verified_claims.get("sid"),
            "status": "active"
        }

    except jwt.ExpiredSignatureError:
        print(f"[AUTH] ❌ Token expired")
        raise HTTPException(
            status_code=401,
            detail="Session expired. Please sign in again."
        )
    except jwt.InvalidTokenError as e:
        print(f"[AUTH] ❌ Invalid token: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Invalid session token. Please sign in again."
        )
    except HTTPException:
        raise
    except Exception as e:
        # Token invalid, expired, or revoked
        print(f"[AUTH] ❌ Token verification failed: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session. Please sign in again."
        )

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
    # CRITICAL: These prompts must NEVER mention windows, doors, beams, fireplaces, flooring, or structural elements
    # Flooring is controlled separately via the flooring toggle
    # CRITICAL: Avoid specific furniture items (sofas, chairs, tables) - describe style aesthetic only to prevent inappropriate furniture being added to wrong room types
    style_prompts = {
        'english_contemporary': 'Transform this room into an English Contemporary style that balances traditional British sensibility with modern restraint. If there are sofas or armchairs, replace them with pieces featuring clean lines but with subtle curves, upholstered in rich textured fabrics like bouclé, heavyweight linen, or soft wool in warm neutrals such as oatmeal, warm grey, soft camel, or muted olive. If there is a bed, change it to an upholstered frame with a gently curved or subtly winged headboard in a textured neutral fabric, dressed with layered white and cream linens with a structured throw in a heritage colour like burnt sienna or forest green. If there are dining chairs, update them to be elegantly simple with gentle curves, perhaps in oak or walnut with linen seat cushions. If there are side tables or coffee tables, replace with pieces in warm-toned timber like oak or walnut with refined proportions, possibly with subtle brass or aged bronze detailing. If there are lamps, change to ceramic table lamps with organic shapes in cream, sage, or warm terracotta with natural linen shades, or sculptural floor lamps with brass stems and fabric shades. If there are curtains or window treatments, replace with full-length linen or wool curtains in soft cream, warm grey, or muted green that puddle slightly on the floor. If there is a rug, change to a high-quality wool rug in a subtle tone-on-tone pattern or solid neutral with interesting texture. If there are bookshelves or storage units, replace with built-in cabinetry painted in warm off-white or soft grey-green, or freestanding pieces in natural timber with brass hardware. Paint walls in warm whites like plaster pink, pale putty, or soft stone. If there are mirrors, update to simple frames in aged brass, warm bronze, or natural timber. Replace any harsh overhead lighting with warm ambient sources. Add texture through the contrast of smooth plaster walls against natural linen, wool textiles, and matte timber surfaces. The overall effect should feel collected, intelligent, quietly luxurious, and effortlessly refined without feeling decorated.',

        'modern_organic': 'Transform this room into a Modern Organic style that celebrates natural materials, sculptural forms, and an earthy, grounded aesthetic. If there are sofas or armchairs, replace them with low-profile pieces featuring curved, embracing silhouettes upholstered in natural fabrics like undyed linen, hemp, raw cotton, or soft leather in tones of warm sand, clay, terracotta, soft mushroom, or warm cream. If there is a bed, change it to a low platform style in solid timber with visible grain, perhaps with a curved or rounded headboard in natural wood or upholstered in a textural fabric, dressed with stonewashed linen bedding in earthy neutrals layered with a chunky knit or handwoven throw. If there are dining chairs, update them to sculptural wooden pieces with organic curves and visible joinery, or woven designs using natural rattan, cane, or rope seats. If there are coffee tables or side tables, replace with sculptural pieces in raw-edged timber, travertine, cast concrete, or hand-carved stone with organic shapes and natural imperfections celebrated rather than hidden. If there are lamps, change to sculptural ceramic pieces with unglazed or matte finishes in cream, terracotta, or charcoal, paper lantern styles, or organic sculptural forms in alabaster or natural stone. If there are curtains, replace with relaxed, unlined linen panels in natural off-white or warm sand that filter light softly. If there is a rug, change to a handwoven jute, sisal, or wool piece with visible texture, perhaps in a natural tone or soft terracotta, or a vintage Berber or Beni Ourain style with organic patterns. If there are shelving units, replace with floating timber shelves with live edges or recessed niches with limewash plaster walls to display ceramic vessels and found natural objects. Apply limewash or microcement in warm earth tones like soft terracotta, warm sand, or pale clay to walls for depth and natural texture. If there are mirrors, update to organic asymmetrical shapes or pieces framed in raw timber or wrapped in natural rope. Replace any standard lighting with warm, diffused sources. Incorporate natural textures throughout: raw linen, unpolished stone, handmade ceramics, woven baskets, and dried botanical elements. The overall effect should feel rooted, tactile, warmly primitive, and connected to the earth while maintaining contemporary sophistication.',

        'scandinavian_minimalism': 'Transform this room into a Scandinavian Minimalist style that embodies functional simplicity, quiet beauty, and a sense of calm restraint. If there are sofas or armchairs, replace them with clean-lined pieces in pale grey, soft white, warm sand, or muted sage, upholstered in quality natural fabrics like bouclé, wool, or heavy cotton, with exposed wooden legs in pale ash, light oak, or birch. If there is a bed, change it to a simple timber frame in pale natural wood like ash or whitewashed oak with clean geometric lines, dressed with pure white or soft grey linen bedding, layered simply with a single textural wool throw in cream or soft grey. If there are dining chairs, update them to iconic Scandinavian designs featuring bent plywood, pale timber, and gentle curves, prioritising ergonomic beauty and craftsmanship. If there are coffee tables or side tables, replace with simple geometric forms in pale oak, ash, or birch with exceptional joinery and smooth surfaces, or paired with white marble or pale concrete. If there are lamps, change to sculptural modern designs in matte white, pale grey, or natural timber, with simple geometric or organic forms like pendant lights with opal glass globes, minimalist floor lamps with slim profiles, or paper lanterns. If there are curtains, replace with sheer white linen panels that maximise natural light while softening the windows, or simple roller blinds in white or pale grey. If there is a rug, change to a flat-weave wool rug in pale grey, cream, or soft white with minimal pattern, or a natural wool sheepskin for textural warmth. If there are storage units, replace with clean-lined pieces in white with timber accents, or pale timber cabinets with handleless fronts emphasising uninterrupted surfaces. Paint all walls in pure white or the softest warm grey to maximise light reflection. If there are mirrors, update to simple round or rectangular shapes with thin pale timber frames or frameless designs. Replace all lighting with warm white sources around 2700K to create hygge warmth. Reduce visual clutter dramatically, leaving only essential pieces and a few carefully chosen objects of functional beauty. The overall effect should feel serene, light-filled, thoughtfully edited, and quietly beautiful, where every object earns its place through both function and aesthetic contribution.',

        'japandi': 'Transform this room into a Japandi style that fuses Japanese wabi-sabi philosophy with Scandinavian functionality, creating a serene, soulful, and impeccably considered space. If there are sofas or armchairs, replace them with low-profile pieces featuring clean geometric lines softened by subtle curves, upholstered in natural fabrics like heavyweight linen, cotton, or wool in warm neutrals such as charcoal, warm grey, soft ecru, or muted moss green, with exposed frames in dark walnut, smoked oak, or blackened timber. If there is a bed, change it to a low platform frame close to the ground in dark-stained walnut, charcoal oak, or natural light timber with strong horizontal lines and minimal ornamentation, dressed with natural linen bedding in cream, soft grey, or warm white with a single textural throw in a complementary natural tone. If there are dining chairs, update them to refined timber designs with woven paper cord, rush, or leather seats in the Danish tradition, or low Japanese-inspired stools in dark wood with subtle craftsmanship details. If there are coffee tables or side tables, replace with low, grounded pieces in dark timber with visible grain or natural stone like grey granite or dark slate, featuring clean lines and subtle asymmetry that embraces imperfection. If there are lamps, change to sculptural pieces in handmade ceramics with natural glazes in cream, grey, or black, rice paper lanterns, or simple timber and metal designs with warm diffused light. If there are curtains, replace with simple linen panels in natural cream or soft charcoal, hung simply from minimal hardware, or rice paper screens for filtered light. If there is a rug, change to a low-pile wool piece in charcoal, cream, or natural undyed wool with subtle texture, or a traditional tatami-inspired natural fibre mat. If there are storage units, replace with pieces featuring sliding doors or push-to-open mechanisms to eliminate visible hardware, in dark timber or combinations of dark wood with cream or paper panels. Apply walls in soft limewash in warm cream, pale grey, or soft charcoal, or leave as natural plaster with subtle texture. If there are mirrors, update to simple shapes framed in dark timber or blackened metal with clean lines. Embrace negative space deliberately, leaving breathing room around furniture and art. Include handcrafted objects that show the maker\'s hand: ceramics with irregular glazes, hand-thrown pottery, or timber pieces with visible joinery. The overall effect should feel contemplative, quietly sophisticated, grounded, and infused with the beauty of restraint, imperfection, and natural materials.',

        'parisian_classic': 'Transform this room into a Parisian Classic style that evokes the timeless elegance of Haussmann-era apartments with their romantic tension between ornate heritage and confident modern living. If there are sofas or armchairs, replace them with elegant French silhouettes featuring curved arms, turned legs, and refined proportions, upholstered in luxurious fabrics like velvet, silk, or fine linen in sophisticated tones such as deep navy, soft blush, warm grey, cream, or muted gold, with frames in gilded wood, painted white, or natural oak showing gentle wear. If there is a bed, change it to an upholstered frame with a tall, dramatic headboard in buttoned velvet or linen in soft grey, blush, or cream, or an ornate antique frame in painted white or gilded wood, dressed with crisp white cotton sheets layered with soft quilted coverlets and plush cushions. If there are dining chairs, update them to Louis XV or XVI inspired designs with cabriole legs and cane or upholstered backs in velvet or linen, mixing matched sets with occasional collected antique pieces. If there are coffee tables or side tables, replace with antique or antique-inspired pieces featuring marble tops, gilded bases, carved timber, or elegant brass and glass combinations with ornate detailing. If there are lamps, change to crystal or brass chandeliers, elegant sconces with fabric shades, or classic table lamps with marble, brass, or ceramic bases and pleated silk shades in cream or soft colours. If there are curtains, replace with full, generous panels in silk, velvet, or heavy linen in cream, soft grey, or muted colours, hung high near the ceiling and puddling gracefully on the floor, perhaps with subtle tiebacks. If there is a rug, change to an antique or vintage-inspired piece, perhaps an Aubusson, Persian, or French needlepoint design in soft, faded colours with ornate patterns. If there are bookcases or storage, replace with ornate carved pieces in painted white or natural timber, or built-in shelving with classical moulding details. Apply soft, sophisticated paint colours to walls: soft grey, pale French blue, antique white, or soft blush, with decorative mouldings, cornices, and panelling emphasised through subtle tonal contrast. If there are mirrors, update to ornate gilded frames, large-scale antique pieces, or trumeau mirrors positioned to reflect light. Install herringbone parquet flooring if replacing floors. Layer in collected antique objects, vintage books, fresh flowers, and classical artwork in gilded frames. The overall effect should feel romantically elegant, intellectually sophisticated, confidently collected over time, and effortlessly glamorous without feeling museum-like or precious.',

        'coastal_elevated': 'Transform this room into an Elevated Coastal style that evokes the sophistication of a refined seaside residence, moving far beyond typical nautical clichés to embrace the natural beauty, light, and serenity of coastal living with quiet luxury. If there are sofas or armchairs, replace them with relaxed yet refined pieces featuring clean lines with soft, sink-in comfort, upholstered in high-quality natural fabrics like heavy linen, cotton, or soft bouclé in tones of warm white, soft sand, pale grey, weathered blue, or soft seafoam green, with exposed legs in natural pale timber, whitewashed wood, or weathered grey oak. If there is a bed, change it to an elegant frame in natural timber, whitewashed wood, or upholstered in natural linen with a relaxed headboard, dressed with crisp white linen bedding layered with soft blue or sandy neutral throws and European pillows in natural textures. If there are dining chairs, update them to relaxed elegant designs in natural rattan, woven rope, or pale timber with linen cushions in soft neutrals, mixing organic textures with refined lines. If there are coffee tables or side tables, replace with pieces in natural materials like driftwood-inspired timber, white marble, natural stone, or cerused oak with organic shapes or clean lines, perhaps incorporating natural elements like coral-inspired forms or shell-textured surfaces. If there are lamps, change to sculptural pieces in natural materials like ceramic in sandy tones, woven rattan pendants, organic glass forms in soft blue or clear tones, or brass with weathered patina, all with natural linen or parchment shades creating warm, diffused light. If there are curtains, replace with billowing sheer linen panels in white or natural cream that move gently with air, hung generously to maximise the sense of light and air. If there is a rug, change to natural sisal, jute, or seagrass for organic texture, or soft wool in sandy neutrals or soft blue with subtle patterns reminiscent of water or sand ripples. If there are storage units, replace with relaxed pieces in whitewashed timber, natural rattan, or white lacquer with organic textures and natural hardware in brass or bronze. Paint walls in warm whites, soft sandy neutrals, palest grey, or the softest hint of sea blue, using flat or matte finishes for natural depth. If there are mirrors, update to frames in weathered timber, natural rope, or simple brass that reference maritime heritage subtly. Incorporate natural coastal textures: linen, rope, woven fibres, bleached timber, shells, and coral-inspired ceramics as accents. Maximise the sense of light and air throughout. The overall effect should feel effortlessly sophisticated, serene, light-filled, and connected to the sea without any literal nautical references, like a beautifully appointed home that happens to sit by the ocean.',

        'midcentury_modern': 'Transform this room into a Mid-Century Modern style that captures the optimistic, design-forward spirit of the 1950s and 1960s with its celebration of clean lines, organic forms, and innovative craftsmanship. If there are sofas or armchairs, replace them with iconic low-profile silhouettes featuring tapered legs in walnut or teak, clean geometric or organic curved forms, and upholstery in period-appropriate fabrics like wool, leather, or textured cotton in warm earth tones such as burnt orange, mustard yellow, olive green, warm brown, or charcoal grey, or in classic neutrals with a pop of colour on accent chairs. If there is a bed, change it to a low platform design with a slim, geometric headboard in warm-toned timber like walnut, teak, or rosewood veneer, with tapered legs and minimal ornamentation, dressed with simple bedding in warm neutrals accented with a bold graphic throw or cushion. If there are dining chairs, update them to iconic designs featuring moulded plywood, fibreglass shells, or elegant timber frames with organic curves and minimal profiles, showcasing the era\'s innovative materials and manufacturing techniques. If there are coffee tables or side tables, replace with classic designs featuring sculptural timber bases, organic kidney or surfboard shapes, glass tops with angled legs, or sleek low profiles in walnut, teak, or rosewood with brass or hairpin leg details. If there are lamps, change to iconic designs of the era: arc floor lamps, Sputnik chandeliers, globe pendants, mushroom table lamps, or sculptural ceramic pieces in period colours, all emphasising form as art. If there are curtains, replace with simple flat panels in solid colours or bold geometric patterns characteristic of the era, or leave windows unadorned to maximise the indoor-outdoor connection. If there is a rug, change to a bold geometric or abstract pattern in warm period colours like orange, gold, olive, and brown, or a high-quality shag rug in a solid warm tone for textural interest. If there are storage units, replace with credenzas, sideboards, or wall units in warm timber with clean lines, tapered legs, and interesting hardware details in brass or chrome, featuring sliding doors, drop-down fronts, or open shelving sections. Apply warm white or soft cream to walls to let furniture stand as art, or add an accent wall in a bold period colour like mustard, teal, or burnt orange. If there are mirrors, update to simple geometric shapes like sunbursts or asymmetrical organic forms in brass or teak frames. Incorporate natural indoor plants, particularly architectural specimens like fiddle-leaf figs or monstera. Display vintage art, bold graphic prints, or abstract expressionist works. The overall effect should feel optimistic, artfully modern, warm yet sophisticated, and celebratory of the era\'s remarkable design innovation and craftsmanship.',

        'moody_contemporary': 'Transform this room into a Moody Contemporary style that embraces bold darkness, dramatic sophistication, and the enveloping intimacy of richly saturated spaces while maintaining contemporary edge. If there are sofas or armchairs, replace them with sculptural contemporary pieces featuring bold proportions and interesting angles, upholstered in luxurious fabrics like velvet, heavy bouclé, or supple leather in deep dramatic tones such as charcoal, inky navy, forest green, burgundy, or pure black, with exposed frames in blackened metal, dark stained timber, or brass for contrast. If there is a bed, change it to a statement piece with a tall, dramatic upholstered headboard in deep velvet in charcoal, navy, or forest green, or a bold contemporary frame in blackened wood or metal, dressed with layered bedding in rich dark tones with textural throws and cushions in complementary deep colours. If there are dining chairs, update them to sculptural contemporary designs in dark leather, velvet, or heavy fabric with interesting structural details in blackened metal or dark timber, mixing matching sets with occasional statement pieces. If there are coffee tables or side tables, replace with bold contemporary pieces in dark marble like nero marquina, blackened steel, smoked glass, or dark-stained timber with architectural presence and interesting proportions. If there are lamps, change to sculptural statement pieces in blackened metal, dark glass, or marble with warm diffused light, dramatic oversized pendants, or contemporary sconces creating pools of light against dark walls. If there are curtains, replace with floor-to-ceiling panels in heavy velvet or wool in deep colours that absorb light and create intimacy, or dark linen for a slightly softer approach. If there is a rug, change to a high-quality piece in deep charcoal, black, or rich jewel tones with subtle texture or an abstract contemporary pattern in tonal darks. If there are storage units, replace with contemporary pieces featuring dark finishes, integrated lighting, and interesting proportions in blackened timber, dark lacquer, or metal with brass or bronze accent details. Paint walls in deeply saturated colours: charcoal, inky blue, forest green, deep burgundy, or pure black, using flat or matte finishes for depth. Consider dark ceiling treatment to create full envelopment. If there are mirrors, update to contemporary frames in blackened metal or antiqued glass that add depth without breaking the darkness. Layer lighting carefully with dimmers, creating pockets of warm light against the darkness. Add metallic accents in brass, bronze, or gold for warmth and reflected light. Incorporate luxurious textures in velvet, fur, heavy linen, and leather to add tactile richness. The overall effect should feel dramatically sophisticated, intimately cocooning, boldly confident, and unexpectedly comforting in its embrace of darkness.',

        'rustic_modern': 'Transform this room into a Rustic Modern style that harmonises raw, natural, heritage-rich elements with clean contemporary design, creating spaces that feel both rooted in history and entirely current. If there are sofas or armchairs, replace them with generously proportioned pieces featuring clean contemporary lines, upholstered in natural, substantial fabrics like heavyweight linen, thick cotton, or aged leather in warm earth tones such as caramel, warm brown, charcoal, cream, or soft terracotta, with frames that might incorporate reclaimed timber elements or blackened metal. If there is a bed, change it to a substantial frame in reclaimed timber showing natural wear, aged patina, and honest joinery, or a contemporary iron frame with rustic warmth, dressed with natural linen bedding in cream or soft neutrals layered with chunky knit throws and textural cushions in earthy tones. If there are dining chairs, update them to honest, substantial designs mixing vintage character pieces like old wooden farmhouse chairs or industrial metal stools with contemporary wooden designs, embracing mismatched character over matched perfection. If there are coffee tables or side tables, replace with pieces celebrating natural materials: reclaimed timber with live edges or visible history, raw stone or concrete with natural imperfections, or blackened metal with honest construction, showing craftsmanship and material authenticity. If there are lamps, change to pieces featuring natural materials like turned timber, handmade ceramics with rustic glazes, or industrial blackened metal with exposed bulbs or natural fabric shades, creating warm pools of light. If there are curtains, replace with relaxed natural linen panels in undyed or soft neutral tones, hung simply and left unpressed for natural texture. If there is a rug, change to a natural jute or sisal piece for organic texture, a vintage kilim or antique rug with faded character, or a handwoven wool piece in natural earth tones. If there are storage units, replace with pieces combining raw materials: reclaimed timber and blackened metal, weathered wood with contemporary hardware, or vintage industrial pieces repurposed for modern living. Apply walls in natural plaster with visible texture and imperfection, limewash in warm earth tones, or simple white to let natural materials stand out. Expose any existing architectural bones: timber beams, brick walls, stone features. If there are mirrors, update to simple frames in reclaimed timber, blackened metal, or left unframed for contemporary edge against rustic materials. Incorporate patina and age throughout: vintage finds, antique tools as decoration, collected natural objects, and inherited pieces with stories. The overall effect should feel warmly authentic, honestly crafted, connected to the land and to history, while remaining edited, contemporary, and sophisticated in its restraint.',

        'hals_choice': 'Transform this room into Hal\'s Choice style, a refined yet soulful aesthetic that marries the warmth of English heritage homes with mid-century craftsmanship and organic materiality, creating spaces that feel intellectually curated, quietly luxurious, and deeply lived-in. This style embraces the tension between traditional architectural bones and contemporary sensibility, celebrating natural timber, sage and olive greens, warm brass, and the patina of collected life. Architectural Elements: If there are doors or door frames, change them to painted timber in deep sage green, olive, or soft forest tones with traditional panelling and aged brass hardware including knurled knobs and lever handles. If there are stairs, update the balustrade to feature slim black or dark metal spindles with a warm timber handrail in oak or walnut, and consider painting the risers in a contrasting tone while leaving treads in natural timber. If there are skirting boards and architraves, ensure they are substantial and traditionally proportioned, painted in warm white or picking up accent colours from doors and built-ins. If there is potential for a skylight or rooflight, incorporate a generous flat rooflight or lantern to flood the space with natural light from above, framed simply in white or black metal. If there are hallways or corridors, treat them as gallery spaces with considered artwork arrangements and runner rugs. Flooring: If there is flooring, change it to engineered oak in a warm, honeyed tone laid in herringbone or chevron pattern with a subtle matte or natural oil finish that shows grain variation and develops character over time. If carpet exists in bedrooms, consider replacing with the same warm oak herringbone for continuity, softened with layered rugs. If there is existing parquet, restore and celebrate it. Walls and Paint: Apply walls in warm, sophisticated neutrals with depth and complexity: soft putty, warm greige, gentle mushroom, or pale plaster tones that shift with daylight. For accent walls or joinery, introduce muted heritage greens such as sage, soft olive, or deep forest green. Avoid stark whites in favour of warm whites with underlying cream, grey, or pink tones. If there is panelling potential, consider adding tongue-and-groove to dado height in hallways, bathrooms, or behind beds, painted in either the wall colour for subtlety or a contrasting sage or green for definition. Built-in Storage and Shelving: If there are storage opportunities, create bespoke built-in shelving and cabinetry with traditional proportions, featuring adjustable shelves for books, integrated cabinetry below with panelled doors, and subtle brass cup handles or knurled knobs. Paint in warm white, soft grey, or sage green. Style shelves as working libraries with real books arranged naturally alongside ceramics, framed photographs, and collected objects rather than decorative perfection. If there are alcoves, maximise them with floor-to-ceiling built-ins featuring a mix of open shelving and closed storage. Sofas and Seating: If there are sofas, replace them with generously proportioned, sink-in comfortable pieces featuring soft, rounded arms and a relaxed silhouette, upholstered in rich textural fabrics like heavyweight linen, bouclé, or soft velvet in warm tones such as ochre, burnt sienna, terracotta, olive, or warm chocolate brown. Legs should be in warm timber or hidden. If there are armchairs, choose pieces with character: vintage leather club chairs with patina, upholstered reading chairs in complementary fabrics, or mid-century inspired pieces in walnut frames with fabric or leather cushions. Mix matched pairs with individual character pieces. Beds: If there is a bed, change it to a simple, considered frame in warm oak or walnut with clean lines and honest joinery, or an upholstered headboard in natural linen, soft bouclé, or muted velvet in cream, soft grey, or sage green. Keep the profile relatively low and unfussy. Dress with high-quality natural linen bedding in white or soft cream, layered with a quilted coverlet and textural throw in a heritage colour, and European pillows in complementary natural fabrics. Dining Furniture: If there is a dining table, replace with a substantial piece in warm oak or walnut with honest construction and clean lines, potentially featuring turned legs or subtle mid-century influence, sized generously for gathering. If there are dining chairs, choose a mix: perhaps a set of simple timber chairs with woven rush or cane seats alongside a bench or a couple of upholstered end chairs, embracing the collected-over-time aesthetic rather than rigid matching. Coffee Tables and Side Tables: If there are coffee tables, replace with substantial pieces in warm timber showing natural grain, potentially with lower shelf for books and objects, or vintage pieces with character and patina. If there are side tables, choose a mix of warm timber, aged brass, or vintage finds that complement rather than match, sized appropriately for lamps and books. Kitchen Elements: If there is a kitchen, transform cabinetry to feature shaker-style doors in deep sage green, olive, or warm putty with unlacquered brass or aged bronze hardware including cup pulls on drawers and knurled knobs on doors. Countertops should be in honed natural stone, warm-toned quartz, or butcher block timber for a preparation area. If there is a backsplash, use vertical format tiles in sage green, soft teal, or warm cream in a classic brick or stack pattern with subtle variation. Open timber shelving in oak or walnut should display everyday ceramics. Integrate appliances where possible, or choose characterful options in cream or stainless steel. Include warm timber details: floating shelves, a butcher block section, or timber drawer fronts mixed with painted. Bathroom Elements: If there is a bathroom, introduce zellige or handmade tiles in soft green, sage, or warm cream with natural variation and imperfect charm, particularly in shower areas or as a feature wall behind the bath. Cabinetry should be painted timber vanities in warm tones with marble or stone countertops and brass hardware. Fixtures and fittings in aged brass, unlacquered brass, or brushed gold with traditional silhouettes updated for contemporary function. If there is a bath, consider a freestanding option or a built-in with a tiled surround. Use warm timber accents: a bath shelf, a stool, or open shelving for towels. Flooring in natural stone, encaustic tiles with subtle pattern, or warm wood-effect porcelain. Lighting: If there are ceiling lights, replace with a considered mix: aged brass pendants with glass or opal shades over dining and kitchen islands, simple flush or semi-flush fixtures in brass or black metal in circulation spaces, and perhaps a statement pendant in hallways or stairwells. If there are table lamps, choose ceramic bases with subtle glazes in cream, sage, or terracotta with natural linen shades, or sculptural pieces in brass or timber. If there are floor lamps, select reading lamps with brass stems and fabric shades, or mid-century inspired designs in warm materials. Add wall sconces in aged brass with fabric shades in living spaces and bedrooms. Ensure all lighting is warm in temperature, around 2700K, and on dimmers where possible. Rugs and Textiles: If there are rugs, layer antique or vintage-inspired Persian, Turkish, or kilim rugs with characterful worn patterns in reds, blues, and earth tones over the timber flooring, particularly in living areas, hallways, and bedrooms. These should feel inherited rather than purchased, with soft fading and honest wear. If there are throws and cushions, use natural fabrics: heavyweight linen, wool, bouclé, and velvet in warm earth tones, burnt orange, olive, mustard, and warm neutrals, arranged with relaxed intention. Window Treatments: If there are curtains, replace with full-length linen panels in natural, cream, or soft muted tones, hung high and generously, puddling slightly on the floor for relaxed elegance. In kitchens and bathrooms, use simple blinds in natural linen or consider leaving windows unadorned if privacy permits. Mirrors and Artwork: If there are mirrors, choose simple frames in warm timber, aged brass, or black metal with traditional proportions, or vintage mirrors with foxed glass and character. Hang artwork in considered gallery arrangements mixing frames: thin black, warm timber, gilded vintage, creating a collected-over-time effect. Include a mix of artwork: landscapes, portraits, prints, photographs, and objects, hung at varied heights and groupings that feel evolved rather than designed. Objects and Styling: Style surfaces with intention but without preciousness: stacks of well-read books with worn spines, ceramic vessels from makers, brass objects with patina, potted plants in simple terracotta or ceramic pots, framed family photographs, and collected objects from travels that tell stories. Bookshelves should be filled with real books, mostly hardback, arranged naturally with occasional objects breaking up the rows. Include living plants throughout: fiddle-leaf figs, trailing pothos, or architectural specimens in characterful pots. Hardware and Details: All hardware throughout in aged brass, unlacquered brass, or antique bronze with traditional forms: cup pulls on drawers, knurled knobs on doors, substantial hinges visible where appropriate. Light switches and sockets in brass or in discrete finishes that disappear into walls. Ensure all metalwork has warmth and patina potential rather than polished perfection. The overall effect should feel like a home that has been thoughtfully assembled over years by someone with quiet but certain taste: warm, intellectual, deeply comfortable, and entirely personal. It should suggest a life of reading, cooking, gathering, and considered living, where every piece has been chosen rather than merely placed, and where the aesthetic serves life rather than demanding attention. The space should feel both timeless and contemporary, equally appropriate for a dinner party or a solitary afternoon with a book, embodying the refined sensibility of English tradition filtered through a modern, organic lens.',
    }

    # Room Type descriptions (for furniture context only)
    room_type_furniture = {
        'living': 'appropriate living room furniture like sofa, coffee table, ambient lighting',
        'bedroom': 'appropriate bedroom furniture like bed, bedside tables, soft lighting',
        'kitchen': 'appropriate kitchen elements like updated cabinets, countertops, modern appliances. Only add a kitchen island if there is sufficient space. If no space for an island, try a peninsula. If no space for either, keep as a galley or simple layout with no kitchen table. Prioritise cabinet layout, flow, and storage composition.',
        'dining': 'appropriate dining furniture like table and chairs, statement lighting',
        'bathroom': 'appropriate bathroom fixtures like updated vanity, modern taps, quality tiles',
        'office': 'appropriate office furniture like desk, ergonomic chair, task lighting',
        'hallway': 'MINIMAL hallway elements ONLY - possibly a console table if space genuinely allows, but NO other furniture whatsoever',
        'garden': 'landscaped garden features',
        'outdoor': 'outdoor furniture and landscaping elements',
    }
    
    # Time of day lighting descriptions - ONLY changes lighting, colors, and hues
    # DO NOT change physical elements or what's visible outside windows
    time_of_day_prompts = {
        'day': 'DAYLIGHT LIGHTING: Apply bright, natural daylight illumination throughout the interior. Use soft, even lighting with minimal shadows. If windows exist, apply cool natural daylight color temperature to the light coming through them - DO NOT change what is visible outside the windows, only adjust the color and brightness of the light itself. The interior should feel fresh, airy, and naturally lit with cool neutral tones.',

        'night': 'NIGHT LIGHTING: Apply warm artificial interior lighting (ceiling lights, lamps, downlights) as the primary light source. If windows exist, darken the light coming through them to indicate nighttime - DO NOT change what is visible outside the windows, only adjust the darkness/brightness of the window areas to appear darker. Use warm amber lighting tones for interior fixtures. Create cozy evening ambiance with warm artificial light.',

        'golden_hour': 'SUBTLE GOLDEN HOUR LIGHTING: Apply gentle, warm golden-hour illumination with soft peachy-orange color cast throughout the scene. If windows exist, add warm golden light streaming through with SUBTLE directional quality - DO NOT change what is visible outside the windows, only apply warm golden-hour color grading to the light. Use soft, natural warm tones (NOT oversaturated or cinematic). Keep the effect realistic and understated - this should look like natural late-afternoon sunlight, not dramatic cinema lighting. Avoid harsh shadows or overly bright highlights.',
    }

    # Colour scheme palettes - Full ROYGBIV spectrum
    # CRITICAL: Only describe paint colors, NEVER mention "accent wall" or structural changes
    colour_scheme_prompts = {
        # Neutrals & Whites
        'soft_linen': 'soft warm linen white color palette, clean calm atmosphere with subtle warmth',
        'cream_core': 'rich cream color palette, warm inviting atmosphere',
        'nordic_mist': 'pale grey-white color palette, Scandinavian bright minimalism',
        'warm_grey': 'warm grey color palette with taupe undertones, cozy neutral sophistication',
        'charcoal': 'deep charcoal grey color palette, dramatic moody elegance with warm lighting',

        # Reds & Pinks
        'burgundy_depth': 'deep burgundy color palette, luxurious moody tones with rich depth',
        'crimson_red': 'bold crimson red color palette, vibrant statement color with neutral balance',
        'blush_pink': 'soft blush pink color palette, romantic feminine warmth',
        'dusty_rose': 'dusty rose color palette, muted pink with earthy sophistication',

        # Oranges & Corals
        'terracotta_sun': 'warm terracotta color palette, sun-baked Mediterranean warmth',
        'burnt_orange': 'burnt orange color palette, bold autumnal richness',
        'coral_reef': 'coral color palette, vibrant tropical warmth with energy',

        # Yellows & Golds
        'amber_glow': 'warm amber yellow color palette, vibrant golden energy',
        'sunshine_yellow': 'bright sunshine yellow color palette, cheerful optimistic warmth',
        'mustard': 'mustard yellow color palette, vintage warm sophistication',

        # Greens
        'sage_calm': 'soft sage green color palette, calming natural tones',
        'olive_grove': 'olive green color palette, earthy natural sophistication',
        'forest_green': 'dark forest green color palette, rich jewel tone with warm brass accents',
        'emerald': 'emerald green color palette, luxurious jewel tone vibrancy',

        # Blues
        'midnight_blue': 'midnight blue color palette with warm brass or gold accents, deep sophisticated navy',
        'sky_blue': 'sky blue color palette, fresh airy coastal lightness',
        'teal': 'teal color palette, sophisticated blue-green balance with depth',
        'navy': 'navy blue color palette, classic nautical depth with warm contrast',

        # Purples & Violets
        'lavender': 'soft lavender color palette, gentle purple with calming elegance',
        'plum': 'rich plum purple color palette, luxurious jewel tone sophistication',
        'aubergine': 'deep aubergine color palette, dramatic dark purple with moody warmth',
    }

    # Flooring descriptions
    flooring_prompts = {
        'wood_parquet': 'wood parquet flooring in herringbone or chevron pattern',
        'tiled': 'large format ceramic or porcelain tiles',
        'stone_slabs': 'natural stone slab flooring',
        'polished_concrete': 'polished concrete floors with subtle sheen',
        'carpetted': 'high-quality carpeted flooring with plush texture',
    }

    # Wallpaper descriptions
    wallpaper_prompts = {
        'floral': 'elegant floral wallpaper with delicate botanical pattern, sophisticated and refined',
        'geometric': 'modern geometric wallpaper with clean lines and abstract patterns, contemporary style',
        'striped': 'classic striped wallpaper with vertical lines, timeless and elegant',
        'damask': 'traditional damask wallpaper with ornate repeating patterns, luxurious heritage style',
        'botanical': 'lush botanical wallpaper with large-scale leaf and plant motifs, tropical sophistication',
    }

    # Garden style descriptions
    garden_style_prompts = {
        'english_cottage': 'Transform into a romantic English Cottage Garden. Create billowing mixed borders filled with roses, lavender, foxgloves, and delphiniums in abundant layers. Add winding natural paths that curve gently through the space. Include climbing plants over arches or pergolas if space allows. Place a hidden bench or seating nook. The overall feel should be joyfully abundant, gently messy in the best way, with a "just discovered this secret garden" romantic atmosphere.',

        'naturalistic_meadow': 'Transform into a calm Naturalistic Meadow Garden. Plant informal drifts of ornamental grasses (like Stipa, Molinia) mixed with perennial wildflowers (like Echinacea, Verbena, Achillea). Create sweeping naturalistic planting that feels seasonal and ever-changing. Add soft curving mown paths through taller grasses. Include a wildlife pond if space permits. The overall feel should be modern-wild, serene, and nature-led with movement and texture.',

        'modern_contemporary': 'Transform into a clean Modern Contemporary Garden. Use sharp geometric lines and clear architectural forms throughout. Install large-format paving slabs or composite decking in neutral tones. Add raised rectangular planters with limited plant palette (architectural grasses like Miscanthus, or Japanese maples). Include a minimalist water feature (rill or reflecting pool). Add sleek outdoor lounge furniture. Install dramatic LED strip lighting for evening drama. The overall feel should be sculptural, confident, and sophisticatedly minimal.',

        'japanese': 'Transform into a quietly spiritual Japanese Garden. Lay natural stone paths or stepping stones with careful placement. Plant clipped evergreen shrubs (pines, junipers, box) with precise sculptural pruning. Add low mossy ground cover and gravel areas raked in patterns. Include a reflecting pond or water bowl if space allows. Place a stone lantern as a focal point. Frame key views with careful plant placement to guide contemplation. The overall feel should be precise without stiffness, balanced asymmetrically, deeply calm and meditative.',

        'mediterranean': 'Transform into a sun-bleached Mediterranean Garden. Plant olive trees or fig trees as structural anchors. Add lavender, rosemary, and other aromatic drought-tolerant herbs throughout. Use gravel as ground cover in warm terracotta tones. Place terracotta pots clustered in groups. Include low stone walls or rendered walls in warm ochre. Add a water bowl or simple fountain. Install a shaded dining pergola with climbing vines. The overall feel should be relaxed, permanently on holiday, sun-soaked and aromatic.',

        'woodland': 'Transform into an enclosed atmospheric Woodland Garden. Preserve or enhance existing mature trees to create dappled shade canopy. Plant layered understory with ferns, hostas, and shade-loving perennials. Add meandering bark-mulch paths that curve naturally. Include natural moss-covered stone seating or log benches. Plant spring bulbs (bluebells, snowdrops) and hellebores for seasonal drama. Create pockets that feel like secret hideaways. The overall feel should be textural, enclosed, deeply atmospheric and mysteriously beautiful.',

        'urban_courtyard': 'Transform into a sophisticated Urban Courtyard Garden. Maximize vertical space with wall-mounted planters and climbing plants on trellises. Create bold container cluster arrangements at multiple heights. Install sleek paving covering most of the floor, softened by strategic greenery. Add compact bistro furniture or a modern lounge chair. Use mirrors on walls to create depth illusion. Install clever uplighting and string lights for nighttime atmosphere. The overall feel should be small-space sophistication with attitude, layered and stylish.',

        'wildlife_pollinator': 'Transform into a buzzing Wildlife/Pollinator Garden. Plant wildflower meadow sections with native species rich in nectar. Add mixed hedgerows with berries and seeds for birds. Include a wildlife pond with gentle sloping edges. Place log piles and insect hotels in corners. Plant seed-rich flowers (Rudbeckia, Sedum, Verbena). Leave some unmanicured edges and corners deliberately wild. The overall feel should be life-first, soulful, abundant with wildlife, trading rigid tidiness for living richness and biodiversity.',
    }
    
    # Build the prompt - FOCUS ON EDITING, NOT GENERATING
    
    # Special handling for garden/outdoor spaces
    if request.room_type in ['garden', 'outdoor']:
        prompt_parts = [
            "CRITICAL NON-NEGOTIABLE RULE - DOORS AND WINDOWS: If there are ANY doors or windows visible in this outdoor space, you MUST leave them EXACTLY where they are - same position, same size, same shape, same number. DO NOT move, resize, add, or remove ANY doors or windows. ABSOLUTELY NO NEW WINDOWS OR DOORS OF ANY KIND.",
            "EDIT THE PROVIDED PHOTOGRAPH of this outdoor space. Do not create a new image - modify the existing photo only.",
            "Keep the EXACT same garden boundaries, fences, walls, and structures in their current positions.",
            "Keep the EXACT same camera angle and perspective as the input photo.",
            "Repair and refinish any existing fences to look fresh and well-maintained.",
            "Leave any large existing trees exactly where they are - preserve mature planting.",
            "Renovate any existing garden sheds to look clean, painted, and well-kept.",
        ]

        # Apply garden style if specified
        if request.garden_style and request.garden_style in garden_style_prompts:
            prompt_parts.append(garden_style_prompts[request.garden_style])
        else:
            # Default fallback garden style if none specified
            prompt_parts.extend([
                "Transform this into a minimal, sophisticated English garden with restraint and elegance.",
                "Add a pristine manicured lawn with healthy lush green grass.",
                "PLANTING: Keep it minimal - ONLY white hydrangeas and subtle neatly-trimmed box hedging for structure.",
                "NO colourful flowers, NO busy borders, NO grandma's garden aesthetic.",
                "If space allows, add simple stepping stone garden path through the grass (natural stone or slate).",
                "The garden should feel calm, understated, and refined - not overdone.",
            ])

        # Add extra notes if provided
        if request.extra_notes:
            prompt_parts.append(f"Also: {request.extra_notes}")

        prompt_parts.append("Photorealistic result, professional landscape photography quality, natural daylight.")

        return " ".join(prompt_parts)

    # Special handling for hallways
    if request.room_type == 'hallway':
        prompt_parts = [
            "CRITICAL NON-NEGOTIABLE RULE #1 - EXACT ROOM DIMENSIONS: The hallway MUST remain the EXACT same size and shape. DO NOT enlarge, shrink, expand, or resize the hallway in any dimension. Keep all walls in their exact original positions. This hallway may be small or tight - that is COMPLETELY FINE and MUST be preserved exactly as is.",
            "CRITICAL NON-NEGOTIABLE RULE #2 - DOORS AND WINDOWS: If there are ANY doors or windows in this hallway, you MUST leave them EXACTLY where they are - same position, same size, same shape, same number. DO NOT move, resize, add, or remove ANY doors or windows. ABSOLUTELY NO NEW WINDOWS OR DOORS OF ANY KIND.",
            "CRITICAL NON-NEGOTIABLE RULE #3 - CAMERA PERSPECTIVE: Keep the EXACT same camera angle, viewpoint, and perspective as the input photo. DO NOT change the viewing angle or create a different perspective.",
            "CRITICAL NON-NEGOTIABLE RULE #4 - NO FURNITURE: DO NOT add ANY furniture to this hallway except POSSIBLY a slim console table ONLY if there is genuinely sufficient space. NO chairs, NO benches, NO storage units, NO shoe racks. Keep the hallway open and uncluttered. When in doubt, add NO furniture at all.",
            "CRITICAL NON-NEGOTIABLE RULE #5 - PRESERVE EXACT LAYOUT: Keep the exact layout, width, and flow of the hallway. If the hallway is narrow or tight, maintain that exact narrowness. DO NOT try to make it appear wider or more spacious.",
            "EDIT THE PROVIDED PHOTOGRAPH. Do not create a new image - modify the existing photo only.",
            "ONLY CHANGE: paint colours, flooring material, wall lighting, and minimal decor like wall art or mirror.",
            "DO NOT CHANGE: room size, room shape, wall positions, hallway width, ceiling height, windows, doors, camera perspective, or add furniture.",
        ]

        # Style
        if request.style and request.style in style_prompts:
            prompt_parts.append(f"Apply {style_prompts[request.style]}.")

        # Add time of day if specified
        if request.time_of_day and request.time_of_day in time_of_day_prompts:
            prompt_parts.append(f"Lighting: {time_of_day_prompts[request.time_of_day]}.")

        # Add colour scheme if specified
        if request.colour_scheme and request.colour_scheme in colour_scheme_prompts:
            prompt_parts.append(f"Colours: {colour_scheme_prompts[request.colour_scheme]}.")

        # Add flooring if specified
        if request.flooring and request.flooring in flooring_prompts:
            prompt_parts.append(f"Flooring: {flooring_prompts[request.flooring]}.")

        # Add wallpaper if specified
        if request.wallpaper and request.wallpaper in wallpaper_prompts:
            prompt_parts.append(f"Wall treatment: {wallpaper_prompts[request.wallpaper]}.")

        # Add extra notes if provided
        if request.extra_notes:
            prompt_parts.append(f"Also: {request.extra_notes}")

        # Final quality reminder
        prompt_parts.append("Photorealistic result, professional interior photography quality. Remember: NO furniture except possibly a slim console table if space genuinely allows.")

        return " ".join(prompt_parts)

    # Standard interior renovation prompt
    prompt_parts = [
        "CRITICAL NON-NEGOTIABLE RULE #1 - EXACT ROOM DIMENSIONS: The room MUST remain the EXACT same size and shape. DO NOT enlarge, shrink, expand, or resize the room in any dimension. The room's width, length, height, and overall volume must be IDENTICAL to the original photo. Keep all walls in their exact original positions.",
        "CRITICAL NON-NEGOTIABLE RULE #2 - DOORS AND WINDOWS: If there are ANY doors or windows in this room, you MUST leave them EXACTLY where they are - same position, same size, same shape, same number. DO NOT move, resize, add, or remove ANY doors or windows. This is ABSOLUTELY MANDATORY. NO NEW WINDOWS OR DOORS OF ANY KIND.",
        "CRITICAL NON-NEGOTIABLE RULE #3 - CAMERA PERSPECTIVE: Keep the EXACT same camera angle, viewpoint, and perspective as the input photo. DO NOT change the viewing angle or create a different perspective.",
        "EDIT THE PROVIDED PHOTOGRAPH. Do not create a new image - modify the existing photo only.",
        "ONLY CHANGE: paint colours, flooring material, furniture, fixtures, and decor.",
        "DO NOT CHANGE: room size, room shape, wall positions, ceiling height, windows, doors, or camera perspective.",
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

    # Add wallpaper if specified
    if request.wallpaper and request.wallpaper in wallpaper_prompts:
        prompt_parts.append(f"Wall treatment: {wallpaper_prompts[request.wallpaper]}.")

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

    # Validate URL is not empty
    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="Image URL cannot be empty")

    # Check if this is a data URI (uploaded image)
    if url.startswith('data:image'):
        print(f"[DEBUG] Processing uploaded image (data URI)")
        try:
            # Extract the base64 data from the data URI
            # Format: data:image/jpeg;base64,<base64-data>
            header, base64_data = url.split(',', 1)

            # Decode the base64 data to validate it's a real image
            image_bytes = base64.b64decode(base64_data)

            # Open with PIL to validate and potentially resize
            img = Image.open(BytesIO(image_bytes))

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

        except Exception as e:
            print(f"[ERROR] Failed to process uploaded image: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid image data: {str(e)}")

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
                raise HTTPException(status_code=400, detail=f"Failed to fetch source image: HTTP {response.status_code}")

            # Validate we received image content
            if not response.content or len(response.content) == 0:
                raise HTTPException(status_code=400, detail="Received empty image data from URL")

            # Try to open as image
            try:
                img = Image.open(BytesIO(response.content))
            except Exception as e:
                print(f"[ERROR] Invalid image data: {e}")
                raise HTTPException(status_code=400, detail="URL did not return a valid image file")

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

        except HTTPException:
            raise
        except httpx.RequestError as e:
            print(f"[ERROR] Network error fetching image: {e}")
            raise HTTPException(status_code=400, detail=f"Network error: Unable to fetch image from URL")
        except Exception as e:
            print(f"[ERROR] Unexpected error in fetch_image_as_base64: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Failed to process image: {str(e)}")


async def generate_with_replicate(source_image_b64: str, prompt: str) -> str:
    """
    Generate image using Replicate's google/nano-banana model.
    """
    if not REPLICATE_API_TOKEN:
        raise HTTPException(status_code=500, detail="REPLICATE_API_TOKEN not configured. Please set it in your environment variables.")

    # Validate inputs
    if not source_image_b64 or not source_image_b64.strip():
        raise HTTPException(status_code=400, detail="Source image data is empty")

    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    headers = {
        "Authorization": f"Token {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json"
    }

    # Convert our base64 source image to a temporary data URL
    image_data_url = f"data:image/jpeg;base64,{source_image_b64}"

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
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

            # Validate prediction response structure
            if "urls" not in prediction or "get" not in prediction["urls"]:
                print(f"[ERROR] Unexpected Replicate response structure: {prediction}")
                raise HTTPException(status_code=500, detail="Replicate API returned unexpected response format")

            prediction_url = prediction["urls"]["get"]

            # Poll until finished
            for _ in range(60):  # ~5 minutes max
                await asyncio.sleep(5)

                status_response = await client.get(prediction_url, headers=headers)
                status = status_response.json()

                if status["status"] == "succeeded":
                    output_url = status.get("output")

                    if not output_url:
                        print(f"[ERROR] Replicate succeeded but no output URL: {status}")
                        raise HTTPException(status_code=500, detail="Replicate generation succeeded but returned no image")

                    # Fetch the generated image
                    img_response = await client.get(output_url)

                    if img_response.status_code != 200 or not img_response.content:
                        raise HTTPException(status_code=500, detail="Failed to download generated image from Replicate")

                    return base64.b64encode(img_response.content).decode("utf-8")

                elif status["status"] == "failed":
                    error_msg = status.get('error', 'Unknown error')
                    print(f"[ERROR] Replicate generation failed: {error_msg}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Nano-Banana generation failed: {error_msg}"
                    )

            print("[ERROR] Replicate generation timed out after 5 minutes")
            raise HTTPException(
                status_code=500,
                detail="Nano-Banana generation timed out after 5 minutes"
            )

        except HTTPException:
            raise
        except httpx.RequestError as e:
            print(f"[ERROR] Network error with Replicate API: {e}")
            raise HTTPException(status_code=500, detail="Network error communicating with Replicate API")
        except Exception as e:
            print(f"[ERROR] Unexpected error in generate_with_replicate: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Unexpected error during image generation: {str(e)}")


async def generate_with_gemini(source_image_b64: str, prompt: str) -> str:
    """Generate image using Gemini API."""

    # Validate inputs
    if not source_image_b64 or not source_image_b64.strip():
        raise HTTPException(status_code=400, detail="Source image data is empty")

    if not prompt or not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    if USE_COMET_API:
        if not COMET_API_KEY:
            raise HTTPException(status_code=500, detail="COMET_API_KEY not configured")
        api_url = COMET_API_URL
        headers = {
            "x-goog-api-key": COMET_API_KEY,
            "Content-Type": "application/json"
        }
    else:
        if not GEMINI_API_KEY:
            raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured. Please set it in your environment variables.")
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
        try:
            response = await client.post(api_url, json=payload, headers=headers)

            print(f"[DEBUG] Gemini response status: {response.status_code}")

            if response.status_code != 200:
                error_detail = response.text
                print(f"[ERROR] Gemini API error: {error_detail}")

                # Provide helpful error messages for common status codes
                if response.status_code == 401:
                    raise HTTPException(status_code=500, detail="Gemini API: Invalid API key. Please check GEMINI_API_KEY.")
                elif response.status_code == 403:
                    raise HTTPException(status_code=500, detail="Gemini API: Access forbidden. Image generation may not be available in your region.")
                elif response.status_code == 429:
                    raise HTTPException(status_code=500, detail="Gemini API: Rate limit exceeded. Please try again later.")
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Gemini API error (HTTP {response.status_code}): {error_detail[:200]}"
                    )

            result = response.json()

            candidates = result.get('candidates', [])
            if not candidates:
                print(f"[ERROR] Gemini returned no candidates: {result}")
                raise HTTPException(status_code=500, detail="Gemini API returned no image candidates. The model may have filtered the request.")

            parts = candidates[0].get('content', {}).get('parts', [])

            for part in parts:
                if 'inlineData' in part:
                    image_data = part['inlineData'].get('data', '')
                    if not image_data:
                        raise HTTPException(status_code=500, detail="Gemini returned empty image data")
                    return image_data

            print(f"[ERROR] Gemini response has no image data: {result}")
            raise HTTPException(status_code=500, detail="Gemini API response contained no image data")

        except HTTPException:
            raise
        except httpx.RequestError as e:
            print(f"[ERROR] Network error with Gemini API: {e}")
            raise HTTPException(status_code=500, detail="Network error communicating with Gemini API")
        except Exception as e:
            print(f"[ERROR] Unexpected error in generate_with_gemini: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Unexpected error during image generation: {str(e)}")


async def generate_renovation_image(request: RenovationRequest) -> str:
    """
    Generate renovated room image using configured provider.
    Returns base64-encoded image.
    """
    # Fetch and encode the source image
    source_image_b64 = await fetch_image_as_base64(request.image_url)

    # Build the prompt
    prompt = build_renovation_prompt(request)

    print(f"\n{'='*80}")
    print(f"[PROMPT] Using image provider: {IMAGE_PROVIDER}")
    print(f"[PROMPT] Full prompt being sent to AI model:")
    print(f"{'-'*80}")
    print(prompt)
    print(f"{'='*80}\n")

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
async def get_property_images(
    request: RightmoveRequest
):
    """
    Extract property images from a Rightmove listing URL.
    Returns list of images with detected room types.
    NO AUTHENTICATION REQUIRED - users can fetch property data before signing in.
    """
    print(f"[INFO] Property fetch (unauthenticated)")
    return await get_property_from_rightmove(str(request.url))


@app.get("/proxy-image")
async def proxy_image(
    url: str
):
    """
    Proxy images from Rightmove - returns base64 encoded image data.
    NO AUTHENTICATION REQUIRED - users can fetch property images before signing in.
    """
    print(f"[INFO] Image proxy (unauthenticated)")

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
async def generate_renovation(
    request: RenovationRequest,
    user: dict = Depends(verify_clerk_session)
):
    """
    Generate a renovated version of a room image.
    Returns the original URL and generated image as base64.
    Single image only - batch processing removed.
    REQUIRES AUTHENTICATION.

    Bulletproof error handling with helpful JSON error messages.
    """
    # Optional: Log user activity
    print(f"[INFO] Renovation generation by user {user['user_id']}")

    try:
        # 1. Validate input parameters
        if not request.image_url or not request.image_url.strip():
            raise HTTPException(
                status_code=400,
                detail="image_url is required and cannot be empty"
            )

        # 2. Check environment variables based on provider
        if IMAGE_PROVIDER == "replicate":
            if not REPLICATE_API_TOKEN:
                raise HTTPException(
                    status_code=500,
                    detail="Server configuration error: REPLICATE_API_TOKEN not set. Please contact support or configure environment variables."
                )
        elif IMAGE_PROVIDER == "gemini":
            if not GEMINI_API_KEY and not COMET_API_KEY:
                raise HTTPException(
                    status_code=500,
                    detail="Server configuration error: GEMINI_API_KEY not set. Please contact support or configure environment variables."
                )

        print(f"[INFO] Starting renovation for image: {request.image_url[:100]}...")
        print(f"[INFO] Configuration - style: {request.style}, room: {request.room_type}, time: {request.time_of_day}")

        # 3. Generate the image (with nested error handling in called functions)
        generated_image = await generate_renovation_image(request)

        # 4. Validate the generated image is not empty
        if not generated_image or not generated_image.strip():
            print("[ERROR] Generated image data is empty")
            raise HTTPException(
                status_code=500,
                detail="Image generation completed but returned empty data. Please try again."
            )

        # 5. Build configuration summary
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

        print(f"[SUCCESS] Image renovation completed successfully")

        return RenovationResponse(
            original_url=request.image_url,
            generated_image_base64=generated_image,
            room_type=request.room_type,
            style=request.style,
            configuration_applied=config
        )

    except HTTPException:
        # Re-raise HTTP exceptions (already have proper error messages)
        raise

    except Exception as e:
        # Catch any unexpected errors and return helpful message
        print(f"[ERROR] Unexpected error in /renovate endpoint: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"Unexpected server error: {str(e)}. Please try again or contact support if the issue persists."
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
