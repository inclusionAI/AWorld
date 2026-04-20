---
name: ad_video_create
description: Create ad-ready product video from product images, with or without character/subject images. The workflow leverages AI-powered image composition, scene understanding, and video generation. Video prompts should follow commercial shot language—visual hooks, product presence, hero shots, detail showcase, function expression, and dynamic visuals.
---

## Workflow Architecture

### Phase 1: Asset Preparation & Analysis
**Input Requirements:**
- **Primary Asset (Required)**: Product image (e.g., cat tower, furniture, gadget)
- **Character/Subject Asset (Optional)**: Supporting character image (e.g., pet, person, lifestyle element)
- **Audio Asset (Optional)**: Background music file (MP3 format)

**Process:**
1. **Asset Discovery**: Scan working directory for available assets
2. **Media Comprehension**: 
   - Activate `media_comprehension` skill
   - Analyze product image to understand:
     - Product features and characteristics
     - Color palette and material textures
     - Suitable environment context
   - If character image exists, analyze its attributes (appearance, pose, mood)

---

### Phase 2: Character Generation (Conditional)
**Trigger Condition**: No character/subject image provided

**Process:**
1. Based on product analysis from Phase 1, determine appropriate character type:
   - For pet products → Generate pet character (matching product target audience)
   - For home goods → Generate lifestyle character or scene element
   - For tech products → Generate user persona or usage scenario
2. Call `image_generator` with detailed prompt:
   - Character attributes aligned with product positioning
   - Pose and expression suitable for composition
   - Style consistency with product aesthetic

**Output**: Character image ready for composition

---

### Phase 3: Image Composition with Environment
**Objective**: Create a realistic advertisement scene combining product + character + environment

**Key Requirements:**
- **Single Character Constraint**: Ensure only ONE character appears in final composition
- **Environment Background**: Must include realistic home/lifestyle setting, not plain white background
- **Natural Integration**: Character should interact naturally with product

**Process:**
1. Prepare input images:
   - Product image (original or compressed if >50KB)
   - Character image (from Phase 2 or user-provided)
2. Call `image_generator` with composition directive:
   ```json
   {
     "content": "Compose [character description] with [product description] in [environment setting]. 
                 Requirements:
                 - Only ONE character in the scene
                 - Realistic home environment (floor, walls, natural lighting, plants, furniture)
                 - Natural interaction between character and product
                 - Professional product photography style",
     "info": {
       "image_urls": ["product.jpg", "character.jpg"],
       "size": "1328x1328",
       "guidance_scale": 4.5-5.0,
       "num_inference_steps": 30-35,
       "watermark": false,
       "output_path": "./composed_ad_image.png"
     }
   }
   ```

**Output**: High-quality composed advertisement image with environment

---

### Phase 4: Video Generation
**Objective**: Transform static composition into dynamic advertisement video

**Shot & visual language (required):** Across the ~10s runtime, the motion and camera work should **cover** these elements where applicable (not necessarily every second, but the final cut should feel like a mini commercial, not a single static pan):

| Element | Meaning |
|--------|---------|
| **Visual hooks (视觉因子)** | Strong focal points, contrast, color, light, or composition that hold attention |
| **Product presence (产品出现)** | Clear establishment of the product in frame—viewer knows what is being advertised |
| **Product / hero shots (产品镜头)** | Dedicated beats where the product is the clear subject (center framing, readable silhouette) |
| **Detail showcase (细节展示)** | Close-ups or slow emphasis on materials, texture, craftsmanship, or key parts |
| **Function / benefit expression (功能表达)** | Motion that implies use, outcome, or core selling point (interaction, before/after feel, problem–solution rhythm) |
| **Dynamic visuals (动态视觉)** | Varied motion: camera (push, pan, subtle orbit), parallax, light shifts, or subject micro-movement—avoid one flat move for the whole clip |

When writing `video_diffusion` prompts, **spell out** which of the above appear in sequence (e.g. establish product → detail → function beat → dynamic wrap). If the source image is character-heavy, still reserve beats for product-first shots.

**Audio Handling Strategy:**

#### Case A: User-Provided Audio (MP3 exists in directory)
1. Generate video WITHOUT audio first via `video_diffusion`:
   ```json
   {
     "content": "Create dynamic advertisement video (mini-commercial pacing, ~10s):
                 - Visual hooks: strong focal points, light/color contrast where fitting
                 - Product presence: early establishment of the product in frame
                 - Product hero shots: beats where the product is clearly the subject
                 - Detail showcase: close-up or emphasis on texture/material/key parts
                 - Function expression: motion suggesting use, benefit, or core value
                 - Dynamic visuals: varied motion (camera push/pan/subtle orbit, parallax, light shifts, optional character micro-movements)
                 - Professional commercial quality",
     "info": {
       "image_url": "./composed_ad_image.png",
       "resolution": "720p",
       "duration": 10,
       "fps": 24,
       "output_dir": "./",
       "sound": "off"
     }
   }
   ```
2. Merge video with user's MP3 using FFmpeg:
   ```bash
   ffmpeg -i generated_video.mp4 -i user_audio.mp3 -t 10 \
          -c:v copy -c:a aac -b:a 192k \
          -map 0:v:0 -map 1:a:0 -shortest \
          final_ad_video.mp4 -y
   ```

#### Case B: No User Audio (Generate with AI audio)
1. Call `video_diffusion` with audio generation enabled:
   ```json
   {
     "content": "Create dynamic advertisement video with suitable background music (mini-commercial pacing, ~10s):
                 - Visual hooks; product presence; hero product shots; detail showcase; function/benefit expression; dynamic visuals (varied camera and motion)
                 - AI-generated background music matching product mood
                 - Professional commercial quality",
     "info": {
       "image_url": "./composed_ad_image.png",
       "resolution": "720p",
       "duration": 10,
       "fps": 24,
       "output_dir": "./",
       "sound": "on"
     }
   }
   ```

**Output**: Final advertisement video (10 seconds, 720p, with audio)

---

## Best Practices

### Image Compression
- **Always check file size** before reading images with `media_comprehension`
- **Compress if >50KB** using PIL/Pillow:
  ```python
  from PIL import Image
  img = Image.open(path)
  if img.mode in ('RGBA', 'LA', 'P'):
      img = img.convert('RGB')
  img.save(output_path, 'JPEG', quality=85, optimize=True)
  ```

### Prompt Engineering for Composition
- **Be explicit about character count**: "Only ONE [character type] in the scene"
- **Specify environment details**: Floor type, wall color, lighting direction, furniture elements
- **Emphasize natural interaction**: "Character naturally using/enjoying the product"
- **Request professional style**: "Product photography style, commercial quality"

### Video Motion Guidelines
- **Shot vocabulary**: Align prompts with visual hooks, product presence, hero product shots, detail showcase, function expression, and dynamic visuals (see Phase 4 table); sequence beats so the ad reads as product-led, not only ambiance
- **Subtle over dramatic**: Gentle camera movements maintain product focus; avoid a single monotonous move for the entire clip
- **Duration constraint**: Keep videos ≤10 seconds for social media optimization
- **Resolution**: 720p (960x960 or 1280x720) balances quality and file size

### Audio Integration
- **Check directory first**: Use `ls *.mp3` to detect existing audio files
- **Trim to video length**: Use `-t 10` flag to match video duration
- **Quality settings**: AAC codec at 192kbps for good quality/size ratio

---

## Error Handling

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Multiple characters appear in composition | Add explicit constraint in prompt: "ONLY ONE [character], no other characters" |
| Plain white background | Specify environment details: "in a modern living room with wooden floor, beige walls, natural window light" |
| Image file too large | Compress before analysis using provided Python script |
| Audio sync issues | Ensure `-shortest` flag in FFmpeg to trim to shortest stream |
| Video generation timeout | Use background task spawning for long operations |

---

## Generalization Notes

### Adaptability Across Product Categories
This workflow is **product-agnostic** and can be applied to:
- **Pet products**: Use pet characters (cats, dogs, birds)
- **Home goods**: Use lifestyle characters or pure environment scenes
- **Tech gadgets**: Use user personas or hands-on demonstrations
- **Fashion items**: Use model characters in appropriate settings
- **Food products**: Use dining scenes or ingredient close-ups

### Scalability Considerations
- **Batch processing**: Extend workflow to process multiple products in parallel
- **Template system**: Create environment templates for different product categories
- **A/B testing**: Generate multiple composition variants with different environments
- **Localization**: Adjust environment aesthetics for different cultural markets

---

## Example Use Cases

### Use Case 1: Pet Product (With Character Image)
```
Input: cat_tower.jpg, calico_cat.jpg
→ Compose: Cat on tower in cozy living room
→ Video: 10s with gentle camera pan + user's "Cat Republic.mp3"
Output: final_ad_video.mp4
```

### Use Case 2: Furniture (No Character Image)
```
Input: modern_sofa.jpg
→ Generate: Lifestyle character reading on sofa
→ Compose: Character + sofa in bright apartment
→ Video: 10s with AI-generated ambient music
Output: final_ad_video.mp4
```

### Use Case 3: Tech Gadget (No Character, No Audio)
```
Input: wireless_earbuds.jpg
→ Generate: Hands holding earbuds
→ Compose: Hands + earbuds on minimalist desk
→ Video: 10s with AI-generated tech music
Output: final_ad_video.mp4
```

---

## Technical Requirements

### Dependencies
- **Python 3.8+** with PIL/Pillow for image processing
- **FFmpeg** for video/audio merging
- **AI Services**:
  - `media_comprehension` skill for image analysis
  - `image_generator` for composition and character generation
  - `video_diffusion` for video creation

### File Naming Conventions
- Product images: `product_*.jpg/png`
- Character images: `character_*.jpg/png` or descriptive names
- Audio files: `*.mp3`
- Output composition: `composed_ad_image.png`
- Final video: `final_ad_video.mp4` or `[product_name]_ad.mp4`

---

## Conclusion

This workflow provides a **systematic, generalizable approach** to advertisement video creation that:
- ✅ Handles both complete and incomplete asset sets
- ✅ Ensures realistic environment integration
- ✅ Maintains character consistency (single character constraint)
- ✅ Flexibly manages audio from multiple sources
- ✅ Produces professional-quality output suitable for social media and e-commerce platforms

By following these guidelines, future users can efficiently create compelling advertisement videos for diverse product categories without overfitting to specific examples.
