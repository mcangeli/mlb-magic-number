# MLB-LED-Scoreboard Magic Number Plugin

A `bullpen` plugin for [MLB-LED-Scoreboard](https://github.com/MLB-LED-Scoreboard/mlb-led-scoreboard) that uses [MLB-StatsAPI](https://pypi.org/project/MLB-StatsAPI/) to display a team's playoff magic number.

## Install

From the scoreboard virtual environment:

```bash
sudo ./venv/bin/pip install /path/to/mlb-led-scoreboard-magic-number
```

Or directly from a Git repository after publishing it:

```bash
sudo ./venv/bin/pip install git+https://github.com/YOURUSER/mlb-led-scoreboard-magic-number.git
```

## Configuration

Add the plugin configuration:

```json
"plugins": {
  "magic_number": {
    "team": "Braves",
    "refresh_seconds": 300,
    "page_duration": 4
  }
}
```

Set `team` to an MLB team name, city, abbreviation, or StatsAPI-recognized lookup value. If `team` is omitted, `null`, or an empty string, the plugin automatically builds a 12-team view containing the six division leaders and the three current wild-card leaders from each league.

The renderer uses a font named `magic_number.font`. Add that font to the coordinates configuration using the same format as your existing plugin fonts (the example plugin in the scoreboard repository shows the pattern). `page_duration` controls how long each automatic standings page remains visible.

Add the screen to `rotation.screens`:

```json
{
  "kind": "magic_number",
  "seconds": 30,
  "with_priority": 0
}
```

For a configured team, the screen is stable. In automatic mode it rotates through the playoff leaders.

## What the number means

The plugin calculates a practical regular-season playoff magic number from the current standings:

`163 - target wins - current fourth-wild-card wins?`

More precisely, it uses the current fourth wild-card team's losses:

`magic_number = 163 - target_wins - cutoff_team_losses`

The cutoff is the best team currently outside the 12-team playoff field. A value of `0` means the team has already clinched based on the current standings; negative values are displayed as `0`.

This is the standard 162-game magic-number calculation and does not attempt to model every possible remaining-schedule/tiebreaker combination. MLB tiebreakers and games remaining can make an official clinching number differ in edge cases.

## Display

The renderer is styled after the compact 64x32 reference layout, with a cyan section header, dim divider, yellow magic number, and three-row standings pages.

For a configured team, the display is approximately:

```text
ATL (82-64)
----------------
PLAYOFF:       M# 7
CUTOFF:       SEA 75-71
```

With no team configured, automatic mode rotates through four pages:

```text
AL DIV LEADERS
----------------
ATL     82-64        M: 7
NYY     81-65        M: 8
CLE     79-67        M:10
```

followed by `NL DIV LEADERS`, `AL WILD CARD`, and `NL WILD CARD`. A magic number of `0` is rendered as `CLN`.

## Development

```bash
python -m unittest discover -s tests -v
python -m pip install -e .
```
