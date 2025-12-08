# Production Migration Checklist

Use this checklist when you're ready to migrate from the current CDN setup to a production build.

---

## Phase 1: Setup (30 minutes)

### Step 1.1: Create Project
- [ ] Run `npm create vite@latest renovision-prod -- --template react`
- [ ] `cd renovision-prod`
- [ ] Run `npm install`
- [ ] Verify dev server works: `npm run dev`

### Step 1.2: Install Tailwind
- [ ] Run `npm install -D tailwindcss postcss autoprefixer`
- [ ] Run `npx tailwindcss init -p`
- [ ] Verify `tailwind.config.js` and `postcss.config.js` created

### Step 1.3: Configure Tailwind
- [ ] Copy Tailwind config from `PRODUCTION_SETUP.md` to `tailwind.config.js`
- [ ] Update `tailwind.config.js` content paths
- [ ] Create `src/styles/index.css`
- [ ] Add Tailwind directives to `index.css`
- [ ] Copy custom CSS from old `index.html` (lines 52-346)

---

## Phase 2: Copy Code (1 hour)

### Step 2.1: Copy Configuration
- [ ] Create `src/config/constants.js`
- [ ] Copy all `*_OPTIONS` arrays from `index.html`
- [ ] Copy `API_BASE_URL` and `LOGO_URL`

### Step 2.2: Copy Utilities
- [ ] Create `src/utils/api.js`
- [ ] Copy from `src_example/utils/api.js`
- [ ] Update `API_BASE_URL` to use `import.meta.env.VITE_API_URL`

### Step 2.3: Copy Components
- [ ] Create `src/components/` folder
- [ ] Copy these files from `src_example/components/`:
  - [ ] `TogglePill.jsx`
  - [ ] `ColourSwatch.jsx`
  - [ ] `ToggleGroup.jsx`
  - [ ] `LoadingDots.jsx`
  - [ ] `ComparisonSlider.jsx` (from old index.html)

### Step 2.4: Copy Main App
- [ ] Replace `src/App.jsx` with code from `src_example/App.jsx`
- [ ] Update imports to match your file structure
- [ ] Copy remaining step logic (Steps 3 & 4)

### Step 2.5: Copy Public Assets
- [ ] Copy `logo.png` to `public/` folder
- [ ] Update logo path in App.jsx if needed

---

## Phase 3: Configure & Test (30 minutes)

### Step 3.1: Environment Variables
- [ ] Create `.env` file in project root
- [ ] Add: `VITE_API_URL=https://renovision-5z2b.onrender.com`
- [ ] Create `.env.example` for reference
- [ ] Add `.env` to `.gitignore`

### Step 3.2: Update package.json
- [ ] Verify scripts exist:
  ```json
  {
    "scripts": {
      "dev": "vite",
      "build": "vite build",
      "preview": "vite preview"
    }
  }
  ```

### Step 3.3: Test Development
- [ ] Run `npm run dev`
- [ ] Open browser to dev URL
- [ ] Test Step 1: Fetch property
- [ ] Test Step 2: Image selection
- [ ] Test Step 3: Configure renovation
- [ ] Test Step 4: Generate result
- [ ] Check console for errors
- [ ] Verify no Tailwind/Babel warnings ✅

---

## Phase 4: Build & Deploy (30 minutes)

### Step 4.1: Production Build
- [ ] Run `npm run build`
- [ ] Check `dist/` folder created
- [ ] Verify file sizes are small (~100-200KB total)
- [ ] Run `npm run preview` to test production build locally

### Step 4.2: Deploy to Vercel (Recommended)
- [ ] Install Vercel CLI: `npm install -g vercel`
- [ ] Run `vercel login`
- [ ] Run `vercel` from project root
- [ ] Follow prompts
- [ ] Get production URL
- [ ] Test production deployment

### Step 4.3: Deploy to Render (Alternative)
- [ ] Create new "Static Site" on Render dashboard
- [ ] Connect GitHub repo
- [ ] Set build command: `npm run build`
- [ ] Set publish directory: `dist`
- [ ] Add environment variable: `VITE_API_URL`
- [ ] Deploy

### Step 4.4: Deploy to Netlify (Alternative)
- [ ] Install Netlify CLI: `npm install -g netlify-cli`
- [ ] Run `netlify login`
- [ ] Run `netlify deploy --prod`
- [ ] Follow prompts
- [ ] Get production URL

---

## Phase 5: Verify Production (15 minutes)

### Step 5.1: Performance Checks
- [ ] Open production URL
- [ ] Open DevTools Network tab
- [ ] Verify CSS file < 100KB
- [ ] Verify JS files < 200KB total
- [ ] Check no CDN requests to `cdn.tailwindcss.com` ✅
- [ ] Check no Babel warning ✅
- [ ] Verify fast load time (< 1 second)

### Step 5.2: Functionality Tests
- [ ] Test with real Rightmove URL
- [ ] Verify images load
- [ ] Test renovation generation
- [ ] Test error handling (bad URL)
- [ ] Test on mobile device
- [ ] Test on different browsers

---

## Troubleshooting

### Issue: "Module not found"
**Solution:** Check import paths match your file structure

### Issue: Tailwind classes not working
**Solution:**
- Verify `index.css` imported in `main.jsx`
- Check `tailwind.config.js` content paths
- Restart dev server

### Issue: API calls failing
**Solution:**
- Check `.env` file exists and has correct URL
- Verify environment variable name starts with `VITE_`
- Restart dev server after changing `.env`

### Issue: Images not displaying
**Solution:**
- Check logo path in `public/` folder
- Verify `API_BASE_URL` is correct
- Check browser console for network errors

---

## Rollback Plan

If something goes wrong during migration:

1. **Keep old `index.html` working** - Don't delete it
2. **Test new setup alongside old** - Different URL/port
3. **Switch gradually** - Test with limited users first
4. **Have backup** - Commit to Git before major changes

---

## Time Estimate

| Phase | Estimated Time | Can Skip? |
|-------|---------------|-----------|
| Phase 1: Setup | 30 min | No |
| Phase 2: Copy Code | 1 hour | No |
| Phase 3: Configure | 30 min | No |
| Phase 4: Deploy | 30 min | Optional* |
| Phase 5: Verify | 15 min | No |
| **Total** | **~3 hours** | |

*You can skip deployment and just run locally

---

## Benefits After Migration

✅ **10x faster** page loads
✅ **No CDN warnings** in console
✅ **No Babel warnings** in console
✅ **Better caching** - faster repeat visits
✅ **Smaller bundles** - less bandwidth
✅ **SEO friendly** - better meta tags
✅ **TypeScript ready** - can add types later
✅ **Environment variables** - secure config
✅ **Hot reload** - faster development
✅ **Build optimizations** - tree shaking, minification

---

## When to Migrate?

### Migrate Now If:
- You're deploying to real users
- Performance matters to you
- You want professional setup
- You have 3 hours free

### Wait to Migrate If:
- Current setup works for you
- You're still prototyping
- You don't have time now
- Performance is acceptable

---

## Post-Migration Cleanup

After successful migration:

- [ ] Archive old `index.html` (rename to `index.old.html`)
- [ ] Update README with new setup instructions
- [ ] Document new deploy process for team
- [ ] Update any documentation referencing old setup
- [ ] Celebrate! 🎉

---

**Good luck with your migration! 🚀**

Refer back to **PRODUCTION_SETUP.md** for detailed code examples and configurations.
