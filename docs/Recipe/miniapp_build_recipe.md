# App Generation Recipe

Generate, evaluate, and self-evolve a complete mini-application from a single text prompt. The AWorld Agent orchestrates the full flow: **automatic creation → automatic evaluation → automatic optimization**. No manual intervention is required once you submit your prompt.

## What You Get

- A runnable mini-application (e.g. web app) generated from your description
- Automatic evaluation and self-evolution until your quality criteria are met
- One command from prompt to product; the entire loop runs autonomously

## Example: English Word Learning App

This recipe reproduces the example from the [README](../../README.md) table: an English word learning app with a UI quality target.

### User Prompt

Use this prompt (or adapt the goal and score threshold to your needs):

```text
help me create an English word learning app, with a UI quality score over 0.9
```

### How Quality Is Measured: Evaluator & Official Skill

The **Evaluator** agent scores the generated app using objective criteria defined by a **Skill**. For this recipe we use the official AWorld Skill:

- **Skill**: [app_evaluator](../../aworld-skills/app_evaluator) (aworld-skills/app_evaluator)

If you have already installed AWorld and AWorld-CLI, the Evaluator **loads this official skill by default**. You do not need to install or configure the skill separately for this flow.

### What Happens (Fully Automated)

1. **Create** — The Developer agent turns your prompt into a runnable app (e.g. HTML/JS).
2. **Evaluate** — The Evaluator runs the app_evaluator skill (e.g. UI quality score).
3. **Optimize** — If the score is below your target (e.g. 0.9), AWorld instructs the Developer to fix issues; the loop repeats until the criteria are met.

All steps run automatically; you only need to start the CLI and submit the prompt.

## How to Run

1. **Install and configure AWorld-CLI**  
   See [Your Journey with AWorld-CLI](../../README.md#your-journey-with-aworld-cli) in the main README:
   - Clone AWorld, create conda env (Python 3.11), install AWorld and aworld-cli.
   - Run `aworld-cli --config` in your working directory (or use a [.env file](../../README_env_config.md)).

2. **Launch the CLI**  
   In your working directory, run:
   ```bash
   aworld-cli
   ```

3. **Enter the app-creation flow**  
   Choose or enter the flow that creates an app from a prompt.

4. **Submit the prompt**  
   Paste or type:
   ```text
   help me create an English word learning app, with a UI quality score over 0.9
   ```

5. **Let it run**  
   The agent will generate the app, evaluate it with the official Evaluator skill, and iterate until the UI quality score meets your target. When done, run or deploy the output as needed.

For installation and activation details, see the main [README](../../README.md).
