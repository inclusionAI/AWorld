import streamlit as st
from dotenv import load_dotenv
import logging
import os
import traceback
import utils
import importlib.util
import inspect
import aworld.trace as trace
from trace_net import generate_trace_graph

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def agent_page(trace_id):

    with st.sidebar:
        agent_list_tab, trace_tab = st.tabs(
            [
                "Agents List",
                "Trace"
            ]
        )
        with agent_list_tab:
            st.title("Agents List")
            for agent in utils.list_agents():
                if st.button(agent):
                    st.session_state.selected_agent = agent
                    st.rerun()

        with trace_tab:
            st.title("Trace")
            generate_trace_graph(trace_id)
            st.components.v1.html(open("trace_graph.html").read(), height=800)

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
                    with trace.span("start") as span:
                        trace_id = span.get_trace_id()
                        logger.info(f"trace_id={trace_id}")
                        async for line in agent.run(prompt):
                            yield f"\n{line}\n"

                st.write_stream(markdown_generator())
    else:
        st.title("AWorld Agent Chat Assistant")
        st.info("Please select an Agent from the left sidebar to start")


try:
    agent_page("e9fefdf8904f4b82ae7ca8f6f51de564")
except Exception as e:
    logger.error(f">>> Error: {traceback.format_exc()}")
    st.error(f"Error: {str(e)}")
