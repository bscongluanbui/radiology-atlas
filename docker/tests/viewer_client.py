"""Acquire real server viewer leases in pre-existing regression tests."""
import re
import secrets


def acquire(client, *, base_url='https://atlas.test', headers=None):
    headers = dict(headers or {})
    page = client.get('/viewer', base_url=base_url, headers=headers)
    if page.status_code == 403:
        return headers  # No grants: the original test verifies content is forbidden.
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert csrf, page.status_code
    headers.update({'X-Viewer-ID': secrets.token_hex(16), 'X-CSRF-Token': csrf.group(1)})
    response = client.post('/api/viewer-session', base_url=base_url, headers=headers, data={'action': 'acquire'})
    assert response.status_code == 200, response.text
    return headers


def release(client, headers, *, base_url='https://atlas.test'):
    client.post('/api/viewer-session', base_url=base_url, headers=headers, data={'action': 'release'})


def request_headers(test, client, path):
    """Only older content tests opt in; concurrency tests use bare clients."""
    protected = path.startswith('/data/') or (path.startswith('/api/') and path.split('?')[0] not in {'/api/catalogue', '/api/languages'})
    if not protected or not getattr(client, '_viewer_logged_in', False):
        return {}
    if not hasattr(client, '_viewer_headers'):
        client._viewer_headers = acquire(client)
        test.addCleanup(release, client, client._viewer_headers)
    return client._viewer_headers
