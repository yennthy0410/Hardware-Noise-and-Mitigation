import socket, sqlite3, subprocess
from security import install,HOME
install()
checks=0
for action in (
    lambda: socket.create_connection(('127.0.0.1',11434),timeout=.1),
    lambda: subprocess.run(['not-a-real-command']),
    lambda: (HOME.parent/'dashboard-must-not-create.txt').write_text('forbidden'),
    lambda: sqlite3.connect(HOME.parent/'dashboard-must-not-create.sqlite3'),
    lambda: __import__('qiskit_ibm_runtime'),
):
    try:action()
    except PermissionError:checks+=1
    else:raise AssertionError('Safety guard failed')
print(f'{checks}/5 isolation guards passed')
from local_ai import LOCAL_AI
token=LOCAL_AI.set(True)
try:
    for address in [('127.0.0.1',11435),('192.0.2.1',443)]:
        try:socket.create_connection(address,timeout=.1)
        except PermissionError:pass
        else:raise AssertionError('Scoped AI permission escaped fixed endpoint')
finally:LOCAL_AI.reset(token)
print('2/2 scoped AI endpoint restrictions passed')
from broker_client import BROKER_ACCESS
token=BROKER_ACCESS.set(True)
try:
    for address in [('127.0.0.1',8768),('192.0.2.1',8767),('127.0.0.1',11434)]:
        try:socket.create_connection(address,timeout=.1)
        except PermissionError:pass
        else:raise AssertionError('Broker scope escaped fixed loopback port')
    try:subprocess.run(['not-a-real-command'])
    except PermissionError:pass
    else:raise AssertionError('Broker scope allowed shell execution')
finally:BROKER_ACCESS.reset(token)
print('4/4 scoped AUTO broker restrictions passed')
