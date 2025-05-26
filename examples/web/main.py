import streamlit as st
from dotenv import load_dotenv
import logging
import os
import traceback
import utils
import importlib.util
import inspect

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def agent_page():
    with st.sidebar:
        st.title("Agents List")

        for agent in utils.list_agents():
            if st.button(agent):
                st.session_state.selected_agent = agent
                st.rerun()

    if "selected_agent" not in st.session_state:
        st.session_state.selected_agent = None

    if st.session_state.selected_agent:
        agent_name = st.session_state.selected_agent
        st.title(f"AWorld Agent: {agent_name}")

        if prompt := st.chat_input("Input message here~"):

            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                full_response = ""

                agent_name = st.session_state.selected_agent
                agent_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "agent_deploy",
                    agent_name,
                )
                try:
                    spec = importlib.util.spec_from_file_location(
                        "agent_module", os.path.join(agent_path, "agent.py")
                    )
                    agent_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(agent_module)
                except Exception:
                    logger.error(
                        f"Error importing agent module: {traceback.format_exc()}"
                    )
                    st.error("Error importing agent module")
                    return

                Agent = agent_module.Agent
                agent = Agent()

                async def markdown_generator():
                    async for line in agent.run(prompt):
                        yield f"\n{line}\n"

                st.write_stream(markdown_generator())
    else:
        st.title("AWorld Agent Chat Assistant")
        st.info("Please select an Agent from the left sidebar to start")


try:
    agent_page()
except Exception as e:
    logger.error(f">>> Error: {traceback.format_exc()}")
    st.error(f"Error: {str(e)}")
