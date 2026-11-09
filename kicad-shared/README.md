# Shared KiCad footprints and 3D models

Used by both boards ([pihat](../pihat) and [sa818s-hat](../sa818s-hat)) through
`${KIPRJMOD}/../kicad-shared` in each project's `fp-lib-table` and footprint model paths.
`kicadgen/sharedlib.py` maintains it; the board build scripts call it.

## `3dmodels/`

STEP models for parts KiCad's own library doesn't cover, downloaded from LCSC / EasyEDA for the
exact part with [easyeda2kicad](https://github.com/uPesy/easyeda2kicad.py). Each one was checked
to sit centred on its footprint's origin at z = 0, so footprints use them with no offset or rotation.

| File | Part | LCSC | Body (mm) |
|---|---|---|---|
| `Bourns_SM-LP-5001E.step` | Bourns SM-LP-5001E 600:600 Ω transformer (T1, T2) | C840532 | 14 × 9 × 7.5 |
| `NiceRF_SA818S.step` | NiceRF SA818S-U transceiver module | C3001549 | 35.6 × 19 × 3.2 |
| `PTC_1812_4.5x3.2x1.0mm.step` | 1812 PTC fuse (F1) | C18198333 | 4.5 × 3.2 × 1.0 |
| `Oscillator_3225_HSO321S.step` | HSO321S 12 MHz oscillator, 3225 (Y1) | C160457 | 3.2 × 2.5 × 0.9 |

## `radio-remote.pretty/`

Copies of stock KiCad footprints whose 3D model reference is broken in KiCad 10, with the model
fixed. Regenerated from the installed KiCad library on every build, so don't edit them by hand.

| Footprint | Model |
|---|---|
| `Fuse_1812_4532Metric` | `3dmodels/PTC_1812_4.5x3.2x1.0mm.step` (KiCad has no 1812 fuse model) |
| `Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm` | `3dmodels/Oscillator_3225_HSO321S.step` (KiCad has none) |
| `Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias` | KiCad's `Texas_HTSOP-8-1EP_…_ThermalVias.step` (the stock footprint names a renamed file) |
| `TI_SO-PowerPAD-8_ThermalVias` | KiCad's `TI_SO-PowerPAD-8.step` (same problem) |

The FXL0630 inductors (L1, and L2 on the SA818S board) use KiCad's own
`Inductor_SMD:L_Changjiang_FXL0630`, the footprint and model for that exact part.
