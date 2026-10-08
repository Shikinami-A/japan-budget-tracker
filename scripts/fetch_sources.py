"""Fetch reviewed official originals to ignored cache, never from the browser or CI."""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT=Path(__file__).resolve().parents[1]
HOSTS={'www.mof.go.jp','www.bb.mof.go.jp','www.soumu.go.jp','www.maff.go.jp',
       'www.mlit.go.jp','www.mhlw.go.jp','www.mod.go.jp','www.pref.aichi.jp',
       'www.pref.tochigi.lg.jp','www.shugiin.go.jp','www.sangiin.go.jp'}
MAX_BYTES=16*1024*1024


def checked(url):
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in (None,443):
        raise ValueError('URL is not an approved official HTTPS resource')
    return url


class CheckedRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        depth=getattr(req,'budget_redirects',0)
        if depth>=4:raise ValueError('Too many redirects')
        checked(newurl)
        result=super().redirect_request(req,fp,code,msg,headers,newurl)
        if result is not None:result.budget_redirects=depth+1
        return result


def failure_details(error):
    """Return bounded diagnostics, never proxy headers, addresses, or raw errors."""
    if isinstance(error,HTTPError):
        return {'failure_category':'http_error','http_status':error.code}
    if isinstance(error,URLError):
        # CONNECT failures are wrapped in URLError by urllib, whereas remote
        # HTTP errors use HTTPError. Do not log the exception's raw message.
        if re.match(r'^Tunnel connection failed: 403(?:\s|$)',str(error.reason)):
            return {'failure_category':'proxy_connect_denied','http_status':403}
        return {'failure_category':'connection_error'}
    return {'failure_category':'timeout' if isinstance(error,TimeoutError) else 'validation_error'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=5,help='Maximum files to request; cached URLs are skipped')
    args=parser.parse_args()
    if not 1<=args.limit<=200:parser.error('--limit must be between 1 and 200')
    cache=ROOT/'.cache'/'originals';cache.mkdir(parents=True,exist_ok=True)
    sources=json.loads((ROOT/'public/data.json').read_text())['sources']
    urls=list(dict.fromkeys(s['url'] for s in sources));opener=build_opener(CheckedRedirect())
    requested=0
    for url in urls:
        try:checked(url)
        except ValueError:continue
        key=hashlib.sha256(url.encode()).hexdigest();destination=cache/(key+'.bin')
        if destination.exists():continue
        requested+=1
        receipt={'url':url,'retrieved_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        try:
            # urllib respects inherited HTTP(S)_PROXY and uses verified TLS.
            req=Request(url,headers={'User-Agent':'japan-budget-tracker/0.1 (public official budget research)'})
            with opener.open(req,timeout=25) as response:
                size=response.headers.get('Content-Length')
                if size and int(size)>MAX_BYTES:raise ValueError('File exceeds size limit')
                content=response.read(MAX_BYTES+1)
                if len(content)>MAX_BYTES:raise ValueError('File exceeds size limit')
                receipt.update(status='downloaded',bytes=len(content),sha256_original=hashlib.sha256(content).hexdigest(),
                               final_url=checked(response.geturl()),content_type=response.headers.get_content_type())
                destination.write_bytes(content)
        except (HTTPError,URLError,TimeoutError,ValueError) as error:
            receipt.update(status='blocked_or_failed',error_type=type(error).__name__,**failure_details(error))
        with (cache/'manifest.jsonl').open('a') as f:f.write(json.dumps(receipt,ensure_ascii=False)+'\n')
        print(json.dumps({'requested':requested,'status':receipt['status'],'host':urlsplit(url).hostname,
                         **{k:receipt[k] for k in ['failure_category','http_status'] if k in receipt}},ensure_ascii=False))
        if requested>=args.limit:break


if __name__=='__main__':main()
