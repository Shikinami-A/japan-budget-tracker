"""Check that hostile source URLs cannot escape the official-host boundary."""
import importlib.util
import unittest
from pathlib import Path
from urllib.request import Request
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location(
    'fetch_sources', Path(__file__).resolve().parents[1] / 'scripts/fetch_sources.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


class FetchBoundaryTests(unittest.TestCase):
    def test_failure_diagnostics_do_not_expose_raw_proxy_details(self):
        denied=URLError(OSError('Tunnel connection failed: 403 Forbidden'))
        self.assertEqual(fetch.failure_details(denied),{'failure_category':'proxy_connect_denied','http_status':403})
        remote=HTTPError('https://www.mof.go.jp/data.pdf',403,'Forbidden',None,None)
        self.assertEqual(fetch.failure_details(remote),{'failure_category':'http_error','http_status':403})
        private=URLError('private proxy host and credentials')
        self.assertEqual(fetch.failure_details(private),{'failure_category':'connection_error'})

    def test_reject_untrusted_urls(self):
        for url in (
            'http://www.mof.go.jp/data.pdf',
            'https://www.mof.go.jp.evil.example/data.pdf',
            'https://www.mof.go.jp@evil.example/data.pdf',
            'https://user:password@www.mof.go.jp/data.pdf',
            'https://127.0.0.1/data.pdf',
            'https://www.mof.go.jp:8443/data.pdf',
            'file:///etc/passwd',
            'https://www.mof.go.jp:invalid/data.pdf',
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch.checked(url)
        self.assertEqual(fetch.checked('https://www.mof.go.jp/data.pdf'),
                         'https://www.mof.go.jp/data.pdf')

    def test_redirect_rechecks_host(self):
        request = Request('https://www.mof.go.jp/data.pdf')
        with self.assertRaises(ValueError):
            fetch.CheckedRedirect().redirect_request(
                request, None, 302, 'Found', {}, 'https://evil.example/data.pdf')
        allowed = fetch.CheckedRedirect().redirect_request(
            request, None, 302, 'Found', {}, 'https://www.mof.go.jp/new.pdf')
        self.assertEqual(allowed.budget_redirects, 1)

    def test_redirect_loop_is_bounded(self):
        request = Request('https://www.mof.go.jp/data.pdf')
        request.budget_redirects = 4
        with self.assertRaises(ValueError):
            fetch.CheckedRedirect().redirect_request(
                request, None, 302, 'Found', {}, 'https://www.mof.go.jp/loop.pdf')


if __name__ == '__main__':
    unittest.main()
