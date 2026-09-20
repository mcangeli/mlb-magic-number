import sys, types, unittest

statsapi = types.ModuleType("statsapi")
bullpen = types.ModuleType("bullpen")
bullpen_api = types.ModuleType("bullpen.api")
bullpen_logging = types.ModuleType("bullpen.logging")
class Dummy: pass
bullpen_api.PluginConfig = Dummy
bullpen_api.PluginData = Dummy
bullpen_api.PluginRenderer = Dummy
bullpen_api.MLBConfig = Dummy
bullpen_api.Layout = Dummy
bullpen_api.Color = Dummy
bullpen_api.PLUGIN_DEFINITION = tuple
bullpen_api.UpdateStatus = Dummy
bullpen_api.renderer = types.SimpleNamespace(graphics=object)
bullpen_logging.LOGGER = Dummy()
sys.modules.update({"statsapi": statsapi, "bullpen": bullpen, "bullpen.api": bullpen_api, "bullpen.logging": bullpen_logging})

from mlb_led_scoreboard_magic_number import TeamRecord, calculate_magic_numbers

def team(team_id, abbr, league, division, wins, losses, div_rank, wc_rank=99):
    return TeamRecord(team_id, abbr, abbr, abbr, league, division, str(division),
                      wins, losses, div_rank, wc_rank, wins / (wins + losses))

class RaceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            team(1, "ATL", 104, 204, 90, 60, 1),
            team(2, "PHI", 104, 204, 84, 66, 2, 4),
            team(3, "NYM", 104, 204, 86, 64, 3, 3),
            team(4, "MIL", 104, 205, 92, 58, 1),
            team(5, "CHC", 104, 205, 88, 62, 2, 1),
            team(6, "STL", 104, 205, 75, 75, 3, 6),
            team(7, "LAD", 104, 203, 91, 59, 1),
            team(8, "SDP", 104, 203, 87, 63, 2, 2),
            team(9, "ARI", 104, 203, 82, 68, 3, 5),
        ]

    def test_division_leader_uses_division_race(self):
        items = {x.team.abbreviation: x for x in calculate_magic_numbers(self.records)}
        atl = items["ATL"]
        self.assertEqual(atl.race, "DIV")
        self.assertEqual(atl.cutoff_team.abbreviation, "PHI")
        self.assertEqual(atl.number, 7)

    def test_non_leader_uses_wild_card_race(self):
        items = {x.team.abbreviation: x for x in calculate_magic_numbers(self.records)}
        sdp = items["SDP"]
        self.assertEqual(sdp.race, "WC")
        self.assertEqual(sdp.cutoff_team.abbreviation, "ARI")
        self.assertEqual(sdp.number, 8)

    def test_wild_card_contender_outside_field_still_shows_wc(self):
        items = {x.team.abbreviation: x for x in calculate_magic_numbers(self.records)}
        phi = items["PHI"]
        self.assertEqual(phi.race, "WC")
        self.assertEqual(phi.cutoff_team.abbreviation, "ARI")

if __name__ == "__main__":
    unittest.main()
