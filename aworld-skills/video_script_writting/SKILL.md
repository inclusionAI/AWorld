---
name: ai-video-script-sop-remotion-diffusion
description: >-
  Standard operating procedure for automated AI video production using a Remotion (code) and
  diffusion (model) hybrid pipeline. Covers narrative DNA (hero, show-don’t-tell, three-act arc),
  technical specs (duration, integer segment lengths, resolution, fps, Mandarin pacing),
  tech-selection matrix (diffusion vs code), a five-part diffusion prompt protocol (style,
  micro-timing, entities, camera, transitions), end-to-end execution workflow, and a fixed
  output template (metadata table + per-shot table). Complements create-video and Remotion
  best-practice skills for execution quality.
license: Complete terms in LICENSE.txt
---

## 1. Core narrative rules (narrative DNA)

To keep the video engaging (“satisfaction”), the script should follow:

*   **Single hero**: One core character drives the story through **action** that solves the problem.
*   **Show, don’t tell**: No inner monologue; emphasize **what happens on screen**.
*   **Three-part arc**:
    1.  **Opening (hook)**: A clear, seemingly impossible big task.
    2.  **Middle (grind)**: Dense, fast execution (cathartic, orderly).
    3.  **Ending (payoff)**: A strong visual reward.
*   **Radical brevity**: Voice and subtitles stay **1:1**; lines only **announce** or **briefly react**—let the pictures carry meaning.

## 2. Technical specs and limits

*   **Total length**: $1\ \text{min}$–$3\ \text{min}$.
*   **Segment length**: Must be an **integer** in seconds (e.g. $4.5\text{s} \rightarrow 5\text{s}$). Diffusion clips are capped at **$10\text{s}$** per segment.
*   **Resolution**: $1080\text{p}$ or $720\text{p}$.
*   **Frame rate**: $24\text{fps}$ or $30\text{fps}$.
*   **Mandarin VO baseline**: Plan copy at about **4–5 characters per second**.

## 3. Shot tech-selection matrix

| Need | Recommended tech | Why | Avoid |
| :--- | :--- | :--- | :--- |
| **Photoreal / complex lighting** | **Diffusion (video)** | Texture, mood, physics, transitions. | On-screen **text or charts** in the same shot; don’t mix code and diffusion **in one lens**. |
| **Character close-up / background change** | **Diffusion (I2V)** | Image-to-video keeps continuity. | Control **physical camera motion** strictly. |
| **Cartoon / vector motion** | **Code (SVG/TSX)** | Clean edges, flat look, precise paths. | Hard to express rich texture. |
| **Info / formulas / charts** | **Code (HTML/Remotion)** | Exact typography, math, data. | Don’t use for photoreal landscapes. |

---

## 4. Diffusion prompt protocol

**This is what keeps visuals high quality and coherent.** Every diffusion shot description should combine **five parts**:

$$ \text{Prompt} = \text{[Style anchor]} + \text{[Micro-timeline]} + \text{[Concrete entities]} + \text{[Camera physics]} + \text{[Physical bridge]} $$

### A. Style anchors

*   **Force consistency**: Start every shot with the **same style phrase**, e.g. `【Impressionist oil painting】` or `【Cyberpunk photoreal】`.
*   **Push intensity**: Use extreme wording—reject “fine.”
    *   *Weak:* “sunflowers”
    *   *Strong:* “**Van Gogh sunflowers as extremely thick, rough impasto in blazing yellow**”

### B. Micro-timing

*   **Avoid even mush**: State what happens **each second**.
    *   *Pattern:* `【0–2s】action A, 【2–10s】action B`.

### C. Concrete entities

*   **Make everything physical**: Turn abstractions into **objects**. Models don’t understand metaphor alone.
    *   *Weak:* “falling into despair”
    *   *Strong:* “**the floor collapses underfoot into a bottomless pit of black tar**”

### D. Camera physics

*   **Lock direction**: Say push in, pull back, pan.
*   **Keep inertia**: If the last shot **pushed in**, this shot must **continue** pushing in—random moves cause visual whiplash.

### E. Physical transitions

*   **Input dependency**: Say explicitly: “this shot is generated from the **last frame of the previous shot**.”
*   **No pop in/out**: Nothing vanishes without a process.
    *   *Weak:* “the house disappears”
    *   *Strong:* “**the house crumbles from the roof into golden sand blown away by wind**”

---

## 5. Execution workflow

1.  **Storyboard**: Lock the story, split into $N$ shots.
2.  **Duration math**:
    *   Write lines $\rightarrow$ count characters $\rightarrow$ divide by speech rate ($4.5$) $\rightarrow$ **round up** to duration $T$.
    *   *Check:* $T \le 10\text{s}$ for diffusion segments.
3.  **Continuity**:
    *   For each shot, define **start frame** and **end frame** sources.
    *   *Strategy A (Diff $\rightarrow$ Diff)*: previous **end frame** = next **start frame** (I2V).
    *   *Strategy B (Code $\rightarrow$ Diff)*: last **code frame export** = first **diffusion** frame.
4.  **Asset build**:
    *   Render all **silent** video segments.
    *   Generate matching **TTS** and **SRT**.
    *   **Verify:** $\sum(\text{segment durations}) = \text{total audio duration}$.
5.  **Final mux**: Remotion combines video, audio, and subtitle layers into MP4.

---

## 6. Standard script output template

When writing a script, use this structure.

### Video basics

*   **Theme**: [e.g. a developer sorting a mountain of messy code]
*   **Estimated total length**: $[xx]\ \text{s}$
*   **Resolution**: $1920 \times 1080$ ($1080\text{p}$)
*   **Style keywords**: [e.g. minimal, low-poly, cool palette]

### Shot execution table

| Shot ID | Duration (s) | Technique | Visual & diffusion prompt / code logic | Audio (VO + subtitles) | Transition strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **01** | 5 | **Diffusion** (T2V) | **[Style]** … <br> **[Time]** 【0–2s】… <br> **[Entity]** … <br> **[Camera]** … | “This is everything that piled up this week.” | **Cold open**: text-only generation; no prior frame. |
| **02** | 8 | **Code** (React/SVG) | **UI**: giant red progress bar SVG.<br> **Motion**: numbers jump 0%→99%; warning icon blinks. | “The system is on the edge.” | **Hard cut**: clean code look vs previous chaos. |
| **03** | 6 | **Diffusion** (I2V) | **[Style]** …<br> **[Bridge]** Start from the red warning; red **liquifies** into flowing lava… | “We must cool it down now.” | **I2V**: **Shot 02 last frame** → **Shot 03 first frame**. |
| … | … | … | … | … | … |

---

## Document metadata

| Field | Value |
|-------|-------|
| Source | `script_skill.md` (Chinese) |
| Last updated | 2026-03-30 |
