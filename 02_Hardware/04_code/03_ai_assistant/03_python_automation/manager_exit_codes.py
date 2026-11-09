"""Stable process outcomes. Windows native exception DWORDs stay unmodified."""

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

COMPLETE=0
CLEAN_STOP=10
OPERATOR_PAUSE=20
RETRYABLE_PAUSE=21
PYTHON_FAILURE=1


def live_exit_code(result):
    state=result.get('state')
    if state=='COMPLETE':return COMPLETE
    if state=='STOPPED':return CLEAN_STOP
    if state in ('PAUSED','BLOCKED_AUTHORIZATION','BLOCKED'):
        return RETRYABLE_PAUSE if result.get('pause_class')=='PAUSED_RETRYABLE' else OPERATOR_PAUSE
    return PYTHON_FAILURE


def exit_outcome(code):
    if (code & 0xffffffff)>=0x80000000:return 'NATIVE_CRASH_OR_SIGNAL'
    return {COMPLETE:'COMPLETE_OR_OFFLINE_SUCCESS',CLEAN_STOP:'CLEAN_STOP',
            OPERATOR_PAUSE:'PAUSED_OPERATOR_ACTION',RETRYABLE_PAUSE:'PAUSED_RETRYABLE',
            PYTHON_FAILURE:'PYTHON_FAILURE',2:'CLI_OR_VALIDATION_ERROR'}.get(code,'UNEXPECTED_EXIT')
