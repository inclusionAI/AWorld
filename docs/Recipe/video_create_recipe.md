# Video Generation Recipe

Create and refine a short video from your prompt and the materials in your current directory. The AWorld Agent **automatically** generates a storyboard and breakdown, then produces the video using a Remotion-based Skill. You then provide **human feedback**, and the Developer continues to optimize until you are satisfied.

## What You Get

- A storyboard and a short breakdown with detailed requirements for each segment
- A generated video that captures each breakdown’s SVG or HTML frame-by-frame and integrates them into a motion storyboard
- Iterative refinement driven by your feedback (human evaluation)

## Example: Video Introducing Your Topic

This recipe reproduces the [README](../../README.md) “Create Video” example: auto-creation by the Remotion Skill, with human evaluation and follow-up optimization.

### User Prompt

Use this prompt (replace the italic part with your topic or project name):

```text
help me create a video introducing ...., based on the current available materials in the current directory, which means you need to create a storyboard and the corresponding short breakdown, with detailed requirements for each breakdown
```

Replace `....` with your subject (e.g. “our product” or “this tutorial”). The agent will use the files in your **current directory** as the basis for the storyboard and per-breakdown requirements.

### How the Video Is Built: Developer & Remotion Skill

The **Developer** agent creates the storyboard and breakdown, then produces the video using a third-party Skill:

- **Skill**: [Remotion](https://www.skillhub.club/skills/remotion-dev-remotion-remotion) — used to capture each breakdown’s SVG or HTML **frame by frame** and integrate them into the motion storyboard.

**Installing the Skill (recommended)**  
This Skill is not bundled by default. To use it:

1. Download the Remotion Skill from the link above.
2. Place it in your personal skills directory:  
   `~/.aworld/skills`  
   (on macOS/Linux, e.g. `/Users/username/.aworld/skills`).

Once AWorld and AWorld-CLI are installed, the **Developer** automatically loads skills from this user directory. No extra configuration is needed beyond putting the skill in `~/.aworld/skills`.

### What Happens (Auto-Create → Human Feedback → Optimize)

1. **Auto-create** — The Developer generates a storyboard and a short breakdown with detailed requirements for each segment, then uses the Remotion Skill to capture each breakdown’s SVG/HTML frame-by-frame and assemble the video.
2. **Human evaluation** — You watch the result and give feedback (e.g. “shorten scene 2”, “change the transition”).
3. **Optimize** — You send your feedback through the CLI; the Developer continues to refine the video based on your input. Repeat until you are satisfied.

So: creation is automatic; evaluation and direction are **human-in-the-loop**.

## How to Run

1. **Install and configure AWorld-CLI**  
   See [Your Journey with AWorld-CLI](../../README.md#your-journey-with-aworld-cli) in the main README (install AWorld and aworld-cli, then run `aworld-cli --config` or use a [.env file](../../README_env_config.md)).

2. **Install the Remotion Skill (for this recipe)**  
   Download the [Remotion Skill](https://www.skillhub.club/skills/remotion-dev-remotion-remotion) and place it under `~/.aworld/skills` (e.g. `/Users/username/.aworld/skills`).

3. **Prepare your materials**  
   Put the assets you want to use (images, SVGs, HTML, etc.) in a directory and `cd` into it.

4. **Launch the CLI**  
   ```bash
   aworld-cli
   ```

5. **Enter the video-creation flow**  
   Choose or enter the flow that creates a video from a prompt.

6. **Submit the prompt**  
   Paste or type (replace `....` with your topic):
   ```text
   help me create a video introducing ...., based on the current available materials in the current directory, which means you need to create a storyboard and the corresponding short breakdown, with detailed requirements for each breakdown
   ```

7. **Review and give feedback**  
   After the agent generates the storyboard, breakdown, and video, review the result and send your feedback. The Developer will keep optimizing based on your input.

For installation and activation details, see the main [README](../../README.md).
