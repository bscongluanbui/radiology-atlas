#!/usr/bin/env python3
"""Verify anonymous GHCR access and both platforms; standard library, no credentials."""
import hashlib
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

IMAGE='bscongluanbui/radiology-atlas'
def check():
    url='https://ghcr.io/token?service=ghcr.io&scope=repository:'+IMAGE+':pull'
    with urlopen(Request(url,headers={'User-Agent':'atlas-public-check'}),timeout=30) as response:
        token=json.load(response)['token']
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'}
    with urlopen(Request('https://ghcr.io/v2/'+IMAGE+'/manifests/latest',headers=headers),timeout=30) as response:
        raw=response.read();digest=response.headers.get('Docker-Content-Digest')
    expected='sha256:'+hashlib.sha256(raw).hexdigest()
    if digest and digest!=expected:raise ValueError('Registry digest mismatch')
    index=json.loads(raw)
    platforms={(m.get('platform',{}).get('os'),m.get('platform',{}).get('architecture')) for m in index.get('manifests',[])}
    if not {('linux','amd64'),('linux','arm64')} <= platforms:raise ValueError('Multi-platform manifest missing amd64/arm64')
    print('PUBLIC_IMAGE=PASS; anonymous=yes; platforms=linux/amd64,linux/arm64; digest='+expected)

if __name__=='__main__':
    try:check()
    except (HTTPError,URLError,ValueError,KeyError) as exc:
        print('PUBLIC_IMAGE=FAIL; publish image first and set package visibility to Public; '+str(exc),file=sys.stderr)
        sys.exit(1)
