---
name: ad_image_create
description: Create ad-ready product images (single or collage) by back-solving sub-image sizes from target output ratio, grounding scene design with media_comprehension, generating images via image_generator with strict request params and actor-count control, and pairing each deliverable with a short social tagline for 小红书/抖音.
---

# Ad Image Creation

## What this skill does

Generate advertising images from product assets with two output styles:
- Single hero image
- Collage image (multiple sub-images stitched into one final canvas)

Core method: decide the final target ratio first, then compute sub-image sizes, and call `image_generator` directly with matching `size` (no manual pre-crop/pre-pad on source assets).

## Required workflow

1. Understand final deliverable:
   - Final ratio and size (for example `16:9`, `1920x1080`)
   - Single image or collage layout (`2x2`, `1x3`, `1x2`)
2. Activate product understanding:
   - `SKILL__active_skill(skill_name="media_comprehension")`
   - Extract product style, tone, audience, and suitable scene category.
3. Design scenes that match product positioning:
   - Keep style consistent with product quality/tone.
   - Avoid mismatched backgrounds (for example: minimal product + ultra-baroque palace).
4. Generate each sub-image using `image_generator` with exact request params.
5. Stitch sub-images (if collage), then validate final size/ratio.
6. **Social copy:** After images are final, add **one** short line of ad copy **per** deliverable image—the same count as the exported ad files (one hero → one line; four separate exports → four lines; one stitched collage file usually → one line unless the user asked for per-panel copy). Keep each line simple, fun, and tightly tied to that image’s scene and benefit; aim for **小红书** / **抖音** scroll appeal, not generic brand platitudes.

## Supporting actor references (Mode 2/3)

When the ad needs a supporting actor beyond the product—either because the user asked for one or because they supplied material—do **not** fetch companion assets from TikTok or similar platforms. Use what is already available:

- **User supplied still image(s):** Use the provided file path(s) as `reference_images` for `image_generator` after a quick `media_comprehension` check that the image shows the intended actor/look.
- **User supplied video:** Capture one or more frames (screenshots) from that video in the workspace, run `SKILL__active_skill(skill_name="media_comprehension")` on each candidate frame, and pick a frame where the model confirms the desired supporting actor/appearance. Use that frame image as `reference_images`.

If the user provides no usable image or video reference, you may still proceed: call `image_generator` without actor `reference_images` and describe the supporting actor so the model generates that character in-scene—still following the actor-count rules below.

## `image_generator` request contract (keep these fields)

### Common fields

```python
image_generator(
    content="...",
    info={
        "image_url": "/path/to/product.jpg",
        "size": "960x540",
        "output_dir": "/path/to/output"
    }
)
```

- `content`: prompt describing scene and composition.
- `info.image_url`: primary product image path.
- `info.size`: output size string in `"WIDTHxHEIGHT"` format.
- `info.output_dir`: output directory.

### Optional field for actor/reference inputs

```python
"reference_images": ["/path/to/ref1.jpg", "/path/to/ref2.jpg"]
```

## Input modes

### Mode 1: Product only

- Input: one product image
- Output: product integrated into environment
- Use when emphasizing material, shape, and style fit.

### Mode 2: Product + one actor reference

- Input: product image + one actor image
- Output: product and actor in one scene
- Use when showing usage context and emotional connection.

### Mode 3: Product + multiple reference images

- Input: product image + multiple references
- Output: richer scene with better pose/style guidance
- Still enforce actor-count language in prompt.

## Critical rule: actor-count control

When using Mode 2/3, model may generate too many actors unless count is explicit.

### Required prompt pattern

- Use explicit count language:
  - Chinese: `只有一只/个`, `最多两只/个`
  - English: `only one`, `a single`, `at most two`
- Recommended actor count:
  - Ad focus: 1 actor (preferred)
  - Lifestyle scene: max 2 actors

### Good vs bad prompt snippet

```python
# Good
content = "Create a warm living-room scene. There is only one cat interacting with the cat tree."

# Bad
content = "Create a warm scene with cats interacting with the cat tree."
```

## Size back-solving quick table

| Final size | Layout | Sub-image size |
|---|---|---|
| 1920x1080 (16:9) | 2x2 | 960x540 |
| 1920x1080 (16:9) | 1x3 | 640x1080 |
| 1920x1080 (16:9) | 1x2 | 960x1080 |
| 1600x1200 (4:3) | 2x2 | 800x600 |
| 1600x1200 (4:3) | 1x3 | 533x1200 |
| 1080x1080 (1:1) | 2x2 | 540x540 |
| 1080x1080 (1:1) | 1x3 | 360x1080 |
| 1080x1920 (9:16) | 2x2 | 540x960 |

## Minimal implementation template

```python
from PIL import Image

# 1) Analyze product style first
SKILL__active_skill(skill_name="media_comprehension")

# 2) Decide target and layout
final_size = (1920, 1080)
layout = "2x2"
sub_size = (960, 540)

# 3) Generate sub-images
for scene in scenes:
    content = scene["prompt"]  # include explicit actor count for Mode 2/3
    info = {
        "image_url": product_image,
        "size": f"{sub_size[0]}x{sub_size[1]}",
        "output_dir": output_dir
    }
    if scene.get("reference_images"):
        info["reference_images"] = scene["reference_images"]
    image_generator(content=content, info=info)

# 4) Stitch
canvas = Image.new("RGB", final_size, (255, 255, 255))
# paste each sub-image by layout...
canvas.save("final_ad.png", quality=95)
```

## Quality checks

- One social tagline per final ad image (step 6): tone fits 小红书/抖音 skim-reading; matches that image, not a generic slogan.
- Product style and environment are consistent.
- For Mode 2/3, actor count is explicitly constrained in `content`.
- `size` values match computed sub-image dimensions.
- Final stitched output matches requested ratio/size.
- If generator output has slight dimension drift (for example height offset), crop after stitching.

## Notes

- No source pre-processing required by default; rely on `size` control in generation.
- Use high-quality product/reference inputs.
- Keep scene descriptions concrete (lighting, furniture, color palette, mood) instead of vague labels.
- Social taglines: default to concise **Chinese** for 小红书/抖音 unless the user specifies another language or brand voice.
