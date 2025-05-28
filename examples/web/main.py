import sys
import streamlit as st
from dotenv import load_dotenv
import logging
import os
import traceback
import utils
import importlib.util

load_dotenv(os.path.join(os.getcwd(), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.getcwd())

def agent_page():
    st.set_page_config(
        page_title="AWorld Agent",
        page_icon=":robot_face:",
        layout="wide",
    )

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
                agent_name = st.session_state.selected_agent
                agent_package_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "agent_deploy",
                    agent_name,
                )

                agent_module_file = os.path.join(agent_package_path, "agent.py")

                try:
                    spec = importlib.util.spec_from_file_location(
                        agent_name, agent_module_file
                    )

                    if spec is None or spec.loader is None:
                        logger.error(
                            f"Could not load spec for agent {agent_name} from {agent_module_file}"
                        )
                        st.error(f"Error: Could not load agent! {agent_name}")
                        return

                    agent_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(agent_module)
                except ModuleNotFoundError as e:
                    logger.error(f"Error loading agent {agent_name}, cwd:{os.getcwd()}, sys.path:{sys.path}: {traceback.format_exc()}")
                    st.error(f"Error: Could not load agent! {agent_name}")
                    return

                except Exception as e:
                    logger.error(
                        f"Error loading agent '{agent_name}': {traceback.format_exc()}"
                    )
                    st.error(f"Error: Could not load agent! {agent_name}")
                    return

                agent = agent_module.AWorldAgent()

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
