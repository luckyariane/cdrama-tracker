# Scene by Scene — C-drama tracker

A small local web app for keeping a watchlist, a reusable episode selector, character names, and a unified journal. New journal entries default to the book symbol and current episode; paper entries are unassociated with an episode and sort last. Existing plot points are converted into book entries at startup. Each drama gets a randomly assigned color theme from an eight-color palette. App data is stored in `cdrama_tracker.sqlite3` next to `tracker.py`; uploaded poster images are stored in the `posters` folder.

## Run it

1. Install Python 3.9 or newer if it is not already installed.
2. Open PowerShell in this folder (`CDramaTracker`).
3. Run `python tracker.py`.
4. Open [http://127.0.0.1:8765](http://127.0.0.1:8765) in your browser.
5. Press Ctrl+C in PowerShell to stop the app. Restarting it keeps all saved data.

The app uses only Python's standard library and SQLite; it does not need the Scrapy project dependencies. Google Fonts are an optional visual enhancement; the app remains usable if the font service is unavailable.

## Data and privacy

The app listens on this computer's network interfaces and writes its database to this folder. To open it on another device on the same home network, visit `http://<computer-ip>:8765`, replacing `<computer-ip>` with this computer's local network address. Back up `cdrama_tracker.sqlite3` and the `posters` folder to preserve or move your journal and uploaded images. Deleting a drama from the watchlist also deletes its associated journal entries and uploaded poster.
