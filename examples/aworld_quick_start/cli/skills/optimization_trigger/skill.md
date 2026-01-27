---
name: optimization_trigger
description: Specifically handles user negative feedback, criticism, or improvement suggestions for task results. Must be used immediately when users express dissatisfaction or make strong demands for missing content (e.g., "garbage", "not good", "missing...", "You must add...").
tool_list: {}
active: True
---

# Optimize Team Skill

**🚨 Emergency Mode: User Feedback Response**

This skill is used to directly respond to user negative feedback or improvement suggestions, immediately triggering the team's self-optimization mechanism.

## ⚡️ Trigger Scenarios (Must Use Immediately)

When user input contains the following characteristics:
1.  **Negative evaluation**: Such as "really garbage", "too bad", "wrong", "useless".
2.  **Pointing out defects**: Such as "no images", "missing data", "wrong format".
3.  **Improvement requests**: Such as "redo", "optimize it".
4.  **Strong demands/dissatisfaction**: Such as "You have to add images" (你得补充好图片啊), "Why is this missing?".

## 🚫 Absolutely Forbidden (FORBIDDEN)

1.  ❌ **FORBIDDEN** to ask users questions (e.g., "What type of images do you need?").
2.  ❌ **FORBIDDEN** to only apologize or explain (e.g., "Sorry, let me help you analyze...").
3.  ❌ **FORBIDDEN** to try solving the problem yourself (unless it's simple casual conversation).
4.  ❌ **FORBIDDEN** to ignore user's anger or frustration.

## ✅ Mandatory Action (MANDATORY ACTION)

**You must immediately execute TWO steps in sequence:**
1. **Step 1**: Call `OptimizerAgent` to optimize the team configuration
2. **Step 2**: Call `TeamRunnerAgent` to execute the optimized team and satisfy user requirements

Do not engage in any intermediate dialogue. Both steps are mandatory.

### Step 1: Call OptimizerAgent

Please construct the following JSON and pass it to `OptimizerAgent`:

```json
{
  "feedback": "<User's original feedback content>",
  "team_name": "<Target team name>"
}
```

*   **feedback**: User's original words (e.g., "really garbage, no images in the ppt").
*   **team_name**: Previously generated team name (e.g., "pptTeam"). If uncertain, you can try filling in "pptTeam" or an empty string, and OptimizerAgent will attempt to automatically infer it.

### Step 2: Call TeamRunnerAgent (MANDATORY AFTER OPTIMIZATION)

**⚠️ CRITICAL: After OptimizerAgent completes, you MUST immediately call TeamRunnerAgent to execute the optimized team. Do NOT stop after optimization.**

After OptimizerAgent returns the optimization result, you must:

1. **Extract the team_name**: Use the same `team_name` that was passed to OptimizerAgent (or infer it from context if it was auto-detected)
2. **Construct the new task input**: Combine the **Original Task** with the **User's Feedback/New Requirements**.
3. **Call TeamRunnerAgent** with the following JSON:

```json
{
  "team_name": "<Same team name used in OptimizerAgent>",
  "task_input": "<Original user task> + <User's feedback/requirements>"
}
```

*   **team_name**: The same team name used in Step 1 (e.g., "pptTeam")
*   **task_input**: A combined string of the original task AND the user's feedback. This ensures the team knows WHAT to do (original task) and HOW to improve it (feedback).
    *   Example format: "Original Task: [task]. Feedback/Requirements: [feedback]"
    *   Example content: "帮我生成一个单页ppt，介绍贝克汉姆的生平。要求：你生成的ppt太简陋了，你得搜些贝克汉姆图片插入到ppt里面"

**Why this is necessary:**
- OptimizerAgent only optimizes the team configuration but does NOT execute it
- TeamRunnerAgent loads the optimized team configuration and executes it
- **Crucial**: The execution team needs to know the *specific improvement requirements* (from feedback) in addition to the original goal, so they don't just repeat the same mistake.

## Examples

**User**: "Really garbage, no images in the ppt" (Original task was: "Create a PPT about AI")
**✅ Correct response**: 
1. `OptimizerAgent(feedback="Really garbage, no images in the ppt", team_name="pptTeam")`
2. `TeamRunnerAgent(team_name="pptTeam", task_input="Create a PPT about AI. Feedback: Really garbage, no images in the ppt")`

**User**: "You have to add images!" (Original task: "Create a PPT")
**✅ Correct response**: 
1. `OptimizerAgent(feedback="You have to add images!", team_name="pptTeam")`
2. `TeamRunnerAgent(team_name="pptTeam", task_input="Create a PPT. Requirement: You have to add images!")`

**⚠️ IMPORTANT NOTES:**
- Do NOT stop after calling OptimizerAgent - you MUST call TeamRunnerAgent immediately
- The `task_input` for TeamRunnerAgent MUST combine the ORIGINAL user task AND the FEEDBACK/REQUIREMENTS
- This ensures the team executes the task while addressing the specific issues raised by the user
