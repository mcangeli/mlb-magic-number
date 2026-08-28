# MLB-LED-Scoreboard Magic Number Plugin

A Bullpen plugin for MLB-LED-Scoreboard that displays a playoff magic number using MLB-StatsAPI standings and regular-season game results.

Compatible with the current Bullpen plugin API used by MLB-LED-Scoreboard v9.x. The plugin uses the existing `standings.font`; no `magic_number.font` entry is required. The current scoreboard documents that a plugin implements Config, Data and Renderer classes, and that `render()` is called once per frame with the current scrolling position.

## Install

```bash
sudo ./venv/bin/pip install /path/to/mlb-led-scoreboard-magic-number
```

## config.json

```json
"plugins": {
  "magic_number": {
    "team": "Braves",
    "refresh_seconds": 300,
    "seconds_per_team": 6,
    "team_abbreviation": false,
    "show_cutoff": true
  }
}
```

Leave `team` empty or omit it to cycle through the current playoff field. Add the screen to `rotation.screens`:

```json
{
  "kind": "magic_number",
  "seconds": 30,
  "with_priority": 0
}
```

The upstream project documents that plugins must be installed and then added to the `screens` configuration before they will appear.

## Magic-number calculation

The baseline is the conventional regular-season formula:

`163 - target wins - cutoff losses`

The cutoff is the current fourth wild-card team in the target's league. The plugin also reads the completed regular-season schedule and checks the target's season-series record against the cutoff. If the season series is already decided in the target's favor, a tied final record is enough and the magic number is reduced by one. A partially completed or tied season series is not assumed to favor either team.

This is intentionally conservative and is not a replacement for MLB's official clinching computation in every multi-team tie scenario.

MLB-StatsAPI exposes the standings endpoint and schedule endpoint used by the plugin.

## Scrolling

Any rendered text wider than the 64-pixel display is automatically marquee-scrolled. The renderer uses Bullpen's `scrolling_text_pos` frame position, so there is no custom render loop. This follows the Bullpen renderer contract.

## Display

The default 64x32 layout is compact:

```text
ATLANTA  82-64
----------------
PLAYOFF        M#7
CUT SEA 75-71 H2H+
```

A clinched team displays `CLINCHED` instead of a magic number.

## Tests

```bash
python -m unittest discover -s tests -v
python -m pip install -e .
```
