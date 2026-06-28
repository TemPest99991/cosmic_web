# Cosmic Ray Research Dashboard

This project creates a Streamlit web dashboard for displaying cosmic-ray detector graphs on a monitor computer.

The intended flow is:

```text
Detector computer or laptop
  -> Python processing
  -> dashboard data files
  -> Streamlit web app
  -> university display computer opens the public URL full-screen
```

## What Is Included

- Live scintillator hit rates and total hits
- Muon lifetime histogram with fit curve
- Absorption count-rate graph
- Angular-dependence graph
- Detector status, last update time, recording duration, and system information
- Sample data so the dashboard works before the detector is connected
- An uploader script for replacing or appending dashboard data

## Run Locally

Install the dependencies:

```bash
python -m pip install -r requirements.txt
```

Start the dashboard:

```bash
streamlit run app.py
```

The local page will usually open at:

```text
http://localhost:8501
```

## Create The Public Web Address

1. Put this folder in a GitHub repository.
2. Go to Streamlit Community Cloud: `https://share.streamlit.io`
3. Choose **New app**.
4. Select your GitHub repository.
5. Set the main file path to:

```text
app.py
```

6. Deploy the app.

Streamlit will create a public address like:

```text
https://your-dashboard-name.streamlit.app
```

On the university monitor computer, open that address in Chrome or Edge and press `F11` for full screen.

## Update The Graphs

The dashboard reads these files from the `data` folder:

- `live_rates.csv`
- `muon_lifetime.csv`
- `absorption_results.csv`
- `angular_results.csv`
- `system_status.json`

To append one demo live-rate update:

```bash
python uploader/upload_results.py --demo
```

To replace the live-rate graph with your own prepared CSV:

```bash
python uploader/upload_results.py --rates-csv path/to/your_rates.csv
```

Your live-rate CSV must have these columns:

```text
timestamp,scintillator,rate_per_min,total_hits
```

To generate dashboard CSVs directly from a detector text file:

```bash
python uploader/parse_detector_file.py "C:\Cosmic Ray\6_clean.txt"
```

That command updates:

- `data/live_rates.csv`
- `data/muon_lifetime.csv`
- `data/absorption_results.csv`
- `data/system_status.json`

The angular-dependence file is not changed by that converter.

## Next Detector Integration Step

Keep the website reading small processed files instead of loading a huge raw detector log. For new detector runs, use `uploader/parse_detector_file.py` to turn the raw `.txt` file into the dashboard CSV files before deploying or refreshing the app.
