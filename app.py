import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import requests
from bs4 import BeautifulSoup

try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False

# ------------------ APP SETUP ------------------ #
st.title("🏋️ CrossFit & Fitness Competition Scoring App")

# ------------------ COMPETITION SOURCE ------------------ #
st.subheader("🌐 Competition Source")
use_feed = st.radio("How do you want to input scores?", ["Manual Entry", "From URL or RSS"])

comp_name = st.text_input("Competition Name", "New Competition")
athletes = []
event_names = []
saved_scores = {}

if use_feed == "From URL or RSS":
    feed_url = st.text_input("Enter Competition Leaderboard URL or RSS Feed:")
    if feed_url:
        try:
            response = requests.get(feed_url)
            soup = BeautifulSoup(response.text, "html.parser")
            # Example: scrape athletes and event headers from table
            table = soup.find("table")
            headers = [th.text.strip() for th in table.find_all("th")]
            rows = table.find_all("tr")[1:]
            event_names = headers[1:]  # assume first column is name
            athletes = []
            input_data = {event: [] for event in event_names}
            for row in rows:
                cols = row.find_all("td")
                name = cols[0].text.strip()
                athletes.append(name)
                for i, event in enumerate(event_names):
                    input_data[event].append(float(cols[i+1].text.strip()))
            df = pd.DataFrame(input_data, index=athletes)
        except Exception as e:
            st.error(f"Could not load data from URL: {e}")
            st.stop()
else:
    event_count = st.number_input("How many events?", min_value=1, max_value=20, value=7)
    event_names = [st.text_input(f"Event {i+1} Name", f"Event {i+1}") for i in range(event_count)]
    st.subheader("👥 Athlete Roster")
    athlete_list = st.text_area("Enter athlete names (one per line):", "")
    athletes = [name.strip() for name in athlete_list.split("\n") if name.strip()]

# ------------------ GOOGLE SHEETS SYNC ------------------ #
use_google_sheets = False
if GOOGLE_SHEETS_AVAILABLE:
    use_google_sheets = st.checkbox("☁️ Sync to Google Sheets")
    sheet_url = ""
    if use_google_sheets:
        sheet_url = st.text_input("Google Sheet URL (must be shared with API service account):")

# Only proceed if both athletes and events are available
if athletes and all(event_names):
    scoring_type = st.selectbox("Choose Scoring Type", ["Open", "Games", "P-Score"])
    max_events = st.slider("View results through how many events?", 1, len(event_names), len(event_names))
    selected_events = event_names[:max_events]

    DATA_FILE = f"saved_scores_{comp_name.replace(' ', '_').lower()}.json"

    def save_data(data):
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)

    def load_saved_data():
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {}

    def apply_open_scoring(df, events):
        df["Open Total"] = df[events].sum(axis=1)
        df["Open Rank"] = df["Open Total"].rank(method="min").astype(int)
        return df

    def apply_games_scoring(df, events):
        games_points = {1: 100, 2: 94, 3: 88, 4: 84, 5: 80, 6: 76, 7: 72, 8: 68, 9: 64, 10: 60}
        for event in events:
            df[event + " - Games"] = df[event].rank(method="min").map(lambda x: games_points.get(int(x), 0))
        games_cols = [e + " - Games" for e in events]
        df["Games Total"] = df[games_cols].sum(axis=1)
        df["Games Rank"] = df["Games Total"].rank(ascending=False, method="min").astype(int)
        return df

    def apply_p_scoring(df, events):
        for event in events:
            mean = df[event].mean()
            std = df[event].std()
            df[event + " - P"] = df[event].apply(lambda x: round((mean - x) / std, 2) if std != 0 else 0)
        p_cols = [e + " - P" for e in events]
        df["P-Score Total"] = df[p_cols].sum(axis=1)
        df["P-Score Rank"] = df["P-Score Total"].rank(ascending=False, method="min").astype(int)
        return df

    def projection_to_goal(df, athlete_name, goal_rank, scoring_type):
        total_col = scoring_type + " Total"
        current_score = df.loc[athlete_name, total_col]
        sorted_scores = df.sort_values(by=total_col, ascending=(scoring_type != "Games"))
        goal_score = sorted_scores.iloc[goal_rank - 1][total_col]
        gap = abs(goal_score - current_score)
        return f"{athlete_name} needs to gain {gap:.2f} points to reach rank {goal_rank} under {scoring_type} scoring."

    if use_feed != "From URL or RSS":
        saved_scores = load_saved_data()
        lock_event = st.checkbox("🔒 Lock Event Inputs to Prevent Changes")
        input_data = {}

        st.subheader("✏️ Enter or Update Event Placements")
        for event in selected_events:
            input_data[event] = []
            st.markdown(f"**{event}**")
            cols = st.columns(len(athletes))
            for i, athlete in enumerate(athletes):
                with cols[i]:
                    key = f"{event}_{athlete}"
                    default_val = saved_scores.get(event, {}).get(athlete, 1.0)
                    if lock_event:
                        placement = st.number_input(f"{athlete}", min_value=1.0, max_value=100.0, step=0.5, value=default_val, key=key, disabled=True)
                    else:
                        placement = st.number_input(f"{athlete}", min_value=1.0, max_value=100.0, step=0.5, value=default_val, key=key)
                        saved_scores.setdefault(event, {})[athlete] = placement
                    input_data[event].append(placement)
        df = pd.DataFrame(input_data, index=athletes)

    df = apply_open_scoring(df.copy(), selected_events)
    df = apply_games_scoring(df.copy(), selected_events)
    df = apply_p_scoring(df.copy(), selected_events)

    if st.button("💾 Save All Scores") and use_feed != "From URL or RSS":
        save_data(saved_scores)
        st.success("Scores saved successfully!")

        if use_google_sheets and sheet_url:
            try:
                scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
                client = gspread.authorize(creds)
                sheet = client.open_by_url(sheet_url).sheet1
                sheet.clear()
                sheet.update([df.reset_index().columns.values.tolist()] + df.reset_index().values.tolist())
                st.success("Synced to Google Sheets successfully!")
            except Exception as e:
                st.error(f"Google Sheets sync failed: {e}")

    if scoring_type == "Open":
        st.subheader("Open Scoring Leaderboard")
        leaderboard = df[["Open Total", "Open Rank"]].sort_values("Open Rank")
    elif scoring_type == "Games":
        st.subheader("Games Scoring Leaderboard")
        leaderboard = df[["Games Total", "Games Rank"]].sort_values("Games Rank")
    elif scoring_type == "P-Score":
        st.subheader("P-Scoring Leaderboard")
        leaderboard = df[["P-Score Total", "P-Score Rank"]].sort_values("P-Score Rank")

    st.dataframe(leaderboard, use_container_width=True)
    st.subheader("📋 Full Leaderboard Table")
    st.dataframe(df.sort_index(), use_container_width=True)

    st.subheader("📊 Athlete Projection")
    athlete_name = st.selectbox("Select Athlete", df.index.tolist(), key="athlete")
    goal_rank = st.slider("Goal Rank (1 = 1st place)", 1, len(df), key="goal")

    if st.button("Calculate Needed Points"):
        msg = projection_to_goal(df, athlete_name, goal_rank, scoring_type)
        st.success(msg)

    st.subheader("📤 Export Leaderboard")
    export_df = leaderboard.reset_index()
    file_name = f"leaderboard_{comp_name.replace(' ', '_').lower()}_{scoring_type.lower()}_{max_events}events.csv"
    st.download_button("Download CSV", data=export_df.to_csv(index=False), file_name=file_name, mime="text/csv")

else:
    st.warning("Please enter athlete names and all event titles to begin or provide a valid URL.")
