import sys
import os
sys.path.insert(0, os.path.abspath('.'))

print('Testing Grok2API Reverse Proxy Function...')

try:
    from app.core.config import config
    print('OK: Config module loaded')
    
    from app.services.reverse.app_chat import AppChatReverse, CHAT_API
    print('OK: Reverse proxy module loaded')
    print('Target API:', CHAT_API)
    
    from app.services.reverse.utils.headers import build_headers
    
    test_token = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzZXNzaW9uX2lkIjoiY2M5MjA0NDUtZGM5NS00MTk1LThjMzQtN2ZmMTM2YzA0ZTM2In0.Ue04zkAYapY2Hcji0JU6F9X5aJ68v6XLx4xDm5U0cwM'
    
    headers = build_headers(
        cookie_token=test_token,
        content_type='application/json',
        origin='https://grok.com',
        referer='https://grok.com/'
    )
    
    print('OK: Headers built successfully')
    print('Headers contain Cookie:', 'Cookie' in headers)
    print('Headers contain Origin:', 'Origin' in headers)
    print('Headers contain User-Agent:', 'User-Agent' in headers)
    
    from app.core.auth import verify_api_key
    print('OK: Auth module loaded')
    
    print('SUCCESS: All core functions tested!')
    
except Exception as e:
    print('ERROR:', str(e))
    import traceback
    traceback.print_exc()
