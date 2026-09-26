# OLIVER Design Language

The visual system for OLIVER's architecture documentation page (`docs/architecture.html`) and the per-run evidence reports (`runs/<id>/report.html`). The canonical implementation is [`oliver.css`](oliver.css); the layout schema is [`skeleton.html`](skeleton.html).

**Point of view.** OLIVER is an engineering tool, so its pages are drawn like engineering drawings: warm drafting paper, ink rules, a blueprint grid behind diagrams, and a title block in the corner of every drawing. The palette follows the hackathon deck (terracotta for the harness, blue for the model, green for the repository and verified output), so a reader who has seen the brief can read the diagrams without a legend.

## 1. Hard rules

These are enforced by `scripts/check_constraints.py` (rules R5 to R8) on every edit.

| Rule | What it means in practice |
|---|---|
| No CSS custom properties | Every color, size and spacing value is written out as a literal hex or pixel value. There are no property tokens and no var function anywhere. |
| No media-query rules | One layout only: a rigid 960px desktop page. Narrow windows scroll horizontally instead of reflowing. No print or dark-mode variants. |
| No timeline elements | No timelines, roadmaps, steppers, progress tracks or Gantt bars in any diagram, mockup or report. Chronological data (a run trajectory) is always a table. State machines are drawn as loops, never as a straight row of steps. |
| Dedicated logo space | Every page header has a three-slot logo rail, every footer repeats it, and every diagram carries a title block with two logo slots. Content never overlaps these slots. |

## 2. Color palette

All text pairs meet WCAG AA (4.5:1). Ratios were computed from the hex values, not estimated.

### Surfaces and ink

| Role | Name | Hex | Used for | Contrast note |
|---|---|---|---|---|
| Background | Paper | `#F4EFE6` | Page background | – |
| Surface | Card | `#FBF8F2` | Cards, header slots, diagram canvas, tables | – |
| Sunken surface | Sunken | `#EFE9DE` | Code blocks, table heads, inline code | – |
| Grid | Grid | `#E6DED0` | Blueprint grid lines, code titles, disabled fills | decorative |
| Border | Hairline | `#D6CCBC` | 1px dividers, card borders, hard shadows | decorative |
| Strong border | Ink | `#2B2D31` | 2px structural rules, diagram nodes, secondary buttons | 12.0:1 on Paper |
| Placeholder border | Dash | `#C9BDA9` | Dashed outlines of empty logo slots only | decorative |

### Text

| Role | Hex | Contrast |
|---|---|---|
| Primary text | `#1F2328` | 13.8:1 on Paper, 14.9:1 on Card |
| Secondary text | `#50545C` | 6.6:1 on Paper, 6.3:1 on Sunken |
| Muted text | `#5F636B` | 5.3:1 on Paper, 5.7:1 on Card |

### Brand and interaction

| Role | Name | Hex | Used for | Contrast |
|---|---|---|---|---|
| Primary | Terracotta | `#A9532F` | Primary buttons, links, eyebrows, harness strips | white text 5.3:1; 4.6:1 on Paper |
| Primary hover | Terracotta Deep | `#8E4526` | Hover and pressed states, text on the tint | 5.6:1 on tint |
| Primary tint | Terracotta Tint | `#F6E4DA` | Callout background, selected rows | – |
| Secondary | Slate | `#4E6380` | Focus ring, info callout border, running state | 5.4:1 on Paper |
| Secondary tint | Slate Tint | `#E3E9F1` | Info callouts, diff hunk headers | – |

### Diagram and chart identity hues

Validated with the dataviz palette validator (lightness band, chroma floor, colorblind separation, normal-vision separation, contrast), all pairs, on the Card surface: **all checks pass**, worst colorblind pair ΔE 9.7 (target 8).

| Slot | Meaning | Hex |
|---|---|---|
| 1 | Harness module | `#A9532F` |
| 2 | Foundation model (OLIVER) / single-series bars | `#3A6FB5` |
| 3 | Repository and verified output | `#2A9D74` |

Identity is never carried by color alone: every node has a visible text label and each diagram has a legend.

### Semantic status (test results)

Status colors are reserved for state and never reused for identity. They always appear with a glyph and a text label.

| State | Glyph | Text | Background | Border | Dot | Text contrast |
|---|---|---|---|---|---|---|
| Pass | ✓ | `#1E6B3A` | `#DDF0E3` | `#9FCFB0` | `#2E8B57` | 5.5:1 |
| Fail | ✕ | `#A12A1E` | `#FBE3DF` | `#EDB1A7` | `#C8372D` | 6.0:1 |
| Error | ! | `#FFFFFF` | `#A12A1E` | `#A12A1E` | `#C8372D` | 7.3:1 |
| Skipped / timeout | – | `#7A5200` | `#FCEFCF` | `#E8CB85` | `#B07A00` | 6.1:1 |
| Running / pending | … | `#3A4F6E` | `#E3E9F1` | `#B7C4D8` | `#4E6380` | 6.8:1 |

Diff lines reuse the same families: added `#1E4D2E` on `#DDF0E3` (8.2:1), removed `#7E2419` on `#FBE3DF` (8.0:1), hunk headers `#3A4F6E` on `#E3E9F1` (6.8:1), file headers `#5F636B`.

## 3. Typography

System and web-safe fonts only; nothing is downloaded.

- **Sans:** `-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif`
- **Mono:** `ui-monospace, "SFMono-Regular", Menlo, Consolas, "Liberation Mono", "Courier New", monospace`

| Style | Font | Size / line height | Weight | Details |
|---|---|---|---|---|
| H1 | Sans | 40px / 48px | 700 | letter-spacing -0.5px; margin 0 0 16px |
| H2 | Sans | 28px / 36px | 700 | margin 64px 0 16px; padding-bottom 8px; 2px Ink bottom rule |
| H3 | Sans | 20px / 28px | 600 | margin 32px 0 12px |
| H4 | Mono | 14px / 20px | 600 | uppercase; letter-spacing 1px; `#50545C` |
| Body | Sans | 16px / 26px | 400 | paragraph margin 0 0 16px |
| Lead | Sans | 18px / 30px | 400 | `#50545C` |
| Caption | Sans | 13px / 20px | 400 | `#5F636B` |
| Eyebrow | Mono | 12px / 16px | 600 | uppercase; letter-spacing 1.5px; Terracotta |
| Wordmark | Mono | 28px / 32px | 700 | letter-spacing 6px |
| Code block | Mono | 13px / 20px | 400 | on Sunken |
| Inline code | Mono | 14px / 20px | 400 | Sunken background; padding 1px 6px; radius 4px |
| Button | Sans | 14px / 20px | 600 | – |
| Badge | Mono | 12px / 16px | 600 | uppercase; letter-spacing 0.5px |
| Table head | Mono | 12px / 16px | 600 | uppercase; letter-spacing 0.5px |
| Table cell | Sans | 14px / 22px | 400 | numbers right-aligned, mono, tabular figures |
| KPI value | Sans | 32px / 40px | 600 | proportional figures (large standalone numbers) |

## 4. Spacing, borders, radii, shadows

- **Spacing scale (px):** 4, 8, 12, 16, 20, 24, 32, 48, 64. Section headings open with 64px; cards pad 24px; tables pad cells 10px 12px.
- **Borders, three kinds only:** 1px solid `#D6CCBC` (default), 2px solid `#2B2D31` (structure: header and footer rules, figure frames, diagram nodes, H2 underline), 1px dashed `#C9BDA9` (empty placeholders only).
- **Radii:** 4px (inline code, logo slots), 6px (cards, buttons, code blocks, tables' container), 999px (badges).
- **Shadows:** hard offset, never blurred, echoing the offset blocks of the event deck. Cards and KPI tiles `4px 4px 0 #D6CCBC`; primary buttons `2px 2px 0 #2B2D31`; diagram nodes draw the same 4px offset in Hairline.

## 5. Components

Exact rules live in `oliver.css`; the key values are below.

### Containers
- `body`: margin 0; min-width 960px; background `#F4EFE6`.
- `.page`: width 960px; margin 0 auto.
- `.content`: padding 48px 10px 64px, which gives a 940px content column.
- `.card`: background `#FBF8F2`; border 1px solid `#D6CCBC`; radius 6px; padding 24px; shadow `4px 4px 0 #D6CCBC`. Accent variants add a 6px top border: `.accent-harness` `#A9532F`, `.accent-model` `#3A6FB5`, `.accent-tools` `#2A9D74`.

### Buttons
| Variant | Background | Text | Border | Extras |
|---|---|---|---|---|
| `.btn` base | – | – | 1px solid `#2B2D31` | padding 10px 18px; radius 6px; 600 14px/20px sans |
| `.btn-primary` | `#A9532F` (hover `#8E4526`) | `#FFFFFF` | `#8E4526` | shadow `2px 2px 0 #2B2D31`; pressed shifts 2px and drops the shadow |
| `.btn-secondary` | `#FBF8F2` (hover `#EFE9DE`) | `#1F2328` | `#2B2D31` | – |
| `.btn-ghost` | transparent | `#A9532F` | transparent | underlined; padding 10px 4px |
| disabled | `#E6DED0` | `#7D766B` | `#D6CCBC` | no shadow; cursor not-allowed |
| focus | – | – | – | outline 2px solid `#4E6380`; offset 2px |

### Code blocks and diffs
- `.code-block`: background `#EFE9DE`; border 1px solid `#D6CCBC`; radius 6px.
- `.code-title`: background `#E6DED0`; 12px mono `#50545C`; padding 8px 16px; file name left, meta right.
- `pre`: padding 16px; 13px/20px mono; horizontal scroll, never wrapping.
- `.diff` + `.diff-line`: one block element per line (`white-space: pre`), coloured full-width with `.diff-add`, `.diff-del`, `.diff-hunk`, `.diff-meta`.

### Status indicators
- `.badge`: pill (radius 999px), padding 2px 10px, 1px border, 12px mono uppercase. Variants `.badge-pass`, `.badge-fail`, `.badge-error`, `.badge-skip`, `.badge-running`. Content is always glyph + word: `✓ Pass`, `✕ Fail`, `! Error`, `– Skipped`, `… Running`.
- `.status` + `.status-dot`: 8×8px dot followed by a text label; dot colors from the status table. Color is never the only signal.

### Tables
- Head: Sunken background, 12px mono uppercase `#50545C`, 2px Ink bottom rule.
- Rows: 1px Hairline separators, 10px 12px padding, `.num` cells right-aligned with tabular figures, `.mono` cells for paths and test ids.

### Callouts
- `.callout`: 4px left border `#A9532F` on `#F6E4DA`, title in `#8E4526`.
- `.callout-info`: 4px left border `#4E6380` on `#E3E9F1`, title in `#3A4F6E`.

### Report figures
- **KPI row** (`.kpi-row`): four 220px tiles with 20px gaps. Each tile: label (sentence case, no colon), value (32px sans 600, compact numbers such as `18.4K`), optional sub-line.
- **Bar chart** (`.chart`): single series, so one color (`#3A6FB5`) for every bar and no legend box; the title names the measure. Bars are 16px thick, grow from one baseline, square at the baseline with a 4px rounded end. Values sit at the bar tips in text colors, never in the bar color. Gridlines are 1px solid `#E6DED0`. Every bar has a native hover title, and the token table in the report is the chart's table view.

## 6. Layout structure

```
body (min-width 960px, Paper)
└─ .page                    width 960px; margin 0 auto
   ├─ header.site-header    height 96px; padding 0 10px; 2px Ink bottom rule
   │   ├─ .brand            460px: OLIVER wordmark + caption
   │   └─ .logo-rail        460px: 3 × .logo-slot (140 × 56), gap 20px, right-aligned
   ├─ nav.doc-nav           height 48px; static anchors; gap 24px; 1px Hairline rule
   ├─ main.content          padding 48px 10px 64px  → 940px column
   │   ├─ section.hero      eyebrow, H1, lead, meta row of badges
   │   ├─ .grid-12          12 × 60px columns, 20px gutters (940px)
   │   │                    span-3 220 · span-4 300 · span-6 460 · span-8 620 · span-12 940
   │   └─ figure.figure     940px frame, 2px Ink border, 936px drawing canvas
   │                        title block 300 × 88 inset 16px bottom-right (logo slots)
   └─ footer.site-footer    padding 32px 10px; 2px Ink top rule;
                            credits (520px) left, 3 × .logo-slot-sm right
```

No flex wrapping and no percentage widths on layout blocks; every block has a pixel size.

## 7. Logo space

| Slot | Where | Size | Image area |
|---|---|---|---|
| `.logo-slot` | Header logo rail (LCC, DevClub, Team) | 140 × 56 | 120 × 40, `object-fit: contain` |
| `.logo-slot-sm` | Footer rail and diagram title blocks | 96 × 40 | 80 × 28, `object-fit: contain` |

Empty slots show a dashed `#C9BDA9` outline and an uppercase mono label. See [`../assets/logos/README.md`](../assets/logos/README.md) for file names; the report generator embeds any logo files it finds.

## 8. Diagrams

- Inline SVG with a fixed viewBox (936 wide), hardcoded fills and strokes, a 24px blueprint grid pattern, and one arrow marker per ink color.
- Nodes: Card fill, 2px Ink stroke, 7px identity strip on top, 4px hard shadow in Hairline. Title 14px sans 600; file name 11px mono `#50545C`.
- Edges: 1.5px Ink with arrowheads; the recovery path is dashed Terracotta (`6 4`). Edge labels are 11px mono in text colors.
- Every drawing has a legend (bottom left) and a title block (bottom right, 300 × 88) with two logo slots, the drawing name, sheet number and revision.
- Control flow is drawn as a loop; there are no straight step tracks or dated milestones.

## 9. Reports

Generated by `oliver/report.py` for every run: a standalone HTML file with this stylesheet inlined, all run data HTML-escaped. Sections, in order: header with logo rail; KPI row (status, tests passing, tokens, wall time); tokens-by-phase bar chart; verification table (baseline vs final badges per test); patch diff; trajectory table (step, phase, tool, target, outcome badge, tokens); token table per phase; footer.
