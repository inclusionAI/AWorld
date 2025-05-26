# Initialize AworldTaskClient with server endpoints
import asyncio
import uuid

from aworld_client import AworldTask, AworldTaskClient

AWORLD_TASK_CLIENT = AworldTaskClient(
    know_hosts=["localhost:9299", "localhost:9399", "localhost:9499"]
)


async def _run_agent_task(agent_name, question_id, question: str) -> None:
    """Run a single Gaia task with the given question ID.

    Args:
        question: The ID of the question to process
    """
    global AWORLD_TASK_CLIENT
    task_id = str(uuid.uuid4())

    # Submit task to Aworld server
    await AWORLD_TASK_CLIENT.submit_task(
        AworldTask(
            task_id=task_id,
            agent_id=agent_name,
            agent_input=question,
            session_id="session_id",
            user_id="SYSTEM"
        )
    )

    # Get and print task result
    task_result = await AWORLD_TASK_CLIENT.get_task_state(task_id=task_id)

    # Write task result to markdown file
    with open(f"task_results/result_{question_id}_{task_id}.md", "w", encoding="utf-8") as f:
        f.write(f"# Task Result for {task_id}\n\n")
        f.write(f"Agent: {agent_name}\n\n")
        f.write(f"Input: {question_id + '_' + question}\n\n")
        f.write("## Result\n\n")
        f.write(str(task_result.data))


if __name__ == '__main__':
    querys = """1	In "The Miracles of the Namiya General Store," among the many consultation letters received by Grandpa Namiya, how many letters provide a detailed description of the writer's family background and specific difficulties? Which letters are they? What family issues and difficulties are mentioned in these letters?"""
    for query in querys.split("\n"):
        question = query.strip().split("\t")
        asyncio.run(_run_agent_task("aworld_agent", question[0], question[1]))
    # Run batch processing for questions 1-5
