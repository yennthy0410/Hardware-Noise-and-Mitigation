import sys
sys.path.insert(0,'02_Hardware/04_code/03_ai_assistant')
from account_config import load_saved_account_profiles,make_saved_profile_service
try:
    make_saved_profile_service(load_saved_account_profiles(('labI',))['labI'])
except Exception as e:
    print('exception',type(e).__name__)
    print('cause',type(e.__cause__).__name__)
    print('context',type(e.__context__).__name__)
    text=str(e).lower()
    print('diagnostic terms',[t for t in ('unable to resolve','instance','channel','proxy','connection','permission','account','not found','invalid url','url','verify','token') if t in text])
    # SDK validation messages contain only key names here; omit values entirely.
    print('SDK traceback locations')
    import traceback
    for f in traceback.extract_tb(e.__traceback__):print(f.name,f.lineno)
