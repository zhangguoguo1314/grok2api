import sys
import os
sys.path.insert(0, os.path.abspath('.'))

print('Testing Grok2API project structure...')

try:
    from app.core.config import config
    print('OK: Config module imported')
    
    # Test config loading
    print('Testing config loading...')
    print('Function enabled:', config.get('app.function_enabled'))
    
    # Test reverse proxy module
    from app.services.reverse.app_chat import AppChatReverse
    print('OK: Reverse proxy module imported')
    
    # Test auth module
    from app.core.auth import verify_api_key
    print('OK: Auth module imported')
    
    print('SUCCESS: All modules imported!')
    
except Exception as e:
    print('ERROR: Module import failed:', str(e))
    import traceback
    traceback.print_exc()
