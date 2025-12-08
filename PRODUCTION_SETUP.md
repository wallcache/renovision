# Production-Ready Setup Guide

This guide transforms your Renovision app from a development setup (Tailwind CDN, in-browser Babel) to a production-ready React application.

---

## Quick Fix (Current File)

If you need to fix the current `index.html` immediately, apply these changes:

### 1. Remove all `greenery` references
**Lines to fix:** 549, 833, 847, 1123

Remove or comment out these lines that reference undefined `greenery` variable.

### 2. Add error handling for image fetching
**Line 727-741**: Wrap in try/catch and show user feedback for failed images.

---

## Full Production Setup

### Project Structure

```
renovision/
├── public/
│   ├── index.html          # Minimal HTML shell
│   └── logo.png            # Your logo
├── src/
│   ├── index.js            # Entry point
│   ├── App.jsx             # Main component
│   ├── components/
│   │   ├── TogglePill.jsx
│   │   ├── ColourSwatch.jsx
│   │   ├── ToggleGroup.jsx
│   │   ├── ComparisonSlider.jsx
│   │   └── LoadingDots.jsx
│   ├── config/
│   │   └── constants.js    # API URL, options
│   ├── styles/
│   │   └── index.css       # Tailwind imports + custom styles
│   └── utils/
│       └── api.js          # API fetch functions
├── package.json
├── tailwind.config.js
├── postcss.config.js
└── vite.config.js (or webpack/next config)
```

---

## Step-by-Step Migration

### Step 1: Initialize Project

```bash
# Create React app with Vite (recommended - faster than CRA)
npm create vite@latest renovision-app -- --template react

cd renovision-app

# Install dependencies
npm install

# Install Tailwind CSS
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

### Step 2: Configure Tailwind

**tailwind.config.js:**
```js
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Cormorant Garamond', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        linen: {
          50: '#fdfcfb',
          100: '#faf8f4',
          200: '#f2efe9',
          300: '#e8e4dc',
          400: '#d4cfc4',
          500: '#b8b1a4',
          600: '#a89f93',
          700: '#8a8278',
          800: '#6b655c',
          900: '#2c2c2c',
        },
        taupe: {
          light: '#c4bbb0',
          DEFAULT: '#a89f93',
          dark: '#8a8278',
        },
        sage: {
          light: '#d4ddd4',
          DEFAULT: '#9aab9a',
          dark: '#7a8f7a',
        },
        clay: {
          light: '#e8d8cc',
          DEFAULT: '#c9a88a',
          dark: '#a68b6a',
        }
      }
    }
  },
  plugins: [],
}
```

**src/styles/index.css:**
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Import Google Fonts */
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=Inter:wght@300;400;500&display=swap');

* {
  box-sizing: border-box;
}

body {
  font-family: 'Inter', sans-serif;
  background: #faf8f4;
  color: #2c2c2c;
  min-height: 100vh;
}

/* ... (copy all custom CSS from lines 52-346 of index.html) ... */
```

### Step 3: Create API Utility

**src/utils/api.js:**
```js
const API_BASE_URL = import.meta.env.VITE_API_URL || 'https://renovision-5z2b.onrender.com';

export async function fetchPropertyData(url) {
  const response = await fetch(`${API_BASE_URL}/property`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  });

  if (!response.ok) {
    const errorText = await response.text();
    try {
      const errorData = JSON.parse(errorText);
      throw new Error(errorData.detail || 'Failed to fetch property data');
    } catch {
      throw new Error('Failed to fetch property data');
    }
  }

  return response.json();
}

export async function proxyImage(url) {
  try {
    const proxyResponse = await fetch(`${API_BASE_URL}/proxy-image?url=${encodeURIComponent(url)}`);

    if (!proxyResponse.ok) {
      console.warn(`Failed to proxy image: ${url} (status ${proxyResponse.status})`);
      return null; // Return null instead of crashing
    }

    const proxyData = await proxyResponse.json();
    return proxyData.data_url;
  } catch (error) {
    console.error(`Error proxying image: ${error.message}`);
    return null; // Return null on network error
  }
}

export async function generateRenovation(imageData) {
  const response = await fetch(`${API_BASE_URL}/renovate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(imageData)
  });

  if (!response.ok) {
    const errorText = await response.text();
    try {
      const errorData = JSON.parse(errorText);
      throw new Error(errorData.detail || 'Generation failed');
    } catch {
      throw new Error('Generation failed');
    }
  }

  return response.json();
}
```

### Step 4: Extract Configuration

**src/config/constants.js:**
```js
export const DESIGN_STYLE_OPTIONS = [
  { id: 'midcentury', label: 'Mid-Century' },
  { id: 'minimal', label: 'Minimal' },
  { id: 'industrial', label: 'Industrial' },
  { id: 'scandinavian', label: 'Scandinavian' },
  { id: 'wabisabi', label: 'Wabi-Sabi' },
  { id: 'mediterranean', label: 'Mediterranean' },
];

export const ROOM_TYPE_OPTIONS = [
  { id: 'living', label: 'Living' },
  { id: 'bedroom', label: 'Bedroom' },
  { id: 'kitchen', label: 'Kitchen' },
  { id: 'dining', label: 'Dining' },
  { id: 'bathroom', label: 'Bathroom' },
  { id: 'office', label: 'Office' },
  { id: 'hallway', label: 'Hallway' },
  { id: 'garden', label: 'Garden' },
  { id: 'outdoor', label: 'Outdoor' },
];

export const TIME_OF_DAY_OPTIONS = [
  { id: 'day', label: 'Day' },
  { id: 'night', label: 'Night' },
  { id: 'golden_hour', label: 'Golden Hour' },
];

export const COLOUR_SCHEME_OPTIONS = [
  { id: 'white', label: 'White', color: '#FFFFFF' },
  { id: 'black', label: 'Black', color: '#000000' },
  { id: 'charcoal', label: 'Charcoal Grey', color: '#36454F' },
  { id: 'navy', label: 'Navy Blue', color: '#000080' },
  { id: 'teal', label: 'Teal', color: '#008080' },
  { id: 'forest_green', label: 'Forest Green', color: '#228B22' },
  { id: 'olive', label: 'Olive', color: '#808000' },
  { id: 'mustard', label: 'Mustard', color: '#FFDB58' },
  { id: 'terracotta', label: 'Terracotta', color: '#E2725B' },
  { id: 'burgundy', label: 'Burgundy', color: '#800020' },
];

export const FLOORING_OPTIONS = [
  { id: 'wood_parquet', label: 'Wood Parquet' },
  { id: 'tiled', label: 'Tiled' },
  { id: 'stone_slabs', label: 'Stone Slabs' },
  { id: 'polished_concrete', label: 'Polished Concrete' },
];
```

### Step 5: Build Process

**package.json scripts:**
```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  }
}
```

**Build for production:**
```bash
npm run build
```

This creates optimized files in `dist/` ready for deployment.

---

## Benefits of Production Setup

### 1. **Performance**
- **CDN Tailwind**: ~300KB uncompressed, loads every visit
- **Production Build**: ~50KB compressed CSS with only used classes
- **Improvement**: 6x smaller, cached

### 2. **Babel Transform**
- **In-browser**: Compiles JSX on every page load (slow)
- **Production Build**: Pre-compiled, instant load
- **Improvement**: 10x faster initial load

### 3. **React**
- **CDN Development**: 1.5MB uncompressed
- **Production Build**: 130KB compressed
- **Improvement**: 11x smaller

### 4. **Code Splitting**
- Production builds can lazy-load components
- Faster initial page load
- Better caching

### 5. **Environment Variables**
- Secure API keys (`.env` files)
- Different configs for dev/staging/production

### 6. **Type Safety (Optional)**
- Add TypeScript for better DX
- Catch errors at compile time

---

## Deployment

### Option 1: Render.com (with your existing backend)

1. Create new **Static Site** on Render
2. Connect your GitHub repo
3. Build command: `npm run build`
4. Publish directory: `dist`

### Option 2: Vercel (Recommended)

```bash
npm install -g vercel
vercel deploy
```

### Option 3: Netlify

```bash
npm install -g netlify-cli
netlify deploy --prod
```

---

## Environment Variables

**Create `.env` file:**
```
VITE_API_URL=https://renovision-5z2b.onrender.com
```

**Access in code:**
```js
const apiUrl = import.meta.env.VITE_API_URL;
```

---

## Migration Timeline

- **Immediate fix** (15 min): Apply quick fixes to current index.html
- **Basic setup** (2 hours): Create Vite app, migrate components
- **Full production** (1 day): Optimize, test, deploy

---

## Questions?

- **CDN still showing warning**: You've migrated successfully when you no longer load `cdn.tailwindcss.com`
- **Babel warning gone**: When you precompile with Vite/Webpack
- **Greenery error fixed**: Remove all references to undefined variables
- **502 errors handled**: Implemented in api.js with try/catch and null returns
