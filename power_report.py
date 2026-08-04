#!/usr/bin/env python3
"""Fetch and report the last 7 days of hourly electricity demand for a US grid region.

This tool queries the U.S. Energy Information Administration (EIA) API v2 for
hourly demand data from the EIA-930 dataset (Hourly Electric Grid Monitor) and
produces a CSV, a line chart, and a printed summary.

--------------------------------------------------------------------------------
SETUP
--------------------------------------------------------------------------------
1. Get a free EIA API key (takes seconds):
       https://www.eia.gov/opendata/register.php
   The key is emailed to you immediately.

2. Create a config.py file next to this script containing your key:
       cp config.example.py config.py
   then edit config.py so it reads:
       EIA_API_KEY = "your-real-key"
   (config.py is git-ignored and must never be committed.)

3. Install dependencies (a virtual environment is recommended):
       python3 -m venv .venv
       source .venv/bin/activate
       pip install -r requirements.txt

--------------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------------
       python power_report.py

Outputs are written to ./output/:
    - demand.csv          timestamp + megawatt columns
    - demand_chart.png    weekly line chart with the peak hour marked

A summary (peak, low, daily average, weekly total) is printed to the console.

The grid region defaults to FPL (Florida Power & Light). Change RESPONDENT below
to target another EIA-930 balancing authority (e.g. "CISO", "PJM", "ERCO").
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

# EIA-930 balancing authority / respondent code to report on.
RESPONDENT = "FPL"          # Florida Power & Light
DATA_TYPE = "D"             # "D" = Demand (as opposed to net generation, etc.)
DAYS = 7                    # number of trailing days to fetch

EIA_ENDPOINT = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def load_api_key():
    """Return the EIA API key from config.py, or exit with guidance if missing."""
    try:
        from config import EIA_API_KEY
    except ImportError:
        sys.exit(
            "ERROR: config.py not found or missing EIA_API_KEY.\n"
            "  Create it with:  cp config.example.py config.py\n"
            "  then paste your free key from "
            "https://www.eia.gov/opendata/register.php"
        )
    if not EIA_API_KEY or EIA_API_KEY == "your-eia-api-key-here":
        sys.exit(
            "ERROR: EIA_API_KEY in config.py is still the placeholder.\n"
            "  Paste your real key from "
            "https://www.eia.gov/opendata/register.php"
        )
    return EIA_API_KEY


def fetch_demand(api_key):
    """Fetch hourly demand records for the configured respondent over the last DAYS.

    Returns a list of (timestamp string, value) API records. Exits with a clear
    message on any network error, HTTP error, or empty/malformed response.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS)

    params = {
        "api_key": api_key,
        "frequency": "hourly",
        "data[0]": "value",
        "facets[respondent][]": RESPONDENT,
        "facets[type][]": DATA_TYPE,
        "start": start.strftime("%Y-%m-%dT%H"),
        "end": end.strftime("%Y-%m-%dT%H"),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "offset": 0,
        "length": 5000,
    }

    try:
        resp = requests.get(EIA_ENDPOINT, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        sys.exit(f"ERROR: network request to EIA failed: {exc}")

    if resp.status_code != 200:
        # EIA returns useful error text in the body for 4xx responses.
        detail = resp.text.strip()
        sys.exit(
            f"ERROR: EIA API returned HTTP {resp.status_code}.\n  {detail[:500]}"
        )

    try:
        payload = resp.json()
    except ValueError:
        sys.exit("ERROR: EIA API response was not valid JSON.")

    # The v2 API nests real data under payload["response"]["data"]; some errors
    # arrive as payload["error"]. Guard every hop.
    if isinstance(payload, dict) and payload.get("error"):
        sys.exit(f"ERROR: EIA API error: {payload['error']}")

    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        sys.exit("ERROR: unexpected EIA response shape (no 'response' object).")

    records = response.get("data")
    if not records:
        sys.exit(
            "ERROR: EIA returned no demand records for "
            f"respondent '{RESPONDENT}' in the last {DAYS} days.\n"
            "  The respondent code may be wrong, or data may not yet be posted."
        )

    return records


def to_dataframe(records):
    """Convert raw EIA records into a clean, hourly, sorted pandas DataFrame."""
    import pandas as pd

    rows = []
    for rec in records:
        period = rec.get("period")
        value = rec.get("value")
        if period is None or value is None:
            continue  # skip incomplete rows defensively
        try:
            rows.append((pd.to_datetime(period), float(value)))
        except (ValueError, TypeError):
            continue

    if not rows:
        sys.exit("ERROR: no usable (timestamp, value) pairs in EIA response.")

    df = pd.DataFrame(rows, columns=["timestamp", "megawatts"])
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    df = df.reset_index(drop=True)
    return df


def write_csv(df, path):
    """Write the demand DataFrame to CSV with timestamp and megawatt columns."""
    df.to_csv(path, index=False)


def write_chart(df, path):
    """Render a weekly demand line chart, marking the peak hour, to `path`."""
    import matplotlib

    matplotlib.use("Agg")  # headless / no display required
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    peak_idx = df["megawatts"].idxmax()
    peak_time = df.loc[peak_idx, "timestamp"]
    peak_val = df.loc[peak_idx, "megawatts"]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df["timestamp"], df["megawatts"], color="#1f77b4", linewidth=1.5,
            label="Hourly demand")

    # Mark the peak hour.
    ax.scatter([peak_time], [peak_val], color="#d62728", zorder=5, s=60)
    ax.annotate(
        f"Peak: {peak_val:,.0f} MW\n{peak_time:%b %d %H:%M}",
        xy=(peak_time, peak_val),
        xytext=(10, -30),
        textcoords="offset points",
        color="#d62728",
        fontsize=9,
        arrowprops=dict(arrowstyle="->", color="#d62728"),
    )

    ax.set_title(
        f"{RESPONDENT} Hourly Electricity Demand — Last {DAYS} Days (EIA-930)",
        fontsize=14,
    )
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Demand (megawatts)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def print_summary(df):
    """Print peak, low, daily-average, and weekly-total demand statistics."""
    peak_idx = df["megawatts"].idxmax()
    low_idx = df["megawatts"].idxmin()
    peak_time = df.loc[peak_idx, "timestamp"]
    low_time = df.loc[low_idx, "timestamp"]
    peak_val = df.loc[peak_idx, "megawatts"]
    low_val = df.loc[low_idx, "megawatts"]

    # Average of the hourly demand values, grouped per calendar day, then meaned.
    daily_avg = df.set_index("timestamp")["megawatts"].resample("D").mean().mean()

    # Each hourly demand reading in MW held for ~1 hour => that many MWh.
    total_mwh = df["megawatts"].sum()

    n_hours = len(df)
    span_start = df["timestamp"].iloc[0]
    span_end = df["timestamp"].iloc[-1]

    print()
    print("=" * 60)
    print(f"  {RESPONDENT} ELECTRICITY DEMAND — LAST {DAYS} DAYS (EIA-930)")
    print("=" * 60)
    print(f"  Coverage        : {span_start:%Y-%m-%d %H:%M} -> "
          f"{span_end:%Y-%m-%d %H:%M} UTC ({n_hours} hourly readings)")
    print(f"  Peak demand     : {peak_val:,.0f} MW  at {peak_time:%Y-%m-%d %H:%M} UTC")
    print(f"  Lowest demand   : {low_val:,.0f} MW  at {low_time:%Y-%m-%d %H:%M} UTC")
    print(f"  Daily average   : {daily_avg:,.0f} MW")
    print(f"  Total for week  : {total_mwh:,.0f} MWh")
    print("=" * 60)


def main():
    """Fetch demand, write CSV + chart, and print the console summary."""
    api_key = load_api_key()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Fetching last {DAYS} days of hourly demand for {RESPONDENT} from EIA...")
    records = fetch_demand(api_key)
    df = to_dataframe(records)
    print(f"Retrieved {len(df)} hourly readings.")

    csv_path = os.path.join(OUTPUT_DIR, "demand.csv")
    chart_path = os.path.join(OUTPUT_DIR, "demand_chart.png")
    write_csv(df, csv_path)
    write_chart(df, chart_path)

    print(f"Wrote {csv_path}")
    print(f"Wrote {chart_path}")
    print_summary(df)


if __name__ == "__main__":
    main()
