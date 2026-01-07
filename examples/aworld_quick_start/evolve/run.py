# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio

from dotenv import load_dotenv

from aworld.runner import Runners

load_dotenv()


async def main():
    # must set llm env vars
    await Runners.evolve(task='我想做模型训练')


if __name__ == '__main__':
    asyncio.run(main())
