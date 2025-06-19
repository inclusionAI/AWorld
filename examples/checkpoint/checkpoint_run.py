##
from aworld.checkpoint import CheckpointMetadata, create_checkpoint
from aworld.checkpoint.inmemory import InMemoryCheckpointRepository

if __name__ == "__main__":
    repo = InMemoryCheckpointRepository()

    # create a checkpoint
    checkpoint = create_checkpoint(
        metadata=CheckpointMetadata(
            session_id="1",
            task_id="1",
        ),
        values={
            "key": "value",
        }
    )

    repo.put(checkpoint)

    cp = repo.get_by_session("1")

    repo.delete_by_session("1")

    print(repo)
