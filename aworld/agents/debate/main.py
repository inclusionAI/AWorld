import logging
from typing import Optional, AsyncGenerator, Any, Coroutine

from aworld.agents.debate.base import DebateSpeech
from aworld.agents.debate.debate_agent import DebateAgent
from aworld.core.agent.base import BaseAgent
from aworld.core.common import Observation
from aworld.memory.base import MemoryItem
from aworld.memory.main import Memory
from aworld.output import Output, WorkSpace
from aworld.output.base import OutputPart


class DebateArena:
    """
    DebateArena is platform for debate
    """

    affirmative_speaker: DebateAgent
    negative_speaker: DebateAgent

    moderator: Optional[BaseAgent]
    judges: Optional[BaseAgent]


    speeches: list[DebateSpeech]

    display_panel: str

    def __init__(self,
                 affirmative_speaker: DebateAgent,
                 negative_speaker: DebateAgent,
                 moderator: BaseAgent,
                 workspace: WorkSpace,
                 **kwargs
                 ):
        self.affirmative_speaker = affirmative_speaker
        self.negative_speaker = negative_speaker
        self.memory = Memory.from_config(config={
            "memory_store": "inmemory"
        })
        self.speeches=[]
        self.workspace = workspace
        self.affirmative_speaker.set_workspace(workspace)
        self.negative_speaker.set_workspace(workspace)
        self.moderator = moderator
        # Event.register("topic", func= );

    async def async_run(self, topic: str, rounds: int)\
            -> AsyncGenerator[Output, None]:

        """
        Start the debate
        1. debate will start from round 1
        2. each round will have two speeches, one from affirmative_speaker and one from negative_speaker
        3. after all rounds finished, the debate will end

        Args:
            topic: str -> topic of the debate
            affirmative_opinion: str -> affirmative speaker's opinion
            negative_opinion: str -> negative speaker's opinion
            rounds: int -> number of rounds

        Returns: list[DebateSpeech]

        """

        ## 1. generate opinions
        moderator_speech = await self.moderator_speech(topic)
        if not moderator_speech:
            return
        yield moderator_speech
        await moderator_speech.wait_until_finished()
        self.store_speech(moderator_speech)


        affirmative_opinion = moderator_speech.metadata["affirmative_opinion"]
        negative_opinion = moderator_speech.metadata["negative_opinion"]

        logging.info(f"✈️==================================== opinions =============================================")
        logging.info(f"topic: {topic}")
        logging.info(f"affirmative_opinion: {affirmative_opinion}")
        logging.info(f"negative_opinion: {negative_opinion}")
        logging.info(f"✈️==================================== start... =============================================")


        ## 2. Alternating speeches
        for i in range(1, rounds+1):
            logging.info(f"✈️==================================== round#{i} start =============================================")

            # affirmative_speech
            speech = await self.affirmative_speech(i, topic, affirmative_opinion, negative_opinion)
            yield speech
            await speech.wait_until_finished()
            self.store_speech(speech)

            # negative_speech
            speech = await self.negative_speech(i, topic, negative_opinion, affirmative_opinion)
            yield speech
            await speech.wait_until_finished()
            self.store_speech(speech)

            logging.info(f"🛬==================================== round#{i} end =============================================")

        ## 3. Summary speeches

    async def moderator_speech(self, topic) -> DebateSpeech | None:
        results = await self.moderator.async_policy(Observation(content=topic))
        if not results or not results[0] or not results[0].policy_info:
            return None
        return results[0].policy_info

    async def affirmative_speech(self, round: int, topic: str, opinion: str, oppose_opinion: str) -> DebateSpeech:
        """
        affirmative_speaker will start speech
        """

        affirmative_speaker = self.get_affirmative_speaker()

        logging.info(affirmative_speaker.name() + ": " + "start")

        speech = await affirmative_speaker.speech(topic, opinion, oppose_opinion, round, self.speeches)

        logging.info(affirmative_speaker.name() + ":  result: " + speech.content)
        return speech

    async def negative_speech(self, round: int, topic: str, opinion: str, oppose_opinion: str) -> DebateSpeech:
        """
        after affirmative_speaker finished speech, negative_speaker will start speech
        """

        negative_speaker = self.get_negative_speaker()

        logging.info(negative_speaker.name() + ": " + "start")

        speech = await negative_speaker.speech(topic, opinion, oppose_opinion, round, self.speeches)

        logging.info(negative_speaker.name() + ":  result: " + speech.content)
        return speech


    def get_affirmative_speaker(self) -> DebateAgent:
        """
        return the affirmative speaker
        """
        return self.affirmative_speaker

    def get_negative_speaker(self) -> DebateAgent:
        """
        return the negative speaker
        """
        return self.negative_speaker

    def store_speech(self, speech: DebateSpeech):
        self.memory.add(MemoryItem.from_dict({
            "content": speech.content,
            "metadata": {
                "round": speech.round,
                "speaker": speech.name,
                "type": speech.type
            }
        }))
        self.speeches.append(speech)

    def gen_closing_statement(self):
        pass


