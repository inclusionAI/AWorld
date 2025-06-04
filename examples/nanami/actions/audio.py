from examples.nanami.actions.base import ActionCollection, ActionResponse


class AudioAction(ActionCollection):
    """
    Audio actions.
    """

    async def mcp_play(self, audio_file: str) -> ActionResponse:
        """
        Play an audio file.
        Args:
            audio_file: The audio file to play.
        Returns:
            The response from the action.
        """
        return ActionResponse()
