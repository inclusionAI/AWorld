# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.utils import import_package

import_package("dotenv", install_name="python-dotenv")
from dotenv import load_dotenv

load_dotenv()

__version__ = '0.1.0'
