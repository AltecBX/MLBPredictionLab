# Brand assets

Drop the logo here as **`logo-source.png`** — square, ideally 1024×1024, with a
transparent background — then run:

```
cd frontend
node scripts/make-icons.mjs
cp brand/logo-source.png public/logo.png
```

That regenerates every icon the product uses and puts the header mark in place:

| Output | Where it appears |
|---|---|
| `app/icon.png` | Browser tab |
| `public/logo.png` | Header and footer on every page |
| `public/apple-icon.png` | iOS home screen, flattened onto the app background |
| `public/icon-192.png`, `icon-512.png` | Android home screen, app switcher |
| `public/icon-maskable-512.png` | Android adaptive icons, padded into the safe zone |

The outputs are committed. This is a development script, never a build step — a
deploy must not depend on regenerating artwork.

**The source file is not in the repository.** It is artwork rather than code, and
nothing here will invent an approximation of it: a logo that is nearly right is
worse than the placeholder mark, because it looks deliberate. Until the file is
added, `BrandMark` falls back to the drawn baseball and the build succeeds.
