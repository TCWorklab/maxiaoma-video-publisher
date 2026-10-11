import os
from pathlib import Path

# 打包模式使用 SAU_DATA_DIR，开发模式回退到 repo/data
_data_dir = os.environ.get("SAU_DATA_DIR")
BASE_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent.parent / "data"

# 确保 data/ 及所有必要子目录在启动时就存在
for _sub in ["db", "logs", "cookies", "cookiesFile", "uploads", "thumbnails", "upload_chunks"]:
    (BASE_DIR / _sub).mkdir(parents=True, exist_ok=True)

# 登录（扫码）必须有头模式，验证/发布可用无头模式
LOCAL_CHROME_HEADLESS = True
LOGIN_HEADLESS = False

# 反馈接口为可选功能；默认不配置服务凭据。
FEEDBACK_API_BASE_URL = os.environ.get('FEEDBACK_API_BASE_URL', 'https://feedback.cjxch.com')
FEEDBACK_APP_KEY = os.environ.get('FEEDBACK_APP_KEY', '')
FEEDBACK_APP_SECRET = os.environ.get('FEEDBACK_APP_SECRET', '')
FEEDBACK_API_TIMEOUT = int(os.environ.get('FEEDBACK_API_TIMEOUT', '10'))
