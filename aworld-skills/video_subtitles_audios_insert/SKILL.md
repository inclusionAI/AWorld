---
name: video-subtitles-and-audio-insert-workflow
description: >-
  Burn hard subtitles from UTF-8 SRT files using moviepy 2.x with CJK-capable system fonts;
  tune font size, placement, stroke, and encode settings (bitrate or CRF) to avoid oversized
  outputs. Documents ffprobe/ffmpeg workflows for inspection, encoding, and batch jobs;
  troubleshooting for fonts, bitrate, and pacing. Covers voiceover with edge-tts (voice selection,
  rate/volume/pitch), matching narration length to video with atempo/apad, and multi-scene pacing
  with breathing room. Targets moviepy 2.x and Python 3.x on macOS, Linux, and Windows.
license: Complete terms in LICENSE.txt
---

## 1. Choosing a Technical Approach

### Recommended: Python moviepy + CJK fonts
- **Tools**: moviepy 2.x
- **Fonts**: System CJK fonts (e.g. STHeiti, Songti, PingFang)
- **Pros**: Cross-platform, supports Chinese, easy styling control
- **Cons**: Slower processing (~40s for an 80s video)

### Alternative: FFmpeg + libass (requires rebuild)
- **Tools**: FFmpeg with libass support
- **Pros**: Fast processing
- **Cons**: Requires rebuilding FFmpeg; complex setup

---

## 2. Core Code Template

```python
#!/usr/bin/env python3
import re
from moviepy import VideoFileClip, TextClip, CompositeVideoClip

def parse_srt(srt_file):
    """Parse an SRT subtitle file."""
    with open(srt_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    blocks = content.strip().split('\n\n')
    subtitles = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            time_line = lines[1]
            match = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})', time_line)
            if match:
                start_h, start_m, start_s, start_ms, end_h, end_m, end_s, end_ms = match.groups()
                start_time = int(start_h) * 3600 + int(start_m) * 60 + int(start_s) + int(start_ms) / 1000
                end_time = int(end_h) * 3600 + int(end_m) * 60 + int(end_s) + int(end_ms) / 1000
                text = '\n'.join(lines[2:])
                subtitles.append(((start_time, end_time), text))
    
    return subtitles

def make_textclip(txt, font_path, font_size=40):
    """Create a subtitle text clip."""
    return TextClip(
        text=txt,
        font_size=font_size,             # Tune for resolution
        color='white',
        font=font_path,                  # CJK-capable font path
        stroke_color='black',
        stroke_width=2.5,
        method='caption',
        size=(1100, None),               # 1100px width, auto height
        text_align='center'
    )

def add_subtitles(video_path, srt_path, output_path, font_path, font_size=40, bottom_margin=100):
    """Burn hard subtitles into a video."""
    video = VideoFileClip(video_path)
    subtitles = parse_srt(srt_path)
    
    subtitle_clips = []
    for (start, end), text in subtitles:
        txt_clip = make_textclip(text, font_path, font_size)
        txt_clip = txt_clip.with_start(start).with_end(end)
        # Position: pixels from bottom (avoids wrapped lines past the lower edge)
        txt_clip = txt_clip.with_position(('center', video.h - bottom_margin))
        subtitle_clips.append(txt_clip)
    
    final_video = CompositeVideoClip([video] + subtitle_clips)
    
    # Important: cap bitrate to avoid huge files
    # Prefer checking source bitrate first, then ~1.2–1.5× that value
    final_video.write_videofile(
        output_path,
        codec='libx264',
        audio_codec='aac',
        fps=video.fps,
        preset='medium',
        bitrate='600k',      # Tune to source (often 400–800k)
        threads=4
    )
    
    video.close()

# Example usage
if __name__ == '__main__':
    add_subtitles(
        video_path='input_video.mp4',
        srt_path='subtitles.srt',
        output_path='output_video_with_subtitles.mp4',
        font_path='/System/Library/Fonts/STHeiti Medium.ttc',  # macOS
        font_size=40,        # e.g. 40px for 1280×720
        bottom_margin=100    # 100px from bottom
    )
```

---

## 3. Key Parameter Settings

### 3.1 Font choice (critical)
```python
# macOS
font_path = '/System/Library/Fonts/STHeiti Medium.ttc'  # STHeiti (recommended)
# or
font_path = '/System/Library/Fonts/Supplemental/Songti.ttc'  # Songti

# Linux
font_path = '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'  # WenQuanYi Micro Hei

# Windows
font_path = 'C:/Windows/Fonts/msyh.ttc'  # Microsoft YaHei
```

**Note**: You must use a font that includes the glyphs you need (e.g. Chinese); otherwise subtitles show as boxes.

### 3.2 Font size by resolution
| Resolution | Recommended size | Notes |
|------------|------------------|-------|
| 1280×720   | 40px             | HD |
| 1920×1080  | 60px             | Full HD |
| 3840×2160  | 120px            | 4K |

### 3.3 Position
```python
# Pixels from bottom ≈ font_size × 2.5
bottom_margin = font_size * 2.5

# Example: 40px font
bottom_margin = 100  # 100px from bottom

# Y position
position_y = video.h - bottom_margin
```

### 3.4 Bitrate (avoid oversized files)
```python
# Step 1: inspect source bitrate
# ffprobe -v error -show_entries format=bit_rate input.mp4

# Step 2: set output bitrate (often 1.2–1.5× source)
# Example:
# source ≈ 444 kbps → output ≈ 600 kbps (~1.35×)

bitrate='600k'
```

---

## 4. Common Issues and Fixes

### Issue 1: Subtitles show as boxes
**Cause**: Font lacks the needed glyphs (e.g. using Arial or Times New Roman for Chinese).  
**Fix**: Use a CJK-capable font (STHeiti, Songti, Microsoft YaHei, etc.).

### Issue 2: Output file size explodes
**Cause**: Bitrate set too high (e.g. 5000 kbps).  
**Fix**:
```python
# Check source bitrate
ffprobe -v error -show_entries format=bit_rate input.mp4

# Set a sensible bitrate (~1.2–1.5× source)
bitrate='600k'  # if source was ~444 kbps
```

### Issue 3: Wrapped lines extend past the bottom
**Cause**: Font too large or position too low.  
**Fix**:
- Reduce font size (e.g. 48px → 40px)
- Raise position (e.g. 80px → 100px from bottom)
- Use: `bottom_margin = font_size * 2.5`

### Issue 4: Subtitles look faint or unclear
**Cause**: Stroke too thin or poor contrast.  
**Fix**:
```python
color='white',
stroke_color='black',
stroke_width=2.5   # often 2–3px works well
```

---

## 5. End-to-End Workflow

### Step 1: Prepare the subtitle file
```bash
# Ensure UTF-8 SRT
file -I subtitles.srt
# Should include: charset=utf-8

# If wrong encoding, convert
iconv -f GBK -t UTF-8 subtitles_gbk.srt > subtitles_utf8.srt
```

### Step 2: Inspect the source video
```bash
# Resolution
ffprobe -v error -show_entries stream=width,height input.mp4

# Bitrate
ffprobe -v error -show_entries format=bit_rate input.mp4
```

### Step 3: Tune parameters from video metadata
```python
# Font size from resolution
if width == 1280 and height == 720:
    font_size = 40
elif width == 1920 and height == 1080:
    font_size = 60
elif width == 3840 and height == 2160:
    font_size = 120

# Bitrate from source
new_bitrate = f"{int(original_bitrate * 1.35 / 1000)}k"
```

### Step 4: Run the burn-in script
```bash
python3 add_subtitles.py
```

### Step 5: Validate output
```bash
ls -lh output_video_with_subtitles.mp4

ffmpeg -i output_video_with_subtitles.mp4 -ss 00:00:10 -vframes 1 test_frame.png

open output_video_with_subtitles.mp4   # macOS
# or
vlc output_video_with_subtitles.mp4    # Linux
# or
start output_video_with_subtitles.mp4  # Windows
```

---

## 6. Performance Tips

### 6.1 Speed
```python
threads=4  # tune to CPU cores (often 2–8)

preset='fast'  # ultrafast, superfast, veryfast, fast, medium, slow — faster = larger files
```

### 6.2 Smaller files
```python
bitrate='500k'
preset='slow'   # better compression, slower encode
```

### 6.3 Quality (CRF)
```python
# Do not combine bitrate and CRF in one pass without knowing the interaction
final_video.write_videofile(
    output_path,
    codec='libx264',
    preset='medium',
    # bitrate='600k',
    ffmpeg_params=['-crf', '23']  # 18–28; lower = higher quality; 23 is a common default
)
```

---

## 7. Quick Checklist

Before burning subtitles:

- [ ] SRT file is UTF-8
- [ ] Font covers all characters in the script
- [ ] Font size matches resolution (e.g. 40px @ 720p, 60px @ 1080p)
- [ ] Bottom margin ≥ ~100px or use `font_size * 2.5`
- [ ] Bitrate ~1.2–1.5× source (or CRF chosen deliberately)
- [ ] Stroke 2–3px with strong contrast
- [ ] Spot-check a frame or short segment

---

## 8. Reference Commands

### List CJK-capable fonts
```bash
# macOS
ls /System/Library/Fonts/*.ttc | grep -i "hei\|song"

# Linux
fc-list :lang=zh

# Windows
dir C:\Windows\Fonts\*.ttc
```

### Video info
```bash
ffprobe input.mp4

ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 input.mp4

ffprobe -v error -show_entries format=bit_rate -of default=noprint_wrappers=1:nokey=1 input.mp4

ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 input.mp4
```

### Subtitle format / encoding
```bash
ffmpeg -i subtitles.srt subtitles.ass

iconv -f GBK -t UTF-8 subtitles_gbk.srt > subtitles_utf8.srt

file -I subtitles.srt
```

### Batch processing
```bash
for video in *.mp4; do
    python3 add_subtitles.py "$video" "${video%.mp4}.srt" "output_${video}"
done
```

---

## 9. Troubleshooting

### moviepy import fails
```bash
python3 -c "import moviepy; print(moviepy.__version__)"

pip3 install moviepy
pip3 install --upgrade moviepy
```

### Font path not found
```bash
python3 -c "from PIL import ImageFont; ImageFont.truetype('/path/to/font.ttc', 40)"

ls -la /path/to/font.ttc
```

### Encode appears stuck
```bash
top   # or htop

# Lower threads
threads=2

preset='ultrafast'
```

---

## 10. Best Practices Summary

### Suggested defaults (1280×720)
```python
add_subtitles(
    video_path='input.mp4',
    srt_path='subtitles.srt',
    output_path='output_with_subtitles.mp4',
    font_path='/System/Library/Fonts/STHeiti Medium.ttc',  # adjust per OS
    font_size=40,
    bottom_margin=100
)

# In write_videofile
bitrate='600k',
preset='medium',
threads=4
```

### Quality vs size
- **Quality first**: `preset='slow'`, `bitrate='800k'` or `crf=20`
- **Balanced**: `preset='medium'`, `bitrate='600k'` or `crf=23` (common default)
- **Speed first**: `preset='fast'`, `bitrate='500k'` or `crf=26`

### Use cases
- **Social sharing**: lower bitrate (400–500k) for smaller files
- **Professional**: CRF (e.g. 20–23)
- **Quick preview**: `ultrafast` preset

---

## 11. Voiceover with edge-tts

### 11.1 Approach
- **Tool**: `edge-tts` (free TTS via Microsoft Edge voices)
- **Pros**: No API key, natural voices, many locales and styles
- **Install**: `pip install edge-tts`

### 11.2 Example Chinese voices
| Voice ID | Gender | Style / use case | Notes |
|----------|--------|------------------|-------|
| `zh-CN-YunxiNeural` | Male | Storytelling, explainers, short video | Very natural, bright |
| `zh-CN-YunjianNeural` | Male | Sports, explainers, fast pace | Energetic, punchy |
| `zh-CN-YunyangNeural` | Male | News, professional | Mature, steady |
| `zh-CN-XiaoxiaoNeural` | Female | News, fiction, general | Warm, natural |

### 11.3 Minimal Python example
```python
import asyncio
import edge_tts

async def generate_audio(text, output_file):
    voice = "zh-CN-YunjianNeural"

    # rate: e.g. "-5%" slower, "+10%" faster
    # volume: e.g. "+50%"
    # pitch: e.g. "-5Hz"
    communicate = edge_tts.Communicate(text, voice, rate="-5%")

    await communicate.save(output_file)

# asyncio.run(generate_audio("Hello, world!", "output.mp3"))
```

### 11.4 Matching audio length to video (FFmpeg)
For storyboards, you often need narration to match a fixed clip duration:

```python
import subprocess

def adjust_audio_duration(input_file, target_duration, output_file):
    cmd_duration = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', input_file
    ]
    result = subprocess.run(cmd_duration, stdout=subprocess.PIPE, text=True)
    current_duration = float(result.stdout.strip())
    
    if current_duration > target_duration:
        tempo = current_duration / target_duration
        # atempo is limited to 0.5–2.0 per filter; chain if needed
        tempo_filter = f"atempo={tempo}" if tempo <= 2.0 else f"atempo=2.0,atempo={tempo/2.0}"
        
        cmd = ['ffmpeg', '-y', '-i', input_file, '-filter:a', tempo_filter, output_file]
    else:
        pad_duration = target_duration - current_duration
        cmd = ['ffmpeg', '-y', '-i', input_file, '-filter_complex', f'apad=pad_dur={pad_duration}', output_file]
        
    subprocess.run(cmd)
```

### 11.5 Smoother multi-scene narration
Stretching every clip to exactly match each shot can make pacing uneven and feel breathless.

**Practices**:
1. **Breathing room**: When using `atempo`, target something like `shot_duration - 0.2s` (tune to taste).
2. **Pad with silence**: Use `apad` at the end to reach the exact total duration per shot.

**Sketch**:
```python
actual_target = target_duration - 0.2

if current_duration > actual_target:
    tempo = current_duration / actual_target
    tempo_filter = f"atempo={tempo}" if tempo <= 2.0 else f"atempo=2.0,atempo={tempo/2.0}"
    # run ffmpeg atempo...
else:
    pass

# Then apad to target_duration
```

This keeps overall pacing more even and adds natural pauses between scenes.

---

## Document metadata

| Field | Value |
|-------|-------|
| Last updated | 2026-03-16 |
| Targets | moviepy 2.x, Python 3.x |
| Tested on | macOS, Linux, Windows; multiple resolutions |
