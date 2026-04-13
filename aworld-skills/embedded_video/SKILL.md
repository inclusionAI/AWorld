---
name: embedded-video-pip-smooth-playback
description: >-
  Prevent stutter and frozen frames when embedding a child video inside a parent in code-driven
  pipelines (Remotion, After Effects scripting, FFmpeg filter graphs). Explains why sparse
  keyframes break frame-accurate seek during per-frame export, and how re-encoding with H.264
  all-intra GOP (-g 1) and yuv420p makes every frame independently decodable. Includes FFmpeg
  command, parameter notes, file-size tradeoffs, and a reusable rule for any seek-heavy
  programmatic video workflow.
license: Complete terms in LICENSE.txt
---

## 1. Problem scenario

When you build videos with code-driven renderers (e.g. Remotion, AE scripts, complex FFmpeg filter graphs), you often need picture-in-picture: one main composition with another video embedded inside it.

**Typical symptom**: In the exported file, motion on the main layer (translation, scale, etc.) looks smooth, but the **embedded clip stutters badly**, drops frames, or even freezes for long stretches.

## 2. Root cause: sparse keyframes

Modern codecs (H.264/H.265) save space by storing full pictures only at scene cuts or every few seconds (**keyframes / I-frames**). Frames in between (**P-frames / B-frames**) only store differences from neighbors.

Engines like Remotion export **frame by frame**. To render frame *N*, the embedded clip must **seek** to the matching timestamp.

If the embedded file has almost no keyframes (e.g. one I-frame at the start of a 10 s clip), the decoder often has to **decode from frame 0** forward to reach frame *N*. That leads to:

1. **Very slow seeks**: Decoding takes so long that the renderer times out and grabs a frame before the decode finishes.
2. **Repeated frames**: The decoder cannot keep up, so several consecutive captures show the same old image—**stutter** in the final output.

## 3. Fix: all-intra encoding (every frame a keyframe)

**Idea**: Re-encode the embedded asset so **every frame is a keyframe**. Then any seek returns a full picture immediately, with no long chains of dependent frames.

### Steps

#### Step 1: Re-encode with FFmpeg

Run:

```bash
ffmpeg -i input.mp4 -c:v libx264 -g 1 -pix_fmt yuv420p output_keyframes.mp4
```

**Parameters**:

| Flag | Meaning |
|------|---------|
| `-i input.mp4` | Source clip you embed. |
| `-c:v libx264` | H.264 for broad compatibility with web and renderers. |
| `-g 1` | **Critical**: GOP size 1 → **one keyframe per frame**. |
| `-pix_fmt yuv420p` | Common 8-bit 4:2:0 layout for players and pipelines. |
| `output_keyframes.mp4` | Output used as the fixed asset. |

*(All-intra files are often **several times larger** than the original. That is usually fine for an **intermediate** asset used only during rendering.)*

#### Step 2: Point your project at the new file

Replace paths so the composition uses `output_keyframes.mp4` instead of the old `input.mp4`.

#### Step 3: Re-render

Export again; embedded playback should track smoothly with the main timeline.

## 4. Rules of thumb

**Rule:** Any asset that must be seeked frame-accurately from code should be pre-converted to **all-intra** (`-g 1`) before use.

This applies broadly in **programmatic video**: PiP, reverse playback, scrubber-driven playback, etc. If embedded video looks choppy, **check keyframe spacing first** and re-encode if needed.

