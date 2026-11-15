"""Offline tests. Run with: python -m unittest"""
import sqlite3
import unittest

import arxiv_digest as ad

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <published>2026-01-05T10:00:00Z</published>
    <title>Option Pricing under
      Rough Volatility</title>
    <summary>We study implied volatility surfaces and hedging.</summary>
    <author><name>Ada Example</name></author>
    <author><name>Bo Sample</name></author>
    <category term="q-fin.PR"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2601.00002v2</id>
    <published>2026-01-06T10:00:00Z</published>
    <title>A Note on Portfolio Theory</title>
    <summary>Nothing about the watched topics here.</summary>
    <author><name>Cy Test</name></author>
    <category term="q-fin.MF"/>
  </entry>
</feed>"""


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.papers = ad.parse_feed(SAMPLE_FEED)

    def test_parse(self):
        self.assertEqual(len(self.papers), 2)
        first = self.papers[0]
        self.assertEqual(first["id"], "2601.00001v1")
        self.assertEqual(first["title"], "Option Pricing under Rough Volatility")
        self.assertEqual(first["authors"], ["Ada Example", "Bo Sample"])
        self.assertEqual(first["published"], "2026-01-05")
        self.assertEqual(first["categories"], ["q-fin.PR"])

    def test_query_url(self):
        url = ad.build_query_url(["q-fin.TR", "q-fin.PR"], 5)
        self.assertIn("cat%3Aq-fin.TR+OR+cat%3Aq-fin.PR", url)
        self.assertIn("max_results=5", url)

    def test_required_words_are_whole_words(self):
        kept = ad.filter_required(self.papers, ["option", "options"])
        self.assertEqual([p["id"] for p in kept], ["2601.00001v1"])
        optional = dict(self.papers[1], abstract="An optional extension.")
        self.assertEqual(ad.filter_required([optional], ["option", "options"]), [])
        self.assertEqual(len(ad.filter_required(self.papers, [])), 2)

    def test_query_url_with_required_words(self):
        url = ad.build_query_url(["q-fin.PR"], 5, ["option", "options"])
        self.assertIn("%28cat%3Aq-fin.PR%29+AND+%28all%3Aoption+OR+all%3Aoptions%29", url)

    def test_score_title_counts_double(self):
        score, matched = ad.score_paper(self.papers[0], {"option": 3, "hedging": 1, "bitcoin": 2})
        self.assertEqual(score, 3 * 2 + 1)
        self.assertEqual(matched, ["option", "hedging"])

    def test_rank_puts_best_match_first(self):
        ranked = ad.rank(self.papers, top=10)
        self.assertEqual(ranked[0]["id"], "2601.00001v1")
        self.assertEqual(ranked[1]["score"], 0)

    def test_dedupe_ignores_version(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE seen (id TEXT PRIMARY KEY, title TEXT, first_seen TEXT)")
        self.assertEqual(len(ad.filter_new(conn, self.papers)), 2)
        ad.mark_seen(conn, self.papers)
        self.assertEqual(ad.filter_new(conn, self.papers), [])
        revised = dict(self.papers[1], id="2601.00002v3")
        self.assertEqual(ad.filter_new(conn, [revised]), [])
        self.assertEqual(ad.base_id("2601.00002v3"), "2601.00002")

    def test_html_escapes_and_lists_papers(self):
        ranked = ad.rank(self.papers, top=10)
        ranked[0]["title"] = "A <b> title"
        page = ad.build_html(ranked, "2026-01-06")
        self.assertIn("A &lt;b&gt; title", page)
        self.assertIn("2 papers", page)


if __name__ == "__main__":
    unittest.main()
