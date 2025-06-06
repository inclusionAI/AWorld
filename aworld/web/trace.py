import logging
import traceback
import streamlit as st
import os

logger = logging.getLogger(__name__)

def view_page():
    st.set_page_config(
        page_title="HTML Viewer",
        page_icon=":robot_face:",
        layout="wide",
    )

    st.markdown(
        "<style> .stAppHeader { display: none !important;} .stMainBlockContainer { padding: 5px 10px !important; } </style>",
        unsafe_allow_html=True,
    )

    query_params = st.query_params
    trace_id = query_params.get("trace_id", None)

    side, main = st.columns([2, 8])

    with side:
        if st.button("Back To Chat"):
            st.switch_page("chat.py")

    with main:
        if trace_id:
            try:
                st.header(f"Chat Trace Graph: {trace_id}")
                folder_name = "trace_data"
                file_name = f"graph.{trace_id}.html"
                html_file_path = os.path.join(
                    os.getcwd(), folder_name, file_name
                )
                with open(html_file_path, "r") as file:
                    html_content = file.read()

                st.components.v1.html(html_content, height=600, scrolling=True)
            except Exception as e:
                logger.error(f"Error: {traceback.format_exc()}")
                st.write(f"Error: {traceback.format_exc()}")
        else:
            st.write("Parameter error!")


if __name__ == "__main__":
    view_page()
