import unittest
from unittest.mock import patch
from analysis.scanner_news import NewsContext,article
from analysis.scanner_reference_data import Providers


class NewsTests(unittest.TestCase):
    def test_ticker_dates_and_link_validation(self):
        row={'tickers':['TEST'],'title':'Company announces results','published_utc':'2026-10-30T12:00:00Z','article_url':'javascript:bad'}
        now=1793361700
        r=article(row,'Massive','article','TEST',now)
        self.assertIsNone(r['url']);self.assertEqual(r['first_seen_at'],now)
        self.assertEqual(r['published_at'],1793361600)
        self.assertIsNone(article(row,'Massive','article','OTHER',now))
        with self.assertRaises(ValueError):article(row,'Massive','article','TEST',now-1000)

    def test_first_seen_survives_refresh_and_errors_are_not_no_news(self):
        p=Providers('fixture','fixture');n=NewsContext(p)
        row={'tickers':['TEST'],'title':'News','published_utc':'2026-10-30T12:00:00Z','article_url':'https://example.com/news'}
        def get(url,provider):
            if provider=='fmp':raise RuntimeError('Provider unavailable')
            return {'results':[row]}
        with patch.object(p,'get',side_effect=get),patch('analysis.scanner_news.time.time',return_value=1793361700):
            first=n.fetch('TEST')
        with patch.object(p,'get',side_effect=get),patch('analysis.scanner_news.time.time',return_value=1793361800):
            second=n.fetch('TEST')
        self.assertEqual(second['articles'][0]['first_seen_at'],first['articles'][0]['first_seen_at'])
        self.assertEqual(second['status'],'partial');self.assertEqual(len(second['errors']),2)
        self.assertEqual(n.reading('TEST',1793361799)['status'],'unavailable')
        self.assertTrue(n.reading('TEST',1793362000)['stale'])
        with patch.object(p,'get',side_effect=RuntimeError('All providers failed')):
            self.assertEqual(n.fetch('OTHER')['status'],'unavailable')

    def test_fmp_naive_timezone_is_disclosed(self):
        row={'symbol':'TEST','title':'Release','publishedDate':'2026-10-30 08:00:00','url':'https://example.com/release'}
        r=article(row,'FMP','press_release','TEST',1793361700)
        self.assertEqual(r['published_at'],1793361600)
        self.assertIn('interpreted',r['publication_timezone_note'])

if __name__=='__main__':unittest.main()
