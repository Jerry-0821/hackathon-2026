"""Entry point: `streamlit run app.py`. Each page lives in views/."""

import streamlit as st

st.set_page_config(page_title="ATC Multi-Agent Decision Support", layout="wide")

page = st.navigation({
    "Demo": [
        st.Page("views/demo.py", title="Negotiation room", icon="🛬", default=True),
        st.Page("views/evaluation.py", title="Results (40 events)", icon="📊"),
        st.Page("views/cascade.py", title="Full detail", icon="🔍"),
    ],
    "Earlier versions": [
        st.Page("views/balanced_negotiation.py", title="Balanced negotiation", icon="⚖️"),
        st.Page("views/single_flight.py", title="Single flight (v1)", icon="✈️"),
    ],
})
page.run()
