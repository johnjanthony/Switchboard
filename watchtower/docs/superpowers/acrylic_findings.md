
# Watchtower Acrylic Blur Findings

## The Goal
To get a native Windows 11 Acrylic blur ("frosted glass") on the borderless `BackgroundForm` that sits behind `DetailPanel`, matching the look of the "command palette".

## What we learned (The Full Resolution Story)

### 1. DWM Silently Declines SBT (System Backdrop Types)
The "canonical" Windows 11 approach of extending the frame (`DwmExtendFrameIntoClientArea`) and requesting `DWMWA_SYSTEMBACKDROP_TYPE = 3` (Acrylic) **fails silently on this machine**. Our Backdrop Lab proved that even on a perfectly standard Sizable window with standard activation, the HRESULT returns `S_OK (0)` and reads back as `3`, but DWM simply refuses to render the material. The window remains a solid opaque slab. This was the root cause of our first and third failures.

### 2. The Test Protocol Trap
Our second attempt (`ACCENT_ENABLE_ACRYLICBLURBEHIND` via `SetWindowCompositionAttribute` (WCA)) might have actually worked in theory, but we tested it over a pure black desktop background. Over black, a dark frosted glass material is indistinguishable from a solid dark grey slab! Always test transparency over bright, busy backgrounds.

### 3. The Alpha-Channel / GDI+ Bug
We initially used `BackColor = Color.Black`. WinForms uses GDI+ to paint the background, which writes pixels with an alpha channel of `255` (fully opaque). This completely covers any DWM backdrop material (which only shines through pixels where alpha is `0`).
**Fix:** We removed `BackColor` and added an empty `OnPaintBackground` override.

### 4. The Working Recipe: Sizable + NCCALCSIZE-zero + ROUND + WCA (State 4)
The single mechanism that correctly renders frost on this machine while respecting rounded corners is:
- `FormBorderStyle = FormBorderStyle.Sizable` (provides the base window frame)
- Intercept `WM_NCCALCSIZE` (`0x0083`) and return 0 (hides the standard frame)
- Set `DWMWCP_ROUND` (attr `33`) to `2` (DWM successfully rounds the window)
- `SetWindowCompositionAttribute` with `ACCENT_ENABLE_ACRYLICBLURBEHIND` (state `4`) and `GradientColor`

### 5. The White-Crush Limitation
WCA Acrylic crushes high-luminance content by design. White backgrounds are heavily muted. Attempting to use a near-zero alpha (e.g. `0x01000000`) to increase translucency is a trap—below a certain threshold, the accent stops compositing entirely and renders solid black. Because we could not achieve a true "frosted glass over white" look (like the Command Palette) with native WCA, we ultimately abandoned DWM for a custom approach.

### 6. The DIY Frost Resolution
We implemented our own "DIY Frost" to achieve the exact visual target:
- **The Recipe**: We take a screenshot (`Graphics.CopyFromScreen`) of the area directly beneath where the popup will appear. We run a highly optimized, 2-pass separable box blur (radius 4) directly on an `int[]` pixel buffer. Finally, we tint it with a Src-Over alpha blend (`0x2A2A2A` at alpha `64`).
- **The Delivery**: We threw out `BackgroundForm`'s DWM interop and converted it into a classic Layered Window (`WS_EX_LAYERED`). The composited frost image is masked against a cleanly anti-aliased `RoundedRectPath` and pushed directly to the screen via `UpdateLayeredWindow`.
- **The Tradeoff (Frozen Capture)**: Because we capture the screen at popup-time, moving windows underneath the widget while it is open will not update the blur dynamically. However, since the popup is transient, this static snapshot approach provides a perfectly deterministic, flawlessly tinted "frosted glass" look that works on every machine without relying on DWM black boxes.
