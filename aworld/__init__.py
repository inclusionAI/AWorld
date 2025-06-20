# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os

from dotenv import load_dotenv

sucess = load_dotenv()
if not sucess:
    load_dotenv(os.path.join(os.getcwd(), '.env'))
