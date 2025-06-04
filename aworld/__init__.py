# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
try:
    import aworld.tools
except:
    pass
from aworld.utils import import_package
import_package("dotenv", install_name="python-dotenv")

from dotenv import load_dotenv

sucess = load_dotenv()
if not sucess:
    load_dotenv(os.path.join(os.getcwd(), '.env'))
