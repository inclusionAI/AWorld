---
name: video-storytelling-core-principles
description: >-
  Core storytelling rules for AI video scripts: concrete metaphors instead of abstract jargon,
  the mute test (story reads without audio), visual contrast and closure, physically visible causes
  of failure or success, visualizing the “eureka” beat, camera motion tied to physics, in-scene
  transitions instead of black cuts, character consistency and multi-speaker action/lip sync
  timelines, and three-act pacing with Mandarin VO speed (~4.5 chars/s) and breathing room for
  action and SFX.
license: Complete terms in LICENSE.txt
---

## 1. The “smoke and fire” rule (make it concrete)

*   **Reject vague grandeur**: Do **not** build visuals around abstract buzzwords like “system,” “architecture,” “underlying logic,” or “empowerment.”
*   **Use lived-in detail**: Map ideas to warm, everyday scenes—e.g. multi-agent teamwork as **tiny kitchen sprites dividing cake work**; single-model limits as **one person juggling chores until everything breaks**.
*   **Stress texture**: Prompts must highlight **physical material** (flour dust, cream sheen, strawberry color, oven glow) for “food appeal” or **satisfying, tactile** life moments.

## 2. The “mute” test

*   **Picture = story**: If you **mute** narration and dialogue, the audience should still follow setup, turn, and payoff. Voiceover **annotates** the image—images must not become a **slide deck for the VO**.
*   **Visual loop and contrast**:
  *   Problem and solution should read through **visual contrast**, not explanation-only VO.
  *   **Weak**: Scene 1 VO “solo is exhausting,” girl sighs; Scene 4 VO “teamwork is easy,” girl smiles—same vague staging.
  *   **Strong**: Scene 1—girl whisks with one hand and struggles to pour flour with the other → **flour explosion** (solo pain). Scene 4—friend takes whisking; girl can **sift flour calmly** (team gain). **Action contrast** closes the loop.

## 3. Physical logic and action breakdown

*   **Visible failure and success**:
  *   Conflict and outcome cannot be a vague label—they must split into **visible physical causes**.
  *   **Weak**: “She failed at the cake and got flour on her face.” (Why the face?)
  *   **Strong**: “Left hand whisks, right hand strains holding the flour bag, recipe in teeth. A sneeze drops the paper; hands slip; the bag tips and **flour blasts upward into her face**.” Tight chain, self-consistent motion.
*   **Visual bridge for the “aha”**:
  *   You cannot jump from “stuck” to “solution” without a **seen** link.
  *   If inspiration comes from watching something (e.g. ants), show **face change** (eyes widen, smile) and **physical action** (grabs a crayon)—then the next beat (drawing a plan) feels earned.

## 4. Camera narrative and transitions

*   **Motion serves physics**:
  *   Push, pull, pan, tilt must have a **story reason**—not motion for its own sake.
  *   Example: for a **sudden flour burst** hitting the face, keep the camera **locked (wide)** so flour **flies toward lens**—don’t chase the particles with the camera and dilute impact.
*   **In-scene transitions (no arbitrary black)**:
  *   Avoid lazy black frames or random hard cuts. Bridge shots with **moving or glowing elements already in frame**.
  *   **Example A (occlusion)**: A flour speck flies straight at a **fixed** camera, whites out the frame; white clears into a **micro world** shot.
  *   **Example B (light)**: An icon on paper **glows**; glow expands to fill screen; fade reveals a real kitchen counter.

## 5. Character consistency and sync

*   **Start/end consistency**: In diffusion video prompts, require **stable identity**—same character, outfit, and general framing **unless** a deliberate occlusion or scene reset (e.g. explicit style line: “same person, same clothes, same hairstyle as previous shot”).
*   **Multi-character action sync**: When several characters speak, the script must include a **beat timeline** (e.g. `[0–2s] A speaks`, `[2–5s] B speaks`) and specify **gesture size** and **mouth movement** in each window so picture matches dubbing.

## 6. Structure and pacing

*   **Compact three acts** (example for ~60s):
  *   **Opening (0–10s)**: Pain point—impossible task or chaos.
  *   **Middle (10–40s)**: Turning point (observe nature, code sketch, etc.)—dense execution and teamwork.
  *   **Ending (40–60s)**: Payoff—result, catharsis, theme lift.
*   **VO speed and breathing room**: Keep Mandarin VO at or below **~4.5 characters per second**. **Cut copy** before you cram frames—leave time for **physical acting**, **SFX**, and the viewer’s eye to rest.

---

## Document metadata

| Field | Value |
|-------|-------|
| Source | `story_skill.md` (Chinese); section 5 completed where the original had placeholders |
| Last updated | 2026-03-30 |
