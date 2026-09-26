# Logo slots

Drop organization logos here. Pages ship with dashed placeholder slots until files exist.

| File name | Organization | Shown in |
|---|---|---|
| `lcc.svg` or `lcc.png` | LCC | header rail, footer rail, diagram title blocks |
| `devclub.svg` or `devclub.png` | DevClub | header rail, footer rail, diagram title blocks |
| `team.svg` or `team.png` | Your team | header rail, footer rail |

## Sizes

| Slot | Box | Image area | Recommended source |
|---|---|---|---|
| Header `.logo-slot` | 140 × 56 px | 120 × 40 px | SVG, or PNG at 240 × 80 px (2x) |
| Footer / title block `.logo-slot-sm` | 96 × 40 px | 80 × 28 px | same file, scaled with `object-fit: contain` |

Use a transparent background. Logos keep their own colors; the slot adds only a 1px Hairline border.

## Using a logo

- **Run reports:** `oliver/report.py` looks for the files above and embeds them automatically.
- **Static pages:** replace a placeholder such as
  `<div class="logo-slot is-empty"><span class="logo-slot-label">LCC logo</span></div>`
  with `<div class="logo-slot"><img src="assets/logos/lcc.svg" alt="LCC"></div>`
  (from `docs/design/`, use `../assets/logos/...`).
- **Diagram title blocks:** replace the dashed `<rect>` and its label with
  `<image href="assets/logos/lcc.svg" x="632" y="482" width="80" height="28"/>`.
