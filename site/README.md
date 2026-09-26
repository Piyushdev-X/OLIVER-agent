# OLIVER website

A static, Vercel-ready website for OLIVER's architecture documentation and evaluation results: four pages, one stylesheet, one small script, no framework and no build step on the server.

```
site/
├── build.py          procedural generator (no classes); renders public/ from data/
├── data/runs.json    real harness data captured from the offline eval
└── public/           the deployable site (committed, served as-is)
    ├── index.html  architecture.html  evaluation.html  memory.html  404.html
    ├── styles.css  fixed 960px layout, hardcoded values, no media queries
    └── app.js      keyboard layer: / or ctrl k palette, ? shortcuts, g-chords, j/k, [ ]
vercel.json           (repo root) serves site/public with strict security headers
```

## Deploy on Vercel

1. Push this repository to GitHub.
2. In Vercel choose **Add New → Project**, import the repository and keep every default. `vercel.json` sets the framework to *Other*, disables the build step and serves `site/public`.
3. Click **Deploy**. Every push to the connected branch redeploys; pull requests get preview URLs.

Command-line alternative, from the repository root:

```bash
npm i -g vercel
vercel          # preview deployment
vercel --prod   # production deployment
```

## Update the content

```bash
python3 site/build.py --collect   # re-run the offline eval and refresh data/runs.json
python3 site/build.py             # re-render public/ from the stored data
python3 -m http.server -d site/public 8000   # local preview at http://localhost:8000
```

Commit the regenerated `site/public/` files; Vercel serves exactly what is committed.

## Logos

Every page header, the footer and each diagram's title block reserve space for the organization's logos (a wide logo and a square mark). Replace the placeholder `<div class="logo-slot">` elements with `<img>` tags pointing at files in `site/public/`, or edit `logo_rail()` and `svg_title_block()` in `build.py` and rebuild.

## Rules this site follows

No CSS custom properties or var function, no media-query rules, a rigid 960px layout, no timeline visuals, and purely procedural Python. `python3 scripts/check_constraints.py site` verifies all of it.
