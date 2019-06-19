import logging

import yarl
import aiohttp
from aiohttp.web import StreamResponse, json_response

from rssant_feedlib.reader import DEFAULT_USER_AGENT, PrivateAddressError
from rssant_feedlib.async_reader import AsyncFeedReader


LOG = logging.getLogger(__name__)

PROXY_REQUEST_HEADERS = [
    'Accept', 'Accept-Encoding', 'ETag', 'If-Modified-Since', 'Cache-Control',
]

PROXY_RESPONSE_HEADERS = [
    'Transfer-Encoding', 'Cache-Control', 'ETag', 'Last-Modified', 'Expires',
]


MAX_IMAGE_SIZE = int(2 * 1024 * 1024)


class ImageProxyError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status

    def to_response(self):
        return json_response({'message': self.message}, status=self.status)


async def check_private_address(url):
    async with AsyncFeedReader() as reader:
        try:
            await reader.check_private_address(url)
        except PrivateAddressError:
            raise ImageProxyError('private address not allowed')


async def get_response(session, url, headers):
    try:
        response = await session.get(url, headers=headers)
    except (OSError, TimeoutError, IOError, aiohttp.ClientError) as ex:
        await session.close()
        raise ImageProxyError(str(ex))
    except Exception:
        await session.close()
        raise
    if yarl.URL(response.url) != yarl.URL(url):
        try:
            await check_private_address(str(response.url))
        except Exception:
            await session.close()
            raise
    return response


REFERER_DENY_STATUS = {401, 403}


async def image_proxy(request, url, referer):
    LOG.info(f'proxy image {url} referer={referer}')
    try:
        await check_private_address(url)
        headers = {'User-Agent': DEFAULT_USER_AGENT}
        for h in PROXY_REQUEST_HEADERS:
            if h in request.headers:
                headers[h] = request.headers[h]
        referer_headers = {'Referer': referer}
        referer_headers.update(headers)
        request_timeout = 30
        session = aiohttp.ClientSession(
            auto_decompress=False,
            read_timeout=request_timeout,
            conn_timeout=request_timeout,
        )
        # 先尝试发带Referer的请求，不行再尝试不带Referer
        response = await get_response(session, url, referer_headers)
        if response.status in REFERER_DENY_STATUS:
            LOG.info(f'proxy image {url} referer={referer} '
                     f'failed {response.status}, will try without referer')
            response.close()
            response = await get_response(session, response.url, headers)
    except ImageProxyError as ex:
        return ex.to_response()
    try:
        my_response = StreamResponse(status=response.status)
        # 'Content-Length', 'Content-Type', 'Transfer-Encoding'
        if response.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            my_response.enable_chunked_encoding()
        elif response.headers.get('Transfer-Encoding'):
            my_response.headers['Transfer-Encoding'] = response.headers['Transfer-Encoding']
        if response.headers.get('Content-Length'):
            content_length = int(response.headers['Content-Length'])
            if content_length > MAX_IMAGE_SIZE:
                return json_response({'message': 'image too large'}, status=413)
            my_response.content_length = content_length
        if response.headers.get('Content-Type'):
            my_response.content_type = response.headers['Content-Type']
        for h in PROXY_RESPONSE_HEADERS:
            if h in response.headers:
                my_response.headers[h] = response.headers[h]
        await my_response.prepare(request)
    except Exception:
        response.close()
        await session.close()
        raise
    try:
        content_length = 0
        async for chunk in response.content.iter_chunked(8 * 1024):
            content_length += len(chunk)
            if content_length > MAX_IMAGE_SIZE:
                LOG.warning(f'image too large, abort the response, url={url}')
                my_response.force_close()
                break
            await my_response.write(chunk)
        await my_response.write_eof()
    finally:
        response.close()
        await session.close()
    return my_response