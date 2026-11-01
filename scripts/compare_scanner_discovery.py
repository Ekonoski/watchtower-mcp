"""Compare recorded observations, preserving incomplete coverage and timing holes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analysis.scanner_parity import compare_first_discovery

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('warrior',type=Path);parser.add_argument('watchtower',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    result=compare_first_discovery(json.loads(args.warrior.read_text(encoding='utf-8')),
                                   json.loads(args.watchtower.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
