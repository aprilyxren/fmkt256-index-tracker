import unittest

from market_indices import generate_daily_news_summary


class DailyNewsSummaryTests(unittest.TestCase):
    def test_generate_daily_news_summary_prefers_real_market_news(self):
        headlines = [
            {
                "title": "US equity markets were up today owing to increased investor demand",
                "source": "example",
                "url": "https://example.com/1",
            },
            {
                "title": "Fed signals inflation remains sticky, Treasury yields rise after hotter-than-expected CPI",
                "source": "Reuters",
                "url": "https://example.com/2",
            },
        ]

        summary = generate_daily_news_summary(headlines=headlines)

        self.assertIn("Treasury yields", summary)
        self.assertIn("inflation", summary.lower())
        self.assertTrue("S&P 500" in summary or "Dow" in summary or "equities" in summary)
        self.assertTrue("after" in summary.lower() or "as" in summary.lower())
        self.assertNotIn("increased investor demand", summary.lower())
        self.assertTrue(summary.endswith("."))

    def test_generate_daily_news_summary_mentions_specific_market_impact(self):
        summary = generate_daily_news_summary([
            {
                "title": "Oil prices jump after OPEC output surprise raises supply concerns",
                "source": "Reuters",
                "url": "https://example.com/3",
            }
        ])

        self.assertIn("oil", summary.lower())
        self.assertTrue("S&P 500" in summary or "Dow" in summary or "equities" in summary)

    def test_generate_daily_news_summary_handles_no_relevant_news(self):
        summary = generate_daily_news_summary([{
            "title": "Roblox (RBLX) Faces a Growth Reset After Guidance Cuts — Is the Long-Term Story Still Intact?",
            "source": "example",
            "url": "https://example.com/4",
        }])
        self.assertNotIn("No clearly relevant market-moving news", summary)
        self.assertIn("S&P 500", summary)


if __name__ == "__main__":
    unittest.main()
