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
    "seconds_per_team": 4
  }
}
```

Set `team` to an MLB team name, city, abbreviation, or StatsAPI-recognized lookup value. If `team` is omitted, `null`, or an empty string, the plugin automatically builds a 12-team view containing the six division leaders and the three current wild-card leaders from each league.

The renderer uses the scoreboard's existing `standings.font`, so no separate magic-number font is required.

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

The plugin calculates a practical regular-season playoff magic number from the current standings. The current fourth wild-card team is used as the playoff cutoff.

Normally:

`magic_number = 163 - target_wins - cutoff_team_losses`

If the target has already won the head-to-head season series against the cutoff, a tied final record is sufficient under MLB's tiebreak hierarchy, so the number is reduced by one:

`magic_number = 162 - target_wins - cutoff_team_losses`

If the head-to-head series is tied and the clubs are in the same division, the plugin falls back to intradivision record, matching MLB's next tiebreak criterion. A tiebreak advantage is shown as `H2H+` or `DIV+` on the display. If the target trails the relevant tiebreak, the calculation remains conservative.

MLB's current mathematical tiebreak procedure starts with head-to-head record, followed by intradivision record when applicable. citeturn0search9 The plugin does not attempt to model every possible multi-team clinching permutation or the later end-of-season tiebreak criteria, so an official MLB clinching number can differ in unusual edge cases.

## Display

The renderer is intentionally conservative for 32x32 and 64x32 boards:

```text
ATL 82-64 PLAYOFF
MAGIC       7
CUTOFF SEA 75-71 H2H
```

When the team name is too wide for the board, it is abbreviated. Automatic mode cycles through the division and wild-card leaders.

## Development

```bash
python -m unittest discover -s tests -v
python -m pip install -e .
```
