"""Read-only platform adapters. Missing positive evidence is NOT proof of logout.

Selectors intentionally fail closed when a platform changes its UI. No CAPTCHA
solving, clicks on publish controls, or raw page/cookie logging here.
"""
import asyncio
from urllib.parse import urlparse

from services.session_store import atomic_state, cookie_path

URLS = {
    1: 'https://creator.xiaohongshu.com/new/home',
    2: 'https://channels.weixin.qq.com/platform',
    3: 'https://creator.douyin.com/creator-micro/home',
}

# Only visible, platform-specific account identity + private dashboard statistics
# together count as authenticated evidence. The page URL alone never does.
SNAPSHOT_JS = r'''pid => {
  const visible = e => !!e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const all = s => [...document.querySelectorAll(s)].filter(visible);
  const txt = e => (e?.innerText || '').trim();
  const body = document.body?.innerText || '';
  const challenge = /请完成安全验证|拖动滑块|拖动下方滑块|安全验证中|操作频繁|访问过于频繁|账号存在风险|请进行身份验证/.test(body) ||
    all('iframe[src*="captcha"], [id*="captcha"] canvas, [class*="captcha"] canvas').length > 0;
  const login = /扫码登录|扫描二维码登录|手机号登录|请先登录|微信扫码登录|登录已过期|登录已失效/.test(body);
  let name = '', avatar = '', dashboard = false;
  if (pid === 2) {
    name = txt(all('h2.finder-nickname')[0]);
    avatar = all('img[alt*="头像"]')[0]?.src || '';
    dashboard = all('.finder-content-info .finder-info-num').length >= 2;
  } else if (pid === 3) {
    for (const c of all('div[class^="container-"]')) {
      const n = c.querySelector('div[class^="name-"]');
      const a = c.querySelector(':scope > div[class^="avatar-"] > img');
      if (visible(n) && visible(a)) { name = txt(n); avatar = a.src; break; }
    }
    dashboard = all('[class*="statics-item-"]').length >= 2 && /粉丝|获赞/.test(body);
  } else if (pid === 1) {
    name = txt(all('.user-name, .user-info .name, .user-info .user-name, .author-name')[0]);
    avatar = all('.user-info img, .user-avatar img, img.avatar')[0]?.src || '';
    dashboard = all('.numerical').length >= 2 && /粉丝数|获赞与收藏/.test(body);
  }
  return {challenge, login, identity: !!name && !!avatar, dashboard, name, avatar};
}'''


def classify(platform_id, url, evidence):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != urlparse(URLS[platform_id]).hostname:
        return 'error'
    if evidence.get('challenge'):
        return 'verification_required'
    if evidence.get('login') and ('login' in parsed.path.lower() or not evidence.get('identity')):
        return 'expired'
    if evidence.get('identity') and evidence.get('dashboard') and not evidence.get('login'):
        return 'valid'
    return 'error'


async def inspect_page(page, platform_id, attempts=6):
    previous = None
    for _ in range(attempts):
        evidence = await page.evaluate(SNAPSHOT_JS, platform_id)
        state = classify(platform_id, page.url, evidence)
        # Two consistent observations avoid transient redirects / hydration.
        if state == previous and state != 'error':
            return state, evidence
        previous = state
        await asyncio.sleep(1)
    return 'error', {}


async def save_verified(context, page, platform_id, path):
    state, _ = await inspect_page(page, platform_id)
    if state != 'valid':
        raise ValueError('未取得可靠登录证据，保留原登录文件')
    atomic_state(path, await context.storage_state())


async def probe(platform, value):
    path = cookie_path(value)
    if not path.exists():
        return {'state': 'missing'}
    browser = None
    try:
        browser = await platform.create_browser(headless=True)
        context = await platform.create_context(browser, storage_state=str(path))
        page = await context.new_page()
        response = await page.goto(URLS[platform.platform_id], wait_until='domcontentloaded', timeout=30000)
        if response is None or response.status >= 400:
            return {'state': 'error'}
        state, evidence = await inspect_page(page, platform.platform_id)
        stats = []
        if state == 'valid':
            stats_fn = getattr(platform, '_login_stats_fn', None)
            if stats_fn:
                try:
                    stats = await stats_fn(page, None) or []
                except Exception:
                    pass
                # Profile collection may navigate; verify the final session again.
                state, evidence = await inspect_page(page, platform.platform_id)
        if state == 'valid':
            atomic_state(path, await context.storage_state())
        return {'state': state, 'name': evidence.get('name', ''), 'avatar': evidence.get('avatar', ''), 'stats': stats}
    except Exception:
        return {'state': 'error'}
    finally:
        if browser is not None:
            await platform.close_browser(browser)
