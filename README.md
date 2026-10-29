# pranena

## Installed skills: Replica (11 skills)

Source: [Jakeschincariol/replica-skill](https://github.com/Jakeschincariol/replica-skill) — MIT.
Installed project-locally in `.claude/skills/`, so they load automatically when working in this repo.

| step | skill | what it does |
| --- | --- | --- |
| 1 | `replica-recon` | Reverse-engineer an app: screens, flows, components, data model, feature matrix |
| 2 | `replica-architect` | Stack, database schema, API routes, build order |
| 3 | `replica-design` | Design tokens, type scale, spacing, component specs (+ WCAG contrast check) |
| 4 | `replica-build` | Rebuild the app screen by screen from the recon map |
| 5 | `replica-backend` | Auth, database, payments, email, integrations |
| 6 | `replica-test` | Test plan, Playwright e2e, severity-ranked bug reports |
| 7 | `replica-diff` | Parity score + screenshot layout diff against the original |
| 8 | `replica-entrepreneur` | Mines real public reviews into a fix plan and positioning angle |
| 9 | `replica-brand` | Name, palette, logo brief, voice, and a sweep for the original's marks |
| 10 | `replica-launch` | Landing page, pricing, App Store / Play listing lint |
| 11 | `replica-deploy` | Preflight gate, production env, DNS, ship |

Run them in order; each reads what the last wrote from a `replica/` folder in your project.

```
recon -> architect -> design -> build -> backend -> test -> diff -> entrepreneur -> brand -> launch -> deploy
```

### Tools (stdlib Python only, no network)

```bash
python3 .claude/skills/replica-diff/imgdiff.py original.png clone.png --out diff.png
python3 .claude/skills/replica-diff/parity.py replica/features.csv
python3 .claude/skills/replica-entrepreneur/reviews.py replica/reviews.csv
python3 .claude/skills/replica-design/contrast.py replica/design/tokens.json
python3 .claude/skills/replica-brand/sweep.py . --avoid "Original App"
python3 .claude/skills/replica-launch/listing.py replica/launch/listing.json
```

### Verifying the install

```bash
python3 -m unittest discover -s .claude/skills/tests   # 55 tool tests pass
python3 tools/vocals/mdx_pipeline_prototype.py         # DSP reference check (needs numpy)
```

The pack's two packaging tests (`test_repo.py`) expect the upstream plugin manifest and
pack README at the repo root; they are expected to fail here and do not affect the skills.

## SimpMusic vocals-only

`simpmusic-vocals-only/` holds the fix for the SimpMusic "vocals only" mode: the
filter bank that never loaded a model is replaced with on-device MDX-Net
(`Kim_Vocal_2.onnx`) plus a bleed suppressor. See that folder's README.

`tools/vocals/mdx_pipeline_prototype.py` is the numpy reference the Kotlin was ported
from — it verifies the 6144-point mixed-radix FFT, the STFT/iSTFT round trip and the
chunk overlap-add before any of it went into the app.

### APK with the vocals-only fix

`.github/workflows/build-simpmusic-vocals.yml` builds it from your SimpMusic fork and
publishes it at
[releases/tag/simpmusic-vocals-only-v4](https://github.com/viralshortsreaction18-netizen/pranena/releases/tag/simpmusic-vocals-only-v4)
(arm64, armeabi-v7a, universal). The v1 crash — R8 stripping ONNX Runtime's JNI
classes — and its fix are documented in `simpmusic-vocals-only/README.md`.
