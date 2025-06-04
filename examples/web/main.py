import sys
import streamlit as st
from dotenv import load_dotenv
import logging
import os
import traceback
import utils
import importlib.util
import aworld.trace as trace
from trace_net import generate_trace_graph, generate_trace_graph_full

load_dotenv(os.path.join(os.getcwd(), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.getcwd())

def agent_page(trace_id):
    st.set_page_config(
        page_title="AWorld Agent",
        page_icon=":robot_face:",
        layout="wide",
    )

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
            col1, col2 = st.columns([4, 1])
            with col1:
                generate_trace_graph(trace_id)
                st.components.v1.html(
                    open("trace_graph.html").read(), height=800)
            with col2:
                generate_trace_graph_full(trace_id)
                html_path = os.path.abspath('trace_graph_full.html')
                html_url = f"file://{html_path}"
                if st.button("全屏显示"):
                    if os.path.exists(html_path):
                        webbrowser.open_new_tab(html_url)
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
                    async with trace.span("start") as span:
                        trace_id = span.get_trace_id()
                        logger.info(f"trace_id={trace_id}")
                        async for line in agent.run(prompt):
                            yield f"\n{line}\n"

                st.write_stream(markdown_generator())
    else:
        st.title("AWorld Agent Chat Assistant")
        st.info("Please select an Agent from the left sidebar to start")


try:
    agent_page("e35df2ff9e82c41b0d2a5ed86436eb52")
except Exception as e:
    logger.error(f">>> Error: {traceback.format_exc()}")
    st.error(f"Error: {str(e)}")
