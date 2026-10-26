# Debloat & Apple TV 4K Launcher Setup for `hi352l` (`172.30.102.100`)

Built on [`Gathin23/cleanyourtv`](https://github.com/Gathin23/cleanyourtv) (`./cleanyourtv`) and tailored for your **HiSilicon `hi352l` (`Hi352` / `Hi3751V350` / `Hi3751V352`)** TV at **`172.30.102.100`**:
- **Target IP:** `172.30.102.100` (pre-configured as the default `TV_HOST` in `build-and-install.sh` and `restore.sh`).
- **Connection:** Wi-Fi, nothing plugged into USB ports.
- **Debloat preferences:** Keeps all streaming apps, wireless casting (`com.hisilicon.miracast`, `com.google.android.apps.mediashell`, `com.android.toofifi`), firmware updater, Live TV / HDMI input drivers (`com.hisilicon.tv.service`, `com.hisilicon.tv.menu`, `com.hisilicon.rmservice`), and Play Store/Services; disables Hi352 OEM ad/telemetry services (`com.cvte.gaia.ad.service`, `com.cvte.wonder.cloud.service`, `com.cvte.industries`, `com.zeasn.deviceportal.asdprovider.skyworth_hisi352`), ad screensaver/cleaner (`com.cvte.tv.pictorial`, `com.cvte.tv.steward`), Voice Assistant (`com.google.android.katniss`), and unneeded background providers.
- **Launcher:** Native Apple TV 4K style home screen (`com.gathin.tvhome`) — pitch-black background, 4-slot bottom dock in a rounded band (configurable to 4, 5, 6, or 7 slots), spring-animated glass focus highlight, ambient colour glow sampled from the focused app's banner, top-left greeting/clock/date in the open-licence **Outfit** typeface, and top-right frosted pills for **Inputs** (HDMI, AV, Live TV dropdown), **Wi-Fi** (showing network name), and **Settings**.

---

## Included Files

| File | Purpose |
|---|---|
| [`build-and-install.sh`](build-and-install.sh) | Defaults to `TV_HOST=172.30.102.100`: connects, runs pre-flight safety checks, backs up packages/props/settings to `backup/`, debloats in small batches with verification screenshots (`shots/`), detects CVTE vs. Zeasn vs. standard Android TV on `hi352l`, builds & installs the Apple TV 4K launcher, pushes `my-theme/config.json`, and verifies D-pad navigation. |
| [`restore.sh`](restore.sh) | Defaults to `TV_HOST=172.30.102.100`: one-line restore of all disabled packages, stock launcher Home takeover, and animation scales; also supports `--enable <pkg>`, `--enable-voice`, `--reset-dns`, and `--from-backup`. |
| [`debloat.txt`](debloat.txt) | `hi352l`-specific categorized debloat list (Batch 1: Hi352 OEM/CVTE/Zeasn/Google Telemetry & Ads, Batch 2: Ad screensaver, cleaner & preloads, Batch 3: Voice Assistant, Batch 4: Unneeded background providers). |
| [`my-theme/config.json`](my-theme/config.json) | Theme overlay for customizing dock size (`columns`), dock app order (`dock_apps`), source picker (`source_panel_service`), and wallpapers without rebuilding the APK. |

---

## 1. Connect & Run on `172.30.102.100` (`hi352l`)

> **Why the cloud sandbox timed out reaching `172.30.102.100`:** `172.30.102.100` is a private RFC1918 LAN IP on your local Wi-Fi network, which is unreachable from a remote cloud container. Run the commands below from a terminal on your computer connected to the same `172.30.x.x` Wi-Fi network.

### Step A: Enable ADB on `hi352l`
Depending on which OEM firmware your `hi352l` board runs, turn on ADB using the matching method below:

1. **Standard Android TV / Google TV menu:**
   - Open **Settings → Device Preferences → About**, press **OK** on **Build** 7 times, then go to **Developer options** and enable **USB debugging** (and **Network debugging** if shown).
2. **CVTE firmware on HiSilicon `hi352l` (no Developer options screen):**
   - Switch the TV to a live source first (**TV** or **HDMI** — the menu does not open from the home screen).
   - Press **MENU**, then quickly press **1 1 4 7** on the remote.
   - Select **DEBUG → ADB ENABLE → ON**, then press **Back** to exit. *(Do not touch any other item in that menu, and never launch `com.cvte.fac.menu` over ADB.)*
3. **Skyworth / BYINTEK firmware on HiSilicon `hi352l` (Developer options closes immediately):**
   - Open the main **Settings** screen (with the category icons).
   - Press **Menu, Menu, Up, Up, Down, Down, Left, Left, Right, Right** on the remote until *"Developer BackDoor key match! Toggle ON"* appears.
   - Open the new **Developer** item at the end of the list and switch **ADB** on.

### Step B: Run `./build-and-install.sh`
On your computer on the same Wi-Fi (with **JDK 17** and **Android SDK platform 34 / build-tools 34.0.0** installed for building the launcher):

```bash
./build-and-install.sh
```
*(It automatically uses `TV_HOST=172.30.102.100`, or you can pass `TV_HOST=<other-ip> ./build-and-install.sh`.)*

- **Accept the prompt on the TV:** When `tools/tv.py --probe` connects, if **"Allow USB debugging?"** appears on your TV screen, tick *"Always allow from this computer"* and select **Allow**. *(Note: Some `hi352l` CVTE builds have `ro.adb.secure=0` and connect immediately without a prompt.)*
- **Debloat only (skip launcher build):**
  ```bash
  ./build-and-install.sh --debloat-only
  ```
- **Launcher only (skip debloat):**
  ```bash
  ./build-and-install.sh --launcher-only
  ```

### Important `hi352l` Hardware & Inputs Notes
- **Protected HiSilicon packages:** `com.hisilicon.tv.service`, `com.hisilicon.tv.menu`, and `com.hisilicon.rmservice` are **never** disabled—without them, a HiSilicon `hi352l` TV has no settings menu, no HDMI/tuner picture, and no remote accessibility service.
- **Inputs Pill on `hi352l`:** Some `hi352l` builds do not register HDMI ports with Android's standard `TvInputManager` and use a vendor source picker activity instead. If pressing the **Inputs** pill on the new launcher does not list HDMI 1–3, add the matching line to [`my-theme/config.json`](my-theme/config.json) and push it with `./cleanyourtv/tools/theme.sh my-theme`:
  - **CVTE `hi352l`:** `"source_panel_service": "com.cvte.tv.globalui/.InputSourceActivity"`
  - **Skyworth `hi352l`:** `"source_panel_service": "com.sdt.app.skysettings/.entry.activity.QuickSourceActivity"`
- **CVTE `hi352l` Stock Launcher Guard:** If your `hi352l` has `com.cvte.*` packages, `build-and-install.sh` automatically sets `STOCK_LAUNCHER=none` so `com.cvte.tv.launcher.space` stays enabled (CVTE `hi352l` firmware has no `FallbackHome`).

---

## 2. How to Re-enable Any App (or Undo Everything)

Nothing is ever uninstalled—packages are only disabled for `user 0` (`pm disable-user --user 0`).

### Re-enable a Single App
List disabled packages on `172.30.102.100`:
```bash
TV_HOST=172.30.102.100 ./cleanyourtv/tools/tv.py 'pm list packages -d --user 0'
```
Re-enable any single package:
```bash
./restore.sh --enable <package.name>
# Or directly via tv.py:
TV_HOST=172.30.102.100 ./cleanyourtv/tools/tv.py 'pm enable --user 0 <package.name>'
```

### Re-enable Voice Assistant (Google Assistant / Alexa)
If you want the remote's microphone button and voice search back:
```bash
./restore.sh --enable-voice
```

### Full Restore (Re-enable All Disabled Packages & Restore Stock Launcher)
To put the TV back the way it was before running `disable.sh` (using the pre-cleanup state saved in `~/.cleanyourtv/`):
```bash
./restore.sh
```
*(If your `hi352l` uses Zeasn Whale OS (`com.zeasn.whale.saas`) or BestV (`com.bestv.ott`) as its stock launcher, pass it as the second argument: `./restore.sh debloat.txt com.zeasn.whale.saas`.)*

---

## 3. How to Change the Dock Size or Dock App Order

You have three ways to change the dock apps (`dock_apps`) or the number of columns/slots (`columns`: `4`, `5`, `6`, or `7`):

### Option A: Push `my-theme/config.json` (Instant — No APK Rebuild Needed)
1. Find the package names of the apps installed on your TV:
   ```bash
   TV_HOST=172.30.102.100 ./cleanyourtv/tools/tv.py 'pm list packages -e --user 0'
   ```
2. Edit [`my-theme/config.json`](my-theme/config.json):
   ```json
   {
     "columns": 4,
     "dock_apps": [
       "com.netflix.ninja",
       "com.amazon.amazonvideo.livingroom",
       "com.google.android.youtube.tv",
       "in.startv.hotstar"
     ]
   }
   ```
   - Set `"columns"` to `4`, `5`, `6`, or `7` (the dock always has `"columns"` slots, and the app grid below uses the same column count).
   - List your preferred apps in `"dock_apps"` in exact left-to-right order. Missing apps are skipped automatically.
3. Push the updated configuration to `172.30.102.100`:
   ```bash
   TV_HOST=172.30.102.100 ./cleanyourtv/tools/theme.sh my-theme
   ```
   Home restarts immediately with your new dock order and column count.

### Option B: Reorder Directly on the TV with Your Remote
- Move focus to the **Settings** pill at the top right of the home screen and **press and hold OK** on your remote, then choose **Reorder apps** (or change **Columns** in **Launcher settings**).
- Or set `"tile_menu": true` in `my-theme/config.json` and push it: holding **OK** on any app tile opens a compact menu with **Move**, allowing you to carry the tile with the D-pad arrows and press **Back** to drop it into place.

### Option C: Edit `config.xml` Before Building the APK
Edit [`cleanyourtv/launcher/app/src/main/res/values/config.xml`](cleanyourtv/launcher/app/src/main/res/values/config.xml):
- Update `<string-array name="dock_apps">` with your `<item>package.name</item>` entries.
- Update `<integer name="columns">4</integer>` (`4`, `5`, `6`, or `7`).
- Run `TV_HOST=172.30.102.100 ./cleanyourtv/launcher/build-and-install.sh`.

---

## 4. How to Change or Rotate the Wallpaper

By default, the launcher uses a pitch-black background (`"wallpaper": false`) with an ambient colour glow sampled from the focused dock app's banner (`"ambient_glow": true`) to keep memory footprint tiny on 1 GB RAM `hi352l` boards.

### Use a Built-in Theme Wallpaper
`cleanyourtv/themes/` ships ready-made themes (`aurora`, `glass`, `ruska`, `scenes`, `nature-calm`, `retro`, `sweden`). Push any of them without rebuilding:
```bash
TV_HOST=172.30.102.100 ./cleanyourtv/tools/theme.sh cleanyourtv/themes/aurora
```

### Use Your Own Single Wallpaper (`wallpaper.jpg`)
1. Place a dark `1920×1080` JPEG (ideally under ~300 KB) at `my-theme/wallpaper.jpg`.
2. In [`my-theme/config.json`](my-theme/config.json), set:
   ```json
   {
     "wallpaper": true,
     "wallpaper_dim": 35,
     "dock_glass": true
   }
   ```
3. Push the theme to the TV:
   ```bash
   TV_HOST=172.30.102.100 ./cleanyourtv/tools/theme.sh my-theme
   ```

### Use a Rotating Wallpaper Folder (`wallpapers/`)
1. Create `my-theme/wallpapers/` and place your `.jpg`, `.png`, or `.webp` images inside (filenames using letters, digits, underscores, dots, and hyphens).
2. In [`my-theme/config.json`](my-theme/config.json), set:
   ```json
   {
     "wallpaper": true,
     "wallpaper_rotation_minutes": 30,
     "wallpaper_order": "shuffle",
     "wallpaper_dim": 40
   }
   ```
3. Push with `TV_HOST=172.30.102.100 ./cleanyourtv/tools/theme.sh my-theme`.

### Reset Back to Pitch-Black Apple TV Default
- In `my-theme/config.json`, set `"wallpaper": false` and run `TV_HOST=172.30.102.100 ./cleanyourtv/tools/theme.sh my-theme`, **or**
- Reset all theme overlays back to the built-in `config.xml`:
  ```bash
  TV_HOST=172.30.102.100 ./cleanyourtv/tools/theme.sh --reset
  ```

---

## 5. Optional Extra: Ad-Blocking Private DNS (Android 9+)

> **Check your router first:** If your router already filters or routes traffic by domain through its own DNS (such as **Pi-hole**, **AdGuard Home**, a **VPN with domain lists**, or a fake-IP proxy), **do not enable Private DNS on the TV**—Android's Private DNS (DNS-over-TLS) bypasses the router's DNS, so your router's rules would stop applying to the TV.

If your router does **not** run its own DNS blocker, you can point Android's built-in Private DNS to AdGuard's ad-blocking resolver (blocks ad domains in apps and system UI, **does not block YouTube video ads**):

```bash
# Enable AdGuard Private DNS on 172.30.102.100:
./build-and-install.sh --enable-dns
```
Test that a streaming app plays video normally afterwards.

**To undo Private DNS anytime:**
```bash
./restore.sh --reset-dns
```

---

## 6. Close Network ADB on `172.30.102.100` When Finished

Many HiSilicon `hi352l` builds ship with `ro.adb.secure=0`, which means **anyone on your Wi-Fi can open an unauthenticated shell on port 5555 while ADB is enabled**. Always close ADB when finished:

1. **Turn ADB OFF on the TV:**
   - **Standard Android TV:** **Settings → Device Preferences → Developer options → USB debugging OFF** + **Revoke USB debugging authorizations**.
   - **CVTE `hi352l` firmware:** Switch to TV or HDMI source, press **MENU → 1 1 4 7**, set **DEBUG → ADB ENABLE → OFF**, and press **Back**.
   - **Skyworth/BYINTEK `hi352l` firmware:** Open **Settings → Developer → ADB OFF**.
2. **Reboot** the TV.
3. **Verify port 5555 refuses connections (`Connection refused`):**
   ```bash
   ./build-and-install.sh --verify-closed
   # (Runs: ./cleanyourtv/tools/venv/bin/python -I ./cleanyourtv/tools/adb-closed.py 172.30.102.100)
   ```
   *(If you have a CVTE projector/TV variant where `adb-closed.py` still reports port 5555 open even after turning the toggle off and rebooting, place the TV on an isolated guest Wi-Fi/VLAN where other LAN devices cannot reach port 5555.)*
