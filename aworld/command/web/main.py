import streamlit as st
from dotenv import load_dotenv
import logging
import os

load_dotenv(os.path.join(os.getcwd(), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

chat = st.Page("chat.py", title="Chat", icon=":material/message:")
trace = st.Page("trace.py", title="Trace")

pg = st.navigation([chat, trace], position="hidden")
pg.run()
