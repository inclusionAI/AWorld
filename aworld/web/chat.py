import sys
import streamlit as st
from dotenv import load_dotenv
import logging
import os
import traceback
import importlib.util
import utils
import aworld.trace as trace
from trace_net import generate_trace_graph_full


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

    st.markdown("<style> .stAppHeader { display: none; }</style>", unsafe_allow_html=True)

    query_params = st.query_params
    selected_agent_from_url = query_params.get("agent", None)

    if "selected_agent" not in st.session_state:
        st.session_state.selected_agent = selected_agent_from_url
        logger.info(f"Initialized selected_agent from URL: {selected_agent_from_url}")

    if selected_agent_from_url != st.session_state.selected_agent:
        st.session_state.selected_agent = selected_agent_from_url

    with st.sidebar:
        st.title("AWorld Agents List")
        for agent in utils.list_agents():
            if st.button(agent):
                st.query_params["agent"] = agent
                st.session_state.selected_agent = agent
                logger.info(f"selected_agent={st.session_state.selected_agent}")

    if st.session_state.selected_agent:
        agent_name = st.session_state.selected_agent
        st.title(f"AWorld Agent: {agent_name}")

        if prompt := st.chat_input("Input message here~"):

            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                agent_name = st.session_state.selected_agent
                agent_package_path = utils.get_agent_package_path(agent_name)

                agent_module_file = os.path.join(
                    agent_package_path, "agent.py")

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
                except Exception as e:
                    logger.error(
                        f"Error loading agent {agent_name}, cwd:{os.getcwd()}, sys.path:{sys.path}: {traceback.format_exc()}")
                    st.error(f"Error: Could not load agent! {agent_name}")
                    return

                agent = agent_module.AWorldAgent()

                async def markdown_generator():
                    async with trace.span("start") as span:
                        trace_id = span.get_trace_id()
                        logger.info(f"trace_id={trace_id}")
                        async for line in agent.run(prompt):
                            yield f"\n{line}\n"

                        trace_id = span.get_trace_id()
                        file_name = f"graph.{trace_id}.html"
                        folder_name = "trace_data"
                        generate_trace_graph_full(
                            trace_id, folder_name=folder_name, file_name=file_name
                        )
                        view_page_url = f"/trace?trace_id={trace_id}"
                        yield f"\n---\n[View Trace]({view_page_url})\n"

                st.write_stream(markdown_generator())
    else:
        st.title("AWorld Agent Chat Assistant")
        st.info("Please select an Agent from the left sidebar to start")


try:
    agent_page()
except Exception as e:
    logger.error(f">>> Error: {traceback.format_exc()}")
    st.error(f"Error: {str(e)}")
