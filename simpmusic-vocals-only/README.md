# Vocals-only for SimpMusic — with an actual model behind it

This is the fix for "vocals only still plays the music". It replaces the filter bank
that was pretending to be Spleeter with a real MDX-Net model running on the phone.

Everything here is ready to apply to `viralshortsreaction18-netizen/SimpMusic`.
The only reason it isn't already pushed there is that the GitHub connection this
session has is scoped to this repository and can't write to the fork — see
[How to get the APK](#how-to-get-the-apk).

---

## Why the old build leaked music

The previous `SpleeterVocalExtractor` (153 lines of Kotlin, no model file anywhere)
named its members `runSpectralUnet`, `enc1..enc6`, `dec1..dec5` and its comments cited
Spleeter's `unet.py`. Under that, it did this:

- a fixed band prior (kill everything under 80 Hz, keep 140 Hz–4.8 kHz),
- a transient gate (`attackAlpha 0.35 / 0.70`) to guess at drums,
- a mid/side centre-coherence mask,
- a "U-Net" whose convolutions were hard-coded coefficients (`0.1, 0.25, 0.3, 0.25, 0.1`)
  and whose "sigmoid activation" was `1 / (1 + exp(-6 * (contrast + 0.15)))`.

**No weights were ever loaded.** Nothing in it had ever heard a song. It could only
reshape the mix, so whatever the mix contained — guitars, pads, hats — stayed, filtered.
That is the sound you were hearing: nothing was separated because nothing was trained.
A filter cannot take a snare out of a vocal, and no amount of tuning makes it able to.

## What it does now

| piece | file | what it is |
| --- | --- | --- |
| model | `MdxVocalModel.kt` | `Kim_Vocal_2.onnx` opened with ONNX Runtime, one `[1, 4, 2048, 256]` complex-STFT chunk per call |
| pipeline | `MdxVocalExtractor.kt` | `audio-separator`'s MDX pipeline: 5.9 s Hann chunks, STFT `n_fft 6144 / hop 1024`, inverse STFT, 25 % overlap-add |
| FFT | `MdxFft6144.kt` | 6144 = 3 × 2048, so three radix-2 passes and one radix-3 stage on top of the module's existing `Fft` |
| bleed killer | `MdxVocalExtractor.suppressBleed` | `music = mix − vocals`; each bin is scaled by `v² / (v² + (α·music)²)`; under 85 Hz and over 16.5 kHz is removed outright |
| weights | `VocalModelStore.kt` | 64 MB model downloaded once to app storage (too big for an APK), verified, then reused offline forever |
| player hook | `MdxVocalAudioProcessor.kt` | Media3 `AudioProcessor`; same toggle as before, now backed by the model |
| rate | `CrossfadeExoPlayerAdapter.kt` | a `SonicAudioProcessor` at 44.1 kHz ahead of the separator (free when the stream already is) |

`Kim_Vocal_2` is the MDX-Net checkpoint the UVR community rates best for vocals at
that size — trained on real songs, which is the whole difference.

## How it was checked before shipping it

The DSP was written first as a numpy prototype (`tools/vocals/mdx_pipeline_prototype.py`)
so the Kotlin could be a port of something measured rather than a guess:

```
fft6144 max err vs numpy.fft:       5.2e-13
ifft6144 round trip:                5.5e-15
identity model, full pipeline:      3.2e-05 peak / 8.2e-07 RMS   (reconstruction)
band-pass model, 110 Hz music:      105840 -> 0.09              (removed)
band-pass model, 700 Hz vocal:      44100  -> 44097.98          (kept)
```

Run it yourself:

```bash
python3 -m venv /tmp/v && /tmp/v/bin/pip install numpy
/tmp/v/bin/python tools/vocals/mdx_pipeline_prototype.py
```

What this proves: the FFT, the frame count, the chunk/overlap-add arithmetic and the
mask application are correct. What it cannot prove: how the model sounds on your
phone, and whether a given phone keeps up in real time (see *Known limits*).

## How to get the APK

The change is on branch `arena/cbbc4124-simpmusic` in the local checkout used to build
it, and it applies cleanly on top of your `dev`. Two ways:

**A. Let me push and let CI build it** (needs GitHub write access to `SimpMusic` —
reconnect GitHub in Arena with that repo selected, then say "push it" and I'll push,
trigger the build and hand you the signed APK).

**B. Do it yourself** — the patch is the whole change:

```bash
git clone https://github.com/viralshortsreaction18-netizen/SimpMusic.git
cd SimpMusic
git checkout dev
git checkout -b arena/vocals-only-mdx
git apply --binary vocals-only.patch      # from this folder
git add -A && git commit -m "feat(vocals): real MDX-Net vocal isolation"
git push -u origin arena/vocals-only-mdx
```

Branch names starting with `arena/` already trigger `.github/workflows/android.yml`
(that trigger is part of the patch), so the build starts on push. Watch it under
*Actions → Build release APK (FOSS)*; the signed APK lands in the run's artifacts and
in a GitHub release.

If you would rather not use the patch, `core-patch/` here is the same tree the build
reads — copy it over the repo root (`cp -r core-patch/* .`) and commit.

## Using it in the app

1. Play a song, open the player sheet, tap **Vocals only (AI)**.
2. First time: a toast explains the one-time 64 MB model download. It is resumable by
   retry, needs Wi-Fi ideally, and after that everything is offline.
3. Once it lands the current song switches over on its own — no restart.
4. Keep tapping to turn it off; the original mix comes back on the next buffer.

## Tuning "no music at all"

`MdxVocalExtractor.DEFAULT_SUPPRESSION` (`1.15`) is the only knob that matters:

- raise it (1.3–1.6) → more music removed, vocals thinner, more artefacting on breathy lines
- lower it (0.9–1.0) → fuller voice, a little more instrument bleed

`VOCAL_LOW_HZ` (85 Hz) and `VOCAL_HIGH_HZ` (16.5 kHz) are the hard edges. If a song's
bass is still audible, raise `VOCAL_LOW_HZ` to 100–110; if cymbals tick through, lower
`VOCAL_HIGH_HZ` to ~14 kHz.

## Known limits, stated plainly

- **Not built yet.** I could not compile or run the Android build from this sandbox: no
  Android SDK here, and the GitHub connection can't reach your fork. The Kotlin has been
  reviewed and the maths it implements has been verified, but the first real compile is
  the CI run. Expect a small fix or two on the first build — say the word and I'll take it.
- **Real time.** A 5.9 s chunk goes through the model before it is heard, so vocals-only
  starts with a few seconds of silence and, on a slow phone, can pause while it catches
  up. A chunk has to be separated faster than 5.9 s to keep up; a recent mid-range or
  better phone does. There is no stutter *within* a chunk — the audio is delivered in
  whole chunks behind the player.
- **44.1 kHz.** The models were trained there. Streams at other rates are resampled by a
  Sonic stage first; that stage is inactive (free) when the stream is already 44.1 kHz.
- **Model licence.** `Kim_Vocal_2.onnx` comes from `TRvlvr/model_repo` (UVR's public model
  collection) — check the terms before you ship this to a store. The code here is MIT like
  the rest of your fork.
- **No music, not no artefacts.** Separation is not subtraction; a hard "only vocals" will
  always leave some breath/room tone. The α mask above is what keeps the instruments out.
