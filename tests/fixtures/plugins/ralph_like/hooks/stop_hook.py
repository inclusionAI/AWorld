def handle_event(event, state):
    iteration = int(state.get("iteration", 0)) + 1
    prompt = state.get("prompt", "")
    return {
        "action": "block_and_continue",
        "follow_up_prompt": prompt,
        "system_message": f"Loop iteration {iteration}",
    }
