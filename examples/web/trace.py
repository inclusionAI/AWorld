import streamlit as st
import os


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
        st.button("Back To Chat", on_click=lambda: st.switch_page("chat.py"))

    with main:
        if trace_id:
            st.header(f"Chat Trace Graph: {trace_id}")
            folder_name = "trace_data"
            file_name = f"graph.{trace_id}.html"
            html_file_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), folder_name, file_name
            )
            with open(html_file_path, "r") as file:
                html_content = file.read()

            st.components.v1.html(html_content, height=600, scrolling=True)
        else:
            st.write("Parameter error!")


if __name__ == "__main__":
    view_page()
