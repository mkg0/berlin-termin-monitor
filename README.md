# Berlin Termin Monitor

## What it does

Watches Berlin public-service and Doctolib appointment calendars from your
terminal. Get a sound and macOS notification when a new bookable day appears,
then open the booking page to reserve it yourself.

## Screenshots

**Berlin offices**

![Terminal dashboard showing available dates, office status, and keyboard controls](docs/screenshot.png)

**Doctolib doctors**

![Doctolib terminal dashboard showing new, known, and taken appointment times](docs/doctolib-screenshot.png)

*Dashboards rendered with sample data.*

## Install and run

Requires **macOS**, **Python 3**, and **Google Chrome**.

```sh
git clone https://github.com/mkg0/berlin-termin-monitor.git
cd berlin-termin-monitor
./start
```

The first run creates `.venv` and installs Playwright automatically. No environment
activation or `playwright install` is needed; the app uses your installed Chrome.

Choose **Berlin services** and select an appointment type and offices, or
choose **Doctolib** and paste a doctor's booking URL. Select **Start monitoring**
and keep the terminal running. Your selection is saved for next time.

**Controls:** ↑↓ select · **o** open booking page · **Space** pause/resume · **q** quit.

To start again, open a terminal in the project folder and run:

```sh
./start
```

Use `./start --help` to see command-line options.

**Doctolib time window:** only count slots that suit you, e.g. weekday evenings
within the next two weeks:

```sh
./start --between 17:00-20:00 --days 14
```

`--between` keeps slots starting inside the window (both ends included) and
`--days` ignores slots further ahead. Slots outside the filter are not shown
and never alert. Both options apply to Doctolib only.

[Development notes](docs/development.md)
