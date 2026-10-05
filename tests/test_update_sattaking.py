import datetime as dt
import unittest

from scripts.update_sattaking import (
    IST,
    chart_correction_allowed,
    find_chart_target_column,
    normalize_cell,
    parse_homepage,
    parse_month_values,
    plan_chart_jobs,
    upsert_cell,
)


class SattaKingUpdaterTests(unittest.TestCase):
    def test_normalize_cell_keeps_two_digit_values_and_rejects_placeholders(self):
        self.assertEqual(normalize_cell("<h3>00</h3>"), "00")
        self.assertEqual(normalize_cell(" 05 "), "05")
        self.assertEqual(normalize_cell("XX"), "XX")
        self.assertIsNone(normalize_cell("--"))
        self.assertIsNone(normalize_cell("Z3"))
        self.assertIsNone(normalize_cell(""))

    def test_chart_parser_selects_game_column_not_first_result_column(self):
        page = '''<div id="mix-chart"><table>
          <tr class="date-name"><td>DATE</td><th class="name">DSWR</th><th class="name">Burj Khalifa - BK</th><th class="name">FRBD</th></tr>
          <tr class="day-number"><td class="day" title="October 01, 2026">01</td><td class="number">11</td><td class="number">00</td><td class="number">42</td></tr>
          <tr class="day-number"><td class="day" title="October 02, 2026">02</td><td class="number">52</td><td class="number">05</td><td class="number">07</td></tr>
        </table></div>'''
        game = {"id": "burj-khalifa-bk", "name": "BURJ KHALIFA - BK"}
        self.assertEqual(find_chart_target_column(page, game), 2)
        values = parse_month_values(page, game, "2026-10")
        self.assertEqual(values["2026-10-01"], "00")
        self.assertEqual(values["2026-10-02"], "05")

    def test_abbreviated_regional_column_is_mapped_explicitly(self):
        page = '''<div id="mix-chart"><table>
          <tr class="date-name"><td>DATE</td><th class="name">DSWR</th><th class="name">FRBD</th></tr>
          <tr class="day-number"><td class="day" title="October 04, 2026">04</td><td class="number">15</td><td class="number">36</td></tr>
        </table></div>'''
        game = {"id": "desawar", "name": "DESAWAR"}
        self.assertEqual(find_chart_target_column(page, game), 1)
        self.assertEqual(parse_month_values(page, game, "2026-10")["2026-10-04"], "15")

    def test_regional_source_abbreviations_are_explicitly_mapped(self):
        page = '''<div id="mix-chart"><table>
          <tr class="date-name"><td>DATE</td><th class="name">DSWR</th><th class="name">SRGN</th><th class="name">FRBD</th><th class="name">GZBD</th><th class="name">GALI</th></tr>
          <tr class="day-number"><td class="day" title="October 04, 2026">04</td><td class="number">15</td><td class="number">36</td><td class="number">42</td><td class="number">51</td><td class="number">09</td></tr>
        </table></div>'''
        game = {"id": "shri-ganesh", "name": "SHRI GANESH"}
        self.assertEqual(find_chart_target_column(page, game), 2)
        self.assertEqual(parse_month_values(page, game, "2026-10")["2026-10-04"], "36")

        delhi_page = page.replace("SRGN", "DLBZ")
        delhi = {"id": "delhi-bazar", "name": "DELHI BAZAR"}
        self.assertEqual(find_chart_target_column(delhi_page, delhi), 2)
        self.assertEqual(parse_month_values(delhi_page, delhi, "2026-10")["2026-10-04"], "36")

    def test_homepage_rows_are_matched_by_validated_route_and_date(self):
        page = '''<html><body>Updated: October 05, 2026, 05:19:47 IST.
          <tr class="game-result"><td class="game-details"><h3 class="game-name">DESAWAR</h3>
          <h3 class="game-time"> at 05:00 AM</h3><h3 class="game-link"><a href="https://satta-king-fast.com/desawar/satta-result-chart/ds/">Record Chart</a></h3></td>
          <td class="yesterday-number"><h3>15</h3></td><td class="today-number"><h3>XX</h3></td></tr>
        </body></html>'''
        routes = {"desawar": "https://satta-king-fast.com/desawar/satta-result-chart/ds/"}
        page_date, values = parse_homepage(page, routes)
        self.assertEqual(page_date, dt.date(2026, 10, 5))
        self.assertEqual(values[("desawar", dt.date(2026, 10, 4))], "15")
        self.assertEqual(values[("desawar", dt.date(2026, 10, 5))], "XX")

    def test_only_numeric_correction_can_replace_verified_cell(self):
        indexed = {("desawar", "2026-10-04"): "15"}
        self.assertEqual(upsert_cell(indexed, "desawar", "2026-10-04", "XX", True), (False, False))
        self.assertEqual(upsert_cell(indexed, "desawar", "2026-10-04", None, True), (False, False))
        self.assertEqual(indexed[("desawar", "2026-10-04")], "15")
        self.assertEqual(upsert_cell(indexed, "desawar", "2026-10-04", "16", False), (False, False))
        self.assertEqual(upsert_cell(indexed, "desawar", "2026-10-04", "16", True), (True, True))
        self.assertEqual(indexed[("desawar", "2026-10-04")], "16")

    def test_pending_cell_accepts_first_numeric_value(self):
        indexed = {("gali", "2026-10-04"): "XX"}
        self.assertEqual(upsert_cell(indexed, "gali", "2026-10-04", "00", False), (True, True))
        self.assertEqual(indexed[("gali", "2026-10-04")], "00")

    def test_chart_fallback_checks_due_blank_and_xx_cells(self):
        today = dt.date(2026, 10, 5)
        yesterday = today - dt.timedelta(days=1)
        now = dt.datetime(2026, 10, 5, 12, 0, tzinfo=IST)
        game = {"id": "gali", "resultTime": "10:00 AM"}
        indexed = {("gali", f"2026-10-{day:02d}"): "11" for day in (1, 2, 3)}
        indexed[("gali", today.isoformat())] = "XX"
        home = {("gali", today): "XX", ("gali", yesterday): None}
        jobs = plan_chart_jobs([game], indexed, "2026-10", today, now, {today, yesterday}, home, True)
        self.assertEqual(jobs, {"gali": {today.isoformat(), yesterday.isoformat()}})

    def test_recent_verified_blank_or_xx_homepage_value_gets_chart_fallback(self):
        today = dt.date(2026, 10, 5)
        yesterday = today - dt.timedelta(days=1)
        now = dt.datetime(2026, 10, 5, 12, 0, tzinfo=IST)
        game = {"id": "gali", "resultTime": "10:00 AM"}
        indexed = {("gali", f"2026-10-{day:02d}"): "11" for day in (1, 2, 3)}
        indexed[("gali", today.isoformat())] = "31"
        indexed[("gali", yesterday.isoformat())] = "30"
        home = {("gali", today): None, ("gali", yesterday): "XX"}
        jobs = plan_chart_jobs([game], indexed, "2026-10", today, now, {today, yesterday}, home, True)
        self.assertEqual(jobs, {"gali": {today.isoformat(), yesterday.isoformat()}})
        self.assertTrue(chart_correction_allowed("30", yesterday.isoformat(), {today, yesterday}))
        self.assertFalse(chart_correction_allowed("29", "2026-10-03", {today, yesterday}))
        self.assertFalse(chart_correction_allowed("XX", yesterday.isoformat(), {today, yesterday}))
        self.assertEqual(upsert_cell(indexed, "gali", yesterday.isoformat(), "32", True), (True, True))
        self.assertEqual(indexed[("gali", yesterday.isoformat())], "32")

    def test_verified_recent_numeric_homepage_needs_no_chart_and_old_values_are_ignored(self):
        today = dt.date(2026, 10, 5)
        yesterday = today - dt.timedelta(days=1)
        now = dt.datetime(2026, 10, 5, 12, 0, tzinfo=IST)
        game = {"id": "gali", "resultTime": "10:00 AM"}
        indexed = {("gali", f"2026-10-{day:02d}"): "11" for day in (1, 2, 3)}
        indexed[("gali", today.isoformat())] = "31"
        indexed[("gali", yesterday.isoformat())] = "30"
        home = {("gali", today): "31", ("gali", yesterday): "30"}
        self.assertEqual(plan_chart_jobs([game], indexed, "2026-10", today, now, {today, yesterday}, home, True), {})
        self.assertEqual(plan_chart_jobs([game], indexed, "2026-10", today, now, {today, yesterday}, {}, False), {})


if __name__ == "__main__":
    unittest.main()
