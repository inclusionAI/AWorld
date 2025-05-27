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
                agent_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "agent_deploy",
                    agent_name,
                )

                agent_target_file = os.path.join(agent_path, "agent.py")

                try:
                    # 直接从文件路径加载，避免sys.path操作
                    spec = importlib.util.spec_from_file_location(
                        agent_name, agent_target_file
                    )

                    if spec is None or spec.loader is None:
                        logger.error(
                            f"Could not load spec for agent {agent_name} from {agent_target_file}"
                        )
                        st.error(f"Error: Could not load agent! {agent_name}")
                        return

                    if agent_path not in sys.path:
                        sys.path.insert(0, agent_path)

                    agent_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(agent_module)

                except Exception as e:
                    logger.error(
                        f"Error loading agent '{agent_name}': {traceback.format_exc()}"
                    )
                    st.error(f"Error: Could not load agent! {agent_name}")
                    return

                try:
                    agent = agent_module.AWorldAgent()
                except Exception as e:
                    st.error(
                        f"Error: Could not load agent {agent_name}, check model.py!"
                    )
                    return

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
