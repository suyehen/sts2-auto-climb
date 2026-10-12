"""Launch the local read-only decision panel."""
from autoslay.dashboard.server import serve
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Open the read-only dashboard in the system default browser.')
    parser.add_argument('--no-browser',action='store_true',help='Start the service without opening a browser.')
    args=parser.parse_args()
    serve(open_browser=not args.no_browser)
