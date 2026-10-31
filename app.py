"""Entry point: `streamlit run app.py`. Each page lives in views/."""

import streamlit as st

st.set_page_config(page_title="ATC Multi-Agent Decision Support", layout="wide")

page = st.navigation([
    st.Page("views/cascade.py", title="Runway closure cascade", icon="🛬", default=True),
    st.Page("views/evaluation.py", title="Evaluation", icon="📊"),
    st.Page("views/balanced_negotiation.py", title="Balanced negotiation", icon="⚖️"),
    st.Page("views/single_flight.py", title="Single flight (v1)", icon="✈️"),
])
page.run()
