"""Start local services and the desktop observer; history opens the default browser."""
import argparse,json,os,subprocess,time
from pathlib import Path
from urllib.request import Request,urlopen
from autoslay.paths import ROOT

def ready(url):
    try:
        with urlopen(Request(url,headers={'X-Autoslay-ReadOnly':'1'}),timeout=1) as r:return r.status==200
    except OSError:return False

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--history',action='store_true');parser.add_argument('--both',action='store_true');args=parser.parse_args()
    logs=ROOT/'.runtime/logs';logs.mkdir(parents=True,exist_ok=True)
    python=ROOT/'.runtime/conda/python.exe'
    # The resident's health route requires a token. A bound loopback socket is checked instead.
    import socket
    for port,script,url in [(18766,'execution_service.py',None),(18769,'agent_service.py',None),(18767,'decision_dashboard.py','http://127.0.0.1:18767/')]:
        with socket.socket() as s:bound=s.connect_ex(('127.0.0.1',port))==0
        if not bound:
            command=[str(python),str(ROOT/'src/python/tools/runtime'/script)]+(['--no-browser'] if port==18767 else [])
            with (logs/f'{port}.out.log').open('a') as out,(logs/f'{port}.err.log').open('a') as err:
                child=subprocess.Popen(command,cwd=ROOT,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
            local=ROOT/'.runtime/local';local.mkdir(parents=True,exist_ok=True)
            name={18766:'execution-service.pid',18769:'agent.pid',18767:'dashboard.pid'}[port]
            (local/name).write_text(str(child.pid),encoding='ascii')
        if url:
            deadline=time.monotonic()+15
            while not ready(url) and time.monotonic()<deadline:time.sleep(.2)
            if not ready(url):raise RuntimeError('Dashboard unavailable; see .runtime/logs/18767.err.log')
    if not args.history or args.both:
        exe=ROOT/'src/csharp/observer/bin/Release/net9.0-windows/AutoSlay.Observer.exe'
        if not exe.exists():raise RuntimeError('Build src/csharp/observer/AutoSlay.Observer.csproj first')
        env=dict(os.environ,DOTNET_ROOT=str(ROOT/'.runtime/tools/dotnet'))
        subprocess.Popen([str(ROOT/'.runtime/tools/dotnet/dotnet.exe'),str(exe.with_suffix('.dll')),'--root='+str(ROOT)],cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    if args.history or args.both:os.startfile('http://127.0.0.1:18767/')
    print('Observer ready. Closing a view leaves services and game control unchanged.')

if __name__=='__main__':main()
