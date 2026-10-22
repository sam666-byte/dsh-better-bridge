# Re-applying the dsh mobile UI patch after an update

The patch lives inside the dsh web frontend's `dist/` folder, which is
**wiped every time dsh updates via npx**. Re-applying takes about a minute.

## What the patch does

- `mobile-patch.css` — phone-width layout fixes: labelled rail buttons,
  sidebar as a floating drawer, full-width memory overlay, and a bigger
  tap target for the file/review pane's tab close (×).
- `mobile-patch.js` — injects a floating **“‹ Kembali”** (back) pill.
  The file/review pane is full-screen on phones but has no back button
  (the app navigates by React state, so `history.back()` does nothing);
  the pill appears only while that pane is open and presses the app's
  own native Close / Collapse controls.

## Procedure

1. Locate the frontend dist (the npx cache hash in the path changes
   between dsh versions):

   ```sh
   find ~/.npm/_npx -name "mobile-patch.css" -path "*dsh-web-frontend*" 2>/dev/null
   find ~/.npm/_npx -type d -name "dsh-web-frontend" 2>/dev/null
   ```

   The dist is `<...>/node_modules/@deepseek-ai/dsh-web-frontend/dist/`
   with `index.html` and an `assets/` folder inside.

2. Copy the two patch files into `assets/`:

   ```sh
   cp mobile-patch.css mobile-patch.js <dist>/assets/
   ```

3. Back up `index.html`, then make sure it contains these two lines
   (the `<link>` goes after the other stylesheet links; the `<script>`
   right after it — both just before `</head>` works too):

   ```sh
   cp <dist>/index.html <dist>/index.html.bak-$(date +%Y%m%d)
   ```

   ```html
   <link rel="stylesheet" href="./assets/mobile-patch.css">
   <script src="./assets/mobile-patch.js" defer></script>
   ```

   If a previous patch was applied, the `<link>` line may already be
   there — don't duplicate it, just add the `<script>` line.

4. No server restart is needed; the dist files are served per request.

5. On the phone, **fully close and reopen the app** — the WebView caches
   the old `index.html`/assets, so the fix may not show up until the
   app process is restarted.

## Verify

Open any session, tap a file in a tool result so the review pane opens
full-screen: the “‹ Kembali” pill should sit at the bottom-left, and
tapping it should return you to the chat. On the chat itself (and on
desktop widths) the pill must stay hidden.
