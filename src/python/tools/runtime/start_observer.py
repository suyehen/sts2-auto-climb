"""Start local services and the desktop observer; history opens the default browser."""
import argparse,os,subprocess
from autoslay.paths import ROOT
from autoslay.services.client import operate

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--history',action='store_true');parser.add_argument('--both',action='store_true');args=parser.parse_args()
    operate('start', 'all')
    if not args.history or args.both:
        exe=ROOT/'src/csharp/observer/bin/Release/net9.0-windows/AutoSlay.Observer.exe'
        if not exe.exists():raise RuntimeError('Build src/csharp/observer/AutoSlay.Observer.csproj first')
        env=dict(os.environ,DOTNET_ROOT=str(ROOT/'.runtime/tools/dotnet'))
        subprocess.Popen([str(ROOT/'.runtime/tools/dotnet/dotnet.exe'),str(exe.with_suffix('.dll')),'--root='+str(ROOT)],cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    if args.history or args.both:os.startfile('http://127.0.0.1:18767/')
    print('Observer ready. Closing a view leaves services and game control unchanged.')

if __name__=='__main__':main()
